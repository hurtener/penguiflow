from __future__ import annotations

import asyncio

import pytest

from penguiflow.planner.memory import (
    ConversationTurn,
    DefaultShortTermMemory,
    MemoryBudget,
    MemoryBudgetExceeded,
    MemoryHealth,
    MemoryIsolation,
    MemoryKey,
    ShortTermMemoryConfig,
    default_token_estimator,
)


def test_memory_key_composite() -> None:
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")
    assert key.composite() == "t:u:s"


def test_default_token_estimator_minimum_one() -> None:
    assert default_token_estimator("") == 1


def test_default_token_estimator_heuristic() -> None:
    assert default_token_estimator("abcd") == 2
    assert default_token_estimator("abcdefgh") == 3


def test_memory_budget_validates_non_negative() -> None:
    with pytest.raises(ValueError, match="full_zone_turns must be >= 0"):
        MemoryBudget(full_zone_turns=-1)
    with pytest.raises(ValueError, match="summary_max_tokens must be >= 0"):
        MemoryBudget(summary_max_tokens=-1)
    with pytest.raises(ValueError, match="total_max_tokens must be >= 0"):
        MemoryBudget(total_max_tokens=-1)


def test_memory_budget_validates_summary_within_total() -> None:
    with pytest.raises(ValueError, match="summary_max_tokens must be <= total_max_tokens"):
        MemoryBudget(summary_max_tokens=11, total_max_tokens=10)


def test_short_term_memory_config_validates_strategy() -> None:
    with pytest.raises(ValueError, match="strategy must be one of"):
        ShortTermMemoryConfig(strategy="wat")  # type: ignore[arg-type]


def test_memory_isolation_defaults_fail_closed() -> None:
    iso = MemoryIsolation()
    assert iso.require_explicit_key is True
    assert iso.tenant_key == "tenant_id"
    assert iso.user_key == "user_id"
    assert iso.session_key == "session_id"


def test_memory_isolation_requires_non_empty_paths() -> None:
    with pytest.raises(ValueError, match="tenant_key must be a non-empty string"):
        MemoryIsolation(tenant_key="")
    with pytest.raises(ValueError, match="user_key must be a non-empty string"):
        MemoryIsolation(user_key="")
    with pytest.raises(ValueError, match="session_key must be a non-empty string"):
        MemoryIsolation(session_key="")


async def test_truncation_keeps_last_n_turns() -> None:
    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=2),
        )
    )
    await memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1"))
    await memory.add_turn(ConversationTurn(user_message="u2", assistant_response="a2"))
    await memory.add_turn(ConversationTurn(user_message="u3", assistant_response="a3"))

    ctx = await memory.get_llm_context()
    recent = ctx["conversation_memory"]["recent_turns"]
    assert [t["user"] for t in recent] == ["u2", "u3"]


async def test_rolling_summary_moves_oldest_to_pending_without_blocking() -> None:
    gate = asyncio.Event()

    async def summarizer(payload: dict) -> dict:
        await gate.wait()
        assert "turns" in payload
        return {"summary": "<session_summary>ok</session_summary>"}

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=2),
            retry_attempts=0,
            retry_backoff_base_s=0.0,
        ),
        summarizer=summarizer,
    )

    await asyncio.wait_for(memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1")), timeout=0.2)
    await asyncio.wait_for(memory.add_turn(ConversationTurn(user_message="u2", assistant_response="a2")), timeout=0.2)
    await asyncio.wait_for(memory.add_turn(ConversationTurn(user_message="u3", assistant_response="a3")), timeout=0.2)

    ctx = await memory.get_llm_context()
    mem = ctx["conversation_memory"]
    assert [t["user"] for t in mem["recent_turns"]] == ["u2", "u3"]
    assert [t["user"] for t in mem["pending_turns"]] == ["u1"]

    gate.set()
    await memory.flush()

    ctx2 = await memory.get_llm_context()
    mem2 = ctx2["conversation_memory"]
    assert "pending_turns" not in mem2
    assert mem2["summary"] == "<session_summary>ok</session_summary>"


async def test_summarizer_failure_degrades_and_falls_back_to_truncation() -> None:
    async def failing_summarizer(payload: dict) -> dict:
        del payload
        raise RuntimeError("boom")

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=2),
            retry_attempts=0,
            retry_backoff_base_s=0.0,
            degraded_retry_interval_s=0.0,
        ),
        summarizer=failing_summarizer,
    )

    await memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1"))
    await memory.add_turn(ConversationTurn(user_message="u2", assistant_response="a2"))
    await memory.add_turn(ConversationTurn(user_message="u3", assistant_response="a3"))
    await memory.flush()

    assert memory.health == MemoryHealth.DEGRADED
    ctx = await memory.get_llm_context()
    mem = ctx["conversation_memory"]
    assert "summary" not in mem
    assert "pending_turns" not in mem
    assert [t["user"] for t in mem["recent_turns"]] == ["u2", "u3"]


async def test_overflow_policy_error_raises() -> None:
    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(
                full_zone_turns=10,
                summary_max_tokens=0,
                total_max_tokens=1,
                overflow_policy="error",
            ),
        )
    )

    with pytest.raises(MemoryBudgetExceeded):
        await memory.add_turn(ConversationTurn(user_message="x" * 1000, assistant_response="y" * 1000))


async def test_persist_hydrate_roundtrip_duck_typed_store() -> None:
    class Store:
        def __init__(self) -> None:
            self._db: dict[str, dict] = {}

        async def save_memory_state(self, key: str, state: dict) -> None:
            self._db[key] = state

        async def load_memory_state(self, key: str) -> dict | None:
            return self._db.get(key)

    store = Store()
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s").composite()

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=2),
        )
    )
    await memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1"))
    await memory.persist(store, key)

    restored = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=2),
        )
    )
    await restored.hydrate(store, key)

    ctx = await restored.get_llm_context()
    assert [t["user"] for t in ctx["conversation_memory"]["recent_turns"]] == ["u1"]


async def test_persist_hydrate_noop_when_store_missing_methods() -> None:
    class Store:
        pass

    memory = DefaultShortTermMemory(config=ShortTermMemoryConfig(strategy="truncation"))
    await memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1"))
    await memory.persist(Store(), "k")

    other = DefaultShortTermMemory(config=ShortTermMemoryConfig(strategy="truncation"))
    await other.hydrate(Store(), "k")
    ctx = await other.get_llm_context()
    assert ctx["conversation_memory"]["recent_turns"] == []


# ---------------------------------------------------------------------------
# Regression tests for issue 128 (rolling_summary persistence, concurrency, budgets)
# ---------------------------------------------------------------------------


def _turn(user: str, assistant: str = "a") -> ConversationTurn:
    return ConversationTurn(user_message=user, assistant_response=assistant)


class _RecordingSummarizer:
    """Summarizer that records each request; optionally blocks until released."""

    def __init__(self, *, gated: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()

    async def __call__(self, payload: dict) -> dict:
        self.calls.append([t["user"] for t in payload["turns"]])
        await self.gate.wait()
        return {"summary": f"summary#{len(self.calls)}:" + ",".join(self.calls[-1])}


class _CountingStore:
    """Duck-typed memory store that keeps every saved state."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.saves: list[tuple[str, dict]] = []

    async def save_memory_state(self, key: str, state: dict) -> None:
        self.data[key] = dict(state)
        self.saves.append((key, dict(state)))

    async def load_memory_state(self, key: str) -> dict | None:
        state = self.data.get(key)
        return dict(state) if state is not None else None


def _rolling(summarizer: object, **budget: object) -> DefaultShortTermMemory:
    return DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(**budget),  # type: ignore[arg-type]
            retry_attempts=0,
            retry_backoff_base_s=0.0,
        ),
        summarizer=summarizer,  # type: ignore[arg-type]
    )


async def test_rolling_summary_survives_hydrate_between_runs() -> None:
    """Bug 1: with a store attached, the summary must survive the per-run hydrate."""
    from penguiflow.state import InMemoryStateStore

    summarizer = _RecordingSummarizer()
    memory = _rolling(summarizer, full_zone_turns=1)
    store = InMemoryStateStore()
    seen: list[tuple[str | None, int]] = []
    for i in range(5):
        await memory.hydrate(store, "t:u:s")  # ReactPlanner: start of every run
        ctx = (await memory.get_llm_context())["conversation_memory"]
        seen.append((ctx.get("summary"), len(ctx.get("pending_turns", []))))
        await memory.add_turn(_turn(f"u{i}"))
        await memory.persist(store, "t:u:s")  # ReactPlanner: right after the turn
        await memory.flush()  # the next message arrives after the summarizer returned

    assert seen[0] == (None, 0)
    assert seen[1] == (None, 0)
    for summary, pending in seen[2:]:
        assert summary is not None
        assert pending == 0
    # Each evicted turn is summarized exactly once.
    assert summarizer.calls == [["u0"], ["u1"], ["u2"], ["u3"]]

    # The store itself holds the summary (a restart or another worker sees it).
    restored = _rolling(_RecordingSummarizer(), full_zone_turns=1)
    await restored.hydrate(store, "t:u:s")
    restored_ctx = (await restored.get_llm_context())["conversation_memory"]
    assert restored_ctx["summary"] == "summary#4:u3"
    assert [t["user"] for t in restored_ctx["recent_turns"]] == ["u4"]


async def test_hydrate_during_inflight_summary_keeps_the_result() -> None:
    """Bug 1: a fast next message (hydrate mid-summarization) must not discard the summary."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=1)
    store = _CountingStore()

    await memory.hydrate(store, "k")
    await memory.add_turn(_turn("u0"))
    await memory.add_turn(_turn("u1"))  # evicts u0 -> summarization starts and blocks
    await memory.persist(store, "k")
    await asyncio.sleep(0)

    await memory.hydrate(store, "k")  # store holds our own revision -> skipped

    summarizer.gate.set()
    await memory.flush()

    assert summarizer.calls == [["u0"]]
    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert ctx["summary"] == "summary#1:u0"
    assert "pending_turns" not in ctx
    assert store.data["k"]["summary"] == "summary#1:u0"
    assert store.data["k"]["pending"] == []


async def test_hydrate_with_newer_state_resummarizes_current_state() -> None:
    """Bug 1: if hydrate loads newer state mid-call, the stale result is dropped."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=1)
    store = _CountingStore()

    await memory.hydrate(store, "k")
    await memory.add_turn(_turn("u0"))
    await memory.add_turn(_turn("u1"))
    await memory.persist(store, "k")
    await asyncio.sleep(0)
    assert summarizer.calls == [["u0"]]

    # Another worker writes a newer state with a different pending turn.
    other = _rolling(_RecordingSummarizer(gated=True), full_zone_turns=1)
    other.from_dict(
        {
            "version": 1,
            "health": "healthy",
            "summary": "",
            "turns": [{"user_message": "w1", "assistant_response": "a"}],
            "pending": [{"user_message": "w0", "assistant_response": "a"}],
            "backlog": [],
        }
    )
    await other.persist(store, "k")
    await memory.hydrate(store, "k")  # newer revision -> applied

    summarizer.gate.set()
    await memory.flush()

    assert summarizer.calls == [["u0"], ["w0"]]
    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert ctx["summary"] == "summary#2:w0"
    assert [t["user"] for t in ctx["recent_turns"]] == ["w1"]


async def test_background_persist_skipped_when_store_has_newer_revision() -> None:
    """Bug 1: a background write must not overwrite a newer write from another worker."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=1)
    store = _CountingStore()

    await memory.hydrate(store, "k")
    await memory.add_turn(_turn("u0"))
    await memory.add_turn(_turn("u1"))
    await memory.persist(store, "k")
    await asyncio.sleep(0)

    newer = dict(store.data["k"])
    newer["revision"] = "written-by-another-worker"
    store.data["k"] = newer

    summarizer.gate.set()
    await memory.flush()

    assert store.data["k"]["revision"] == "written-by-another-worker"
    assert store.data["k"]["summary"] == ""


async def test_background_persist_goes_to_the_key_it_started_under() -> None:
    """Bug 1: a memory shared across keys never writes one key's result under another."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=1)
    store = _CountingStore()

    seeded = _rolling(_RecordingSummarizer(), full_zone_turns=1)
    seeded.from_dict(
        {
            "version": 1,
            "health": "healthy",
            "summary": "",
            "turns": [{"user_message": "b1", "assistant_response": "a"}],
            "pending": [{"user_message": "b0", "assistant_response": "a"}],
            "backlog": [],
        }
    )
    await seeded.persist(store, "B")

    await memory.hydrate(store, "A")
    await memory.add_turn(_turn("a0"))
    await memory.add_turn(_turn("a1"))  # summarization of a0 starts under key A
    await memory.persist(store, "A")
    await asyncio.sleep(0)
    a_state_before = dict(store.data["A"])

    await memory.hydrate(store, "B")  # same memory object now serves key B

    summarizer.gate.set()
    await memory.flush()

    # The stale A result was dropped; B's state was summarized and written to B only.
    assert summarizer.calls == [["a0"], ["b0"]]
    assert store.data["A"] == a_state_before
    assert store.data["B"]["summary"] == "summary#2:b0"


async def test_turns_arriving_during_summarization_are_kept() -> None:
    """Bug 2: only the turns that were summarized leave the pending buffer."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=2)

    await memory.add_turn(_turn("u1"))
    await memory.add_turn(_turn("u2"))
    await memory.add_turn(_turn("u3"))  # u1 -> pending, summarization blocks
    await asyncio.sleep(0)
    await memory.add_turn(_turn("u4"))  # u2 -> pending while the call runs

    summarizer.gate.set()
    await memory.flush()

    assert summarizer.calls == [["u1"], ["u2"]]
    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert "pending_turns" not in ctx
    assert ctx["summary"] == "summary#2:u2"
    assert [t["user"] for t in ctx["recent_turns"]] == ["u3", "u4"]


async def test_identical_turns_are_removed_by_identity_not_value() -> None:
    """Bug 2: two turns with the same text are still distinct turns."""
    summarizer = _RecordingSummarizer(gated=True)
    memory = _rolling(summarizer, full_zone_turns=1)

    await memory.add_turn(_turn("same"))
    await memory.add_turn(_turn("same"))  # first "same" -> pending, call blocks
    await asyncio.sleep(0)
    await memory.add_turn(_turn("last"))  # second "same" -> pending

    summarizer.gate.set()
    await memory.flush()

    assert summarizer.calls == [["same"], ["same"]]


class _BoundedEstimator:
    """Default estimator that fails instead of letting a runaway loop hang the test."""

    def __init__(self, limit: int = 500) -> None:
        self.calls = 0
        self.limit = limit

    def __call__(self, text: str) -> int:
        self.calls += 1
        if self.calls > self.limit:
            raise AssertionError("token estimator called too often: budget loop does not terminate")
        return default_token_estimator(text)


def _truncate_summary_memory(
    *, summary: str, health: str = "healthy", summary_max_tokens: int, total_max_tokens: int
) -> DefaultShortTermMemory:
    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(
                full_zone_turns=2,
                summary_max_tokens=summary_max_tokens,
                total_max_tokens=total_max_tokens,
                overflow_policy="truncate_summary",
            ),
            token_estimator=_BoundedEstimator(),
            retry_attempts=0,
            degraded_retry_interval_s=3600.0,
        ),
        summarizer=_RecordingSummarizer(),
    )
    memory.from_dict(
        {"version": 1, "health": health, "summary": summary, "turns": [], "pending": [], "backlog": []}
    )
    return memory


async def test_truncate_summary_rfc_repro_does_not_hang() -> None:
    """Bug 3: the RFC repro (summary fits its budget, payload over total) terminates."""

    async def summarize(request: dict) -> dict:
        return {"summary": "short"}

    budget = MemoryBudget(
        full_zone_turns=1, summary_max_tokens=50, total_max_tokens=100, overflow_policy="truncate_summary"
    )
    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(strategy="rolling_summary", budget=budget, token_estimator=_BoundedEstimator()),
        summarizer=summarize,
    )
    await memory.add_turn(_turn("u0", "a0"))
    await memory.add_turn(_turn("u1", "a1"))
    await memory.flush()
    await memory.add_turn(_turn("u2", "x" * 2000))
    assert memory.estimate_tokens() <= 100


async def test_truncate_summary_shrinks_summary_before_dropping_turns() -> None:
    """Bug 3: a small overflow trims the oldest part of the summary and keeps the turns."""
    original = "".join(f"{i:04d}" for i in range(200))  # 800 chars, ~201 tokens
    memory = _truncate_summary_memory(summary=original, summary_max_tokens=250, total_max_tokens=300)

    await memory.add_turn(_turn("q" * 300, "r" * 300))

    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert 0 < len(ctx["summary"]) < len(original)
    assert original.endswith(ctx["summary"])
    assert [t["user"] for t in ctx["recent_turns"]] == ["q" * 300]
    assert memory.estimate_tokens() <= 300


async def test_truncate_summary_clears_summary_then_drops_oldest_turns() -> None:
    """Bug 3: when the summary isn't enough, it is cleared and then the oldest turns go."""
    memory = _truncate_summary_memory(summary="s" * 80, summary_max_tokens=50, total_max_tokens=100)

    await memory.add_turn(_turn("first" * 50))
    await memory.add_turn(_turn("second" * 50))

    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert "summary" not in ctx
    assert [t["user"] for t in ctx["recent_turns"]] == ["second" * 50]


async def test_truncate_summary_keeps_summary_while_degraded() -> None:
    """Bug 3: a degraded memory doesn't send its summary, so it is kept for recovery."""
    original = "s" * 800
    memory = _truncate_summary_memory(
        summary=original, health="degraded", summary_max_tokens=250, total_max_tokens=300
    )

    await memory.add_turn(_turn("q" * 600))
    await memory.add_turn(_turn("r" * 600))

    assert memory.health == MemoryHealth.DEGRADED
    assert memory.to_dict()["summary"] == original
    assert memory.estimate_tokens() <= 300


async def test_summarizer_failures_are_retried_in_background() -> None:
    """Item 10: a failing summarizer is retried with backoff without another add_turn."""
    attempts = 0
    succeeded = asyncio.Event()

    async def flaky(payload: dict) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient")
        succeeded.set()
        return {"summary": "recovered"}

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=1),
            retry_attempts=3,
            retry_backoff_base_s=0.0,
        ),
        summarizer=flaky,
    )
    await memory.add_turn(_turn("u0"))
    await memory.add_turn(_turn("u1"))

    await asyncio.wait_for(succeeded.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert attempts == 3
    assert memory.health == MemoryHealth.HEALTHY
    ctx = (await memory.get_llm_context())["conversation_memory"]
    assert ctx["summary"] == "recovered"
    assert "pending_turns" not in ctx


async def test_summarizer_degrades_after_exhausting_retries() -> None:
    """Item 10: after retry_attempts failures the memory degrades and keeps a backlog."""
    attempts = 0

    async def failing(payload: dict) -> dict:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("down")

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=1),
            retry_attempts=2,
            retry_backoff_base_s=0.0,
            degraded_retry_interval_s=3600.0,
        ),
        summarizer=failing,
    )
    await memory.add_turn(_turn("u0"))
    await memory.add_turn(_turn("u1"))
    await memory.flush()

    assert attempts == 3
    assert memory.health == MemoryHealth.DEGRADED
    assert [t["user_message"] for t in memory.to_dict()["backlog"]] == ["u0"]


def _state(health: str, *, backlog: list[str] = (), pending: list[str] = ()) -> dict:  # type: ignore[assignment]
    return {
        "version": 1,
        "health": health,
        "summary": "",
        "turns": [],
        "pending": [{"user_message": u, "assistant_response": "a"} for u in pending],
        "backlog": [{"user_message": u, "assistant_response": "a"} for u in backlog],
    }


async def test_restored_recovering_state_recovers_its_backlog() -> None:
    """Item 11: a state saved mid-recovery is restored as degraded and then recovers."""
    recovered = asyncio.Event()

    async def summarize(payload: dict) -> dict:
        recovered.set()
        return {"summary": "from-backlog:" + ",".join(t["user"] for t in payload["turns"])}

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=5),
            degraded_retry_interval_s=0.0,
        ),
        summarizer=summarize,
    )
    memory.from_dict(_state("recovering", backlog=["b0", "b1"]))
    assert memory.health == MemoryHealth.DEGRADED

    await memory.add_turn(_turn("u0"))  # degraded path schedules recovery
    await asyncio.wait_for(recovered.wait(), timeout=1.0)
    await asyncio.sleep(0)

    assert memory.health == MemoryHealth.HEALTHY
    assert memory.to_dict()["backlog"] == []
    assert memory.to_dict()["summary"] == "from-backlog:b0,b1"


async def test_restored_transient_health_states_are_normalised() -> None:
    """Item 11: retry and an empty recovering state restore as healthy."""
    memory = _rolling(_RecordingSummarizer(), full_zone_turns=2)
    memory.from_dict(_state("retry", pending=["p0"]))
    assert memory.health == MemoryHealth.HEALTHY

    memory.from_dict(_state("recovering"))
    assert memory.health == MemoryHealth.HEALTHY


async def test_failed_recovery_stays_degraded() -> None:
    """Item 11: a failed recovery keeps the backlog and stays degraded (not retry)."""
    attempted = asyncio.Event()

    async def failing(payload: dict) -> dict:
        attempted.set()
        raise RuntimeError("still down")

    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=5),
            retry_attempts=3,
            degraded_retry_interval_s=0.0,
        ),
        summarizer=failing,
    )
    memory.from_dict(_state("degraded", backlog=["b0"]))

    await memory.add_turn(_turn("u0"))
    await asyncio.wait_for(attempted.wait(), timeout=1.0)
    for _ in range(3):
        await asyncio.sleep(0)

    assert memory.health == MemoryHealth.DEGRADED
    assert [t["user_message"] for t in memory.to_dict()["backlog"]] == ["b0"]
