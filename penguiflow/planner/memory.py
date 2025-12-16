"""Short-term memory types, protocols, and a default implementation for ReactPlanner.

Memory is intentionally opt-in and designed to be safe-by-default for multi-tenant
deployments via explicit session keys (see :class:`MemoryKey` and :class:`MemoryIsolation`).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol


class MemoryBudgetExceeded(RuntimeError):
    """Raised when memory exceeds configured token budgets and overflow_policy="error"."""


class MemoryHealth(str, Enum):
    """Health states for the summarization subsystem."""

    HEALTHY = "healthy"
    RETRY = "retry"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


@dataclass(slots=True)
class TrajectoryDigest:
    """Compressed trajectory representation for memory persistence."""

    tools_invoked: list[str]
    observations_summary: str
    reasoning_summary: str | None = None
    artifacts_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationTurn:
    """Atomic user-assistant exchange stored in short-term memory."""

    user_message: str
    assistant_response: str
    trajectory_digest: TrajectoryDigest | None = None

    artifacts_shown: dict[str, Any] = field(default_factory=dict)
    artifacts_hidden_refs: list[str] = field(default_factory=list)

    ts: float = 0.0


@dataclass(slots=True)
class MemoryBudget:
    """Token economy configuration for memory management."""

    full_zone_turns: int = 5
    summary_max_tokens: int = 1000
    total_max_tokens: int = 10000
    overflow_policy: Literal["truncate_summary", "truncate_oldest", "error"] = "truncate_oldest"

    def __post_init__(self) -> None:
        if self.full_zone_turns < 0:
            raise ValueError("full_zone_turns must be >= 0")
        if self.summary_max_tokens < 0:
            raise ValueError("summary_max_tokens must be >= 0")
        if self.total_max_tokens < 0:
            raise ValueError("total_max_tokens must be >= 0")
        if self.total_max_tokens and self.summary_max_tokens > self.total_max_tokens:
            raise ValueError("summary_max_tokens must be <= total_max_tokens")
        if self.overflow_policy not in {"truncate_summary", "truncate_oldest", "error"}:
            raise ValueError("overflow_policy must be one of: truncate_summary, truncate_oldest, error")


@dataclass(slots=True)
class MemoryIsolation:
    """Session isolation configuration to prevent context leakage."""

    tenant_key: str = "tenant_id"
    user_key: str = "user_id"
    session_key: str = "session_id"

    require_explicit_key: bool = True

    def __post_init__(self) -> None:
        if not self.tenant_key:
            raise ValueError("tenant_key must be a non-empty string")
        if not self.user_key:
            raise ValueError("user_key must be a non-empty string")
        if not self.session_key:
            raise ValueError("session_key must be a non-empty string")


@dataclass(slots=True)
class MemoryKey:
    """Composite key for memory isolation."""

    tenant_id: str
    user_id: str
    session_id: str

    def composite(self) -> str:
        return f"{self.tenant_id}:{self.user_id}:{self.session_id}"


def default_token_estimator(text: str) -> int:
    """Estimate token count using a conservative character heuristic."""

    return len(text) // 4 + 1


@dataclass(slots=True)
class ShortTermMemoryConfig:
    """Configuration for opt-in short-term memory."""

    strategy: Literal["truncation", "rolling_summary", "none"] = "none"
    budget: MemoryBudget = field(default_factory=MemoryBudget)
    isolation: MemoryIsolation = field(default_factory=MemoryIsolation)

    summarizer_model: str | None = None
    include_trajectory_digest: bool = True

    recovery_backlog_limit: int = 20
    retry_attempts: int = 3
    retry_backoff_base_s: float = 2.0
    degraded_retry_interval_s: float = 30.0

    token_estimator: Callable[[str], int] | None = None

    on_turn_added: Callable[[ConversationTurn], Awaitable[None]] | None = None
    on_summary_updated: Callable[[str, str], Awaitable[None]] | None = None
    on_health_changed: Callable[[MemoryHealth, MemoryHealth], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if self.strategy not in {"truncation", "rolling_summary", "none"}:
            raise ValueError("strategy must be one of: truncation, rolling_summary, none")
        if self.recovery_backlog_limit < 0:
            raise ValueError("recovery_backlog_limit must be >= 0")
        if self.retry_attempts < 0:
            raise ValueError("retry_attempts must be >= 0")
        if self.retry_backoff_base_s < 0:
            raise ValueError("retry_backoff_base_s must be >= 0")
        if self.degraded_retry_interval_s < 0:
            raise ValueError("degraded_retry_interval_s must be >= 0")


class ShortTermMemory(Protocol):
    """Minimal required protocol for short-term memory implementations."""

    @property
    def health(self) -> MemoryHealth:  # pragma: no cover - protocol
        ...

    async def add_turn(self, turn: ConversationTurn) -> None:  # pragma: no cover - protocol
        ...

    async def get_llm_context(self) -> Mapping[str, Any]:  # pragma: no cover - protocol
        ...

    def estimate_tokens(self) -> int:  # pragma: no cover - protocol
        ...

    async def flush(self) -> None:  # pragma: no cover - protocol
        ...


class ShortTermMemoryPersistence(Protocol):
    """Optional persistence extension, intentionally duck-typed against the store object."""

    async def persist(self, store: Any, key: str) -> None:  # pragma: no cover - protocol
        ...

    async def hydrate(self, store: Any, key: str) -> None:  # pragma: no cover - protocol
        ...


class ShortTermMemorySerializable(Protocol):
    """Optional serialization extension for custom persistence backends."""

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - protocol
        ...

    def from_dict(self, state: Mapping[str, Any]) -> None:  # pragma: no cover - protocol
        ...


class ShortTermMemoryArtifacts(Protocol):
    """Optional artifact access extension."""

    def get_artifact(self, ref: str) -> Any | None:  # pragma: no cover - protocol
        ...


def _safe_create_task(coro: Coroutine[Any, Any, None]) -> None:
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        return


def _turn_to_llm_dict(turn: ConversationTurn, *, include_digest: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"user": turn.user_message, "assistant": turn.assistant_response}
    if include_digest and turn.trajectory_digest is not None:
        payload["trajectory_digest"] = {
            "tools_invoked": list(turn.trajectory_digest.tools_invoked),
            "observations_summary": turn.trajectory_digest.observations_summary,
            "reasoning_summary": turn.trajectory_digest.reasoning_summary,
            "artifacts_refs": list(turn.trajectory_digest.artifacts_refs),
        }
    return payload


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


async def _default_summarizer(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    del payload
    raise RuntimeError(
        "DefaultShortTermMemory requires an explicit summarizer callable for rolling summaries. "
        "When using ReactPlanner, it wires an LLM-backed summarizer automatically."
    )


class DefaultShortTermMemory:
    """In-memory short-term memory with optional background summarization."""

    def __init__(
        self,
        *,
        config: ShortTermMemoryConfig,
        summarizer: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        """Create an in-memory short-term memory instance.

        Args:
            config: Memory strategy, budgets, isolation, callbacks, and retry settings.
            summarizer: Async function that receives a request payload and returns
                a mapping containing a string key `"summary"`. Required for
                `strategy="rolling_summary"`.
            time_source: Optional time provider used for retries and health timing.
        """
        if config.strategy == "rolling_summary" and summarizer is None:
            raise ValueError("rolling_summary requires a summarizer callable")
        self._config = config
        self._summarizer = summarizer or _default_summarizer
        self._time_source = time_source or time.monotonic

        self._health: MemoryHealth = MemoryHealth.HEALTHY
        self._summary: str = ""
        self._pending: list[ConversationTurn] = []
        self._turns: list[ConversationTurn] = []
        self._backlog: list[ConversationTurn] = []

        self._lock = asyncio.Lock()
        self._summarize_task: asyncio.Task[None] | None = None
        self._retry_count = 0
        self._last_degraded_attempt_ts = 0.0

    @property
    def health(self) -> MemoryHealth:
        """Return the current summarization health state."""
        return self._health

    async def add_turn(self, turn: ConversationTurn) -> None:
        """Add a completed user-assistant turn to memory.

        Notes:
            - For `strategy="truncation"`, memory is immediately truncated to the
              configured `full_zone_turns` and budgets are enforced.
            - For `strategy="rolling_summary"`, older turns are evicted into a
              pending buffer and summarized in the background.
            - If summarization is degraded, the memory falls back to keeping only
              recent turns and buffers a limited backlog for later recovery.
        """
        if self._config.strategy == "none":
            return

        async with self._lock:
            self._turns.append(turn)
            await self._maybe_fire_on_turn_added(turn)

            if self._config.strategy == "truncation":
                self._truncate_to_full_zone_locked()
                self._enforce_budget_locked()
                return

            if self._config.strategy != "rolling_summary":
                return

            if self._health == MemoryHealth.DEGRADED:
                self._truncate_to_full_zone_locked(store_backlog=True)
                self._enforce_budget_locked()
                self._maybe_schedule_degraded_recovery_locked()
                return

            self._evict_to_pending_locked()
            self._enforce_budget_locked()
            self._maybe_schedule_summarize_locked()

    async def get_llm_context(self) -> Mapping[str, Any]:
        """Return a JSON-serialisable patch to be merged into `llm_context`.

        Returns:
            A mapping containing a single key `conversation_memory` when memory is
            enabled, otherwise an empty mapping.

        Shape:
            - `conversation_memory.recent_turns`: list of `{user, assistant, ...}`
            - `conversation_memory.summary`: optional string (rolling_summary only)
            - `conversation_memory.pending_turns`: optional list (rolling_summary only)
        """
        if self._config.strategy == "none":
            return {}

        async with self._lock:
            include_digest = self._config.include_trajectory_digest
            recent_turns = [_turn_to_llm_dict(t, include_digest=include_digest) for t in self._turns]

            if self._config.strategy == "truncation":
                return {"conversation_memory": {"recent_turns": recent_turns}}

            if self._config.strategy != "rolling_summary":
                return {}

            if self._health == MemoryHealth.DEGRADED:
                return {"conversation_memory": {"recent_turns": recent_turns}}

            pending_turns = [_turn_to_llm_dict(t, include_digest=include_digest) for t in self._pending]
            payload: dict[str, Any] = {"recent_turns": recent_turns}
            if pending_turns:
                payload["pending_turns"] = pending_turns
            if self._summary:
                payload["summary"] = self._summary
            return {"conversation_memory": payload}

    def estimate_tokens(self) -> int:
        """Estimate the token size of the memory payload using the configured estimator."""
        estimator = self._config.token_estimator or default_token_estimator
        data = self._estimate_payload_snapshot()
        return estimator(_compact_json(data))

    async def flush(self) -> None:
        """Wait for any in-flight summarization to finish.

        This is best-effort and returns immediately for non-rolling strategies or
        when the summarizer is degraded.
        """
        while True:
            task = None
            async with self._lock:
                task = self._summarize_task
            if task is not None:
                try:
                    await task
                finally:
                    async with self._lock:
                        if self._summarize_task is task:
                            self._summarize_task = None

            async with self._lock:
                if self._config.strategy != "rolling_summary":
                    return
                if self._health == MemoryHealth.DEGRADED:
                    return
                if not self._pending:
                    return
                if self._summarize_task is None:
                    self._maybe_schedule_summarize_locked()

    async def persist(self, store: Any, key: str) -> None:
        """Persist memory state to a store implementing `save_memory_state(key, state)`."""
        if not hasattr(store, "save_memory_state"):
            return
        state = self.to_dict()
        await store.save_memory_state(key, state)

    async def hydrate(self, store: Any, key: str) -> None:
        """Hydrate memory state from a store implementing `load_memory_state(key)`."""
        if not hasattr(store, "load_memory_state"):
            return
        state = await store.load_memory_state(key)
        if state is None:
            return
        self.from_dict(state)

    def to_dict(self) -> dict[str, Any]:
        """Serialise memory state to a JSON-friendly dictionary."""
        config = self._config
        snapshot = {
            "strategy": config.strategy,
            "budget": {
                "full_zone_turns": config.budget.full_zone_turns,
                "summary_max_tokens": config.budget.summary_max_tokens,
                "total_max_tokens": config.budget.total_max_tokens,
                "overflow_policy": config.budget.overflow_policy,
            },
            "isolation": {
                "tenant_key": config.isolation.tenant_key,
                "user_key": config.isolation.user_key,
                "session_key": config.isolation.session_key,
                "require_explicit_key": config.isolation.require_explicit_key,
            },
            "summarizer_model": config.summarizer_model,
            "include_trajectory_digest": config.include_trajectory_digest,
            "recovery_backlog_limit": config.recovery_backlog_limit,
            "retry_attempts": config.retry_attempts,
        }

        def _dump_turn(turn: ConversationTurn) -> dict[str, Any]:
            return {
                "user_message": turn.user_message,
                "assistant_response": turn.assistant_response,
                "trajectory_digest": (
                    {
                        "tools_invoked": list(turn.trajectory_digest.tools_invoked),
                        "observations_summary": turn.trajectory_digest.observations_summary,
                        "reasoning_summary": turn.trajectory_digest.reasoning_summary,
                        "artifacts_refs": list(turn.trajectory_digest.artifacts_refs),
                    }
                    if turn.trajectory_digest is not None
                    else None
                ),
                "artifacts_shown": dict(turn.artifacts_shown),
                "artifacts_hidden_refs": list(turn.artifacts_hidden_refs),
                "ts": turn.ts,
            }

        return {
            "version": 1,
            "health": self._health.value,
            "summary": self._summary,
            "turns": [_dump_turn(t) for t in self._turns],
            "pending": [_dump_turn(t) for t in self._pending],
            "backlog": [_dump_turn(t) for t in self._backlog],
            "config_snapshot": snapshot,
        }

    def from_dict(self, state: Mapping[str, Any]) -> None:
        """Load memory state from a dictionary created by `to_dict()`."""
        if state.get("version") != 1:
            raise ValueError("Unsupported memory state version")

        health = state.get("health")
        if health not in {h.value for h in MemoryHealth}:
            raise ValueError("Invalid health value in memory state")

        def _load_turn(payload: Any) -> ConversationTurn:
            if not isinstance(payload, Mapping):
                raise ValueError("Invalid turn payload")
            digest_payload = payload.get("trajectory_digest")
            digest = None
            if digest_payload is not None:
                if not isinstance(digest_payload, Mapping):
                    raise ValueError("Invalid trajectory_digest payload")
                digest = TrajectoryDigest(
                    tools_invoked=list(digest_payload.get("tools_invoked") or []),
                    observations_summary=str(digest_payload.get("observations_summary") or ""),
                    reasoning_summary=(
                        str(digest_payload.get("reasoning_summary"))
                        if digest_payload.get("reasoning_summary") is not None
                        else None
                    ),
                    artifacts_refs=list(digest_payload.get("artifacts_refs") or []),
                )

            return ConversationTurn(
                user_message=str(payload.get("user_message") or ""),
                assistant_response=str(payload.get("assistant_response") or ""),
                trajectory_digest=digest,
                artifacts_shown=dict(payload.get("artifacts_shown") or {}),
                artifacts_hidden_refs=list(payload.get("artifacts_hidden_refs") or []),
                ts=float(payload.get("ts") or 0.0),
            )

        turns = state.get("turns") or []
        pending = state.get("pending") or []
        backlog = state.get("backlog") or []
        if not isinstance(turns, Sequence) or not isinstance(pending, Sequence) or not isinstance(backlog, Sequence):
            raise ValueError("Invalid memory state structure")

        self._health = MemoryHealth(str(health))
        self._summary = str(state.get("summary") or "")
        self._turns = [_load_turn(t) for t in turns]
        self._pending = [_load_turn(t) for t in pending]
        self._backlog = [_load_turn(t) for t in backlog]
        self._summarize_task = None
        self._retry_count = 0
        self._last_degraded_attempt_ts = 0.0

    def get_artifact(self, ref: str) -> Any | None:
        """Return a hidden artifact by reference (default implementation returns None)."""
        del ref
        return None

    def _truncate_to_full_zone_locked(self, *, store_backlog: bool = False) -> None:
        limit = self._config.budget.full_zone_turns
        if limit <= 0:
            dropped = self._turns
            self._turns = []
        elif len(self._turns) <= limit:
            return
        else:
            dropped = self._turns[:-limit]
            self._turns = self._turns[-limit:]

        if store_backlog and dropped:
            self._backlog.extend(dropped)
            overflow = len(self._backlog) - self._config.recovery_backlog_limit
            if overflow > 0:
                self._backlog = self._backlog[overflow:]

    def _evict_to_pending_locked(self) -> None:
        limit = self._config.budget.full_zone_turns
        if limit <= 0:
            if self._turns:
                self._pending.extend(self._turns)
                self._turns = []
            return
        while len(self._turns) > limit:
            self._pending.append(self._turns.pop(0))

    def _maybe_schedule_summarize_locked(self) -> None:
        if self._summarize_task is not None and not self._summarize_task.done():
            return
        if not self._pending:
            return
        self._summarize_task = asyncio.create_task(self._run_summarization())

    def _maybe_schedule_degraded_recovery_locked(self) -> None:
        if not self._backlog:
            return
        now = self._time_source()
        if now - self._last_degraded_attempt_ts < self._config.degraded_retry_interval_s:
            return
        if self._summarize_task is not None and not self._summarize_task.done():
            return
        self._last_degraded_attempt_ts = now
        self._summarize_task = asyncio.create_task(self._run_recovery())

    async def _run_summarization(self) -> None:
        await self._run_summarization_impl(recovering=False)

    async def _run_recovery(self) -> None:
        await self._run_summarization_impl(recovering=True)

    async def _run_summarization_impl(self, *, recovering: bool) -> None:
        while True:
            async with self._lock:
                if self._config.strategy != "rolling_summary":
                    return
                if self._health == MemoryHealth.DEGRADED and not recovering:
                    return
                if recovering and not self._backlog:
                    return
                if not recovering and not self._pending:
                    return

                old_health = self._health
                if recovering:
                    self._health = MemoryHealth.RECOVERING
                elif self._retry_count > 0:
                    self._health = MemoryHealth.RETRY

                await self._maybe_fire_on_health_changed(old_health, self._health)

                if recovering:
                    turns = list(self._backlog)
                else:
                    turns = list(self._pending)
                previous_summary = self._summary

            include_digest = self._config.include_trajectory_digest
            turns_payload = [_turn_to_llm_dict(t, include_digest=include_digest) for t in turns]
            request = {"previous_summary": previous_summary, "turns": turns_payload}

            try:
                response = await self._summarizer(request)
                summary = response.get("summary") if isinstance(response, Mapping) else None
                if not isinstance(summary, str):
                    raise TypeError("summarizer response must include a string 'summary'")
            except Exception:
                await self._handle_summarizer_failure()
                return

            async with self._lock:
                old_summary = self._summary
                self._summary = summary
                if recovering:
                    self._backlog = []
                else:
                    self._pending = []
                self._retry_count = 0

                old_health = self._health
                self._health = MemoryHealth.HEALTHY

                self._truncate_summary_to_budget_locked()
                self._enforce_budget_locked()

            await self._maybe_fire_on_summary_updated(old_summary, summary)
            await self._maybe_fire_on_health_changed(old_health, self._health)
            return

    async def _handle_summarizer_failure(self) -> None:
        async with self._lock:
            self._retry_count += 1
            old_health = self._health
            if self._retry_count <= self._config.retry_attempts:
                self._health = MemoryHealth.RETRY
            else:
                self._health = MemoryHealth.DEGRADED
                self._backlog.extend(self._pending)
                self._pending = []
                overflow = len(self._backlog) - self._config.recovery_backlog_limit
                if overflow > 0:
                    self._backlog = self._backlog[overflow:]
            await self._maybe_fire_on_health_changed(old_health, self._health)
            retry = self._health == MemoryHealth.RETRY
            attempt = self._retry_count

        if retry:
            await asyncio.sleep(self._config.retry_backoff_base_s * (2 ** max(0, attempt - 1)))
            async with self._lock:
                if self._config.strategy != "rolling_summary":
                    return
                if not self._pending:
                    return
                if self._summarize_task is None or self._summarize_task.done():
                    self._summarize_task = asyncio.create_task(self._run_summarization())

    def _truncate_summary_to_budget_locked(self) -> None:
        budget = self._config.budget
        if budget.summary_max_tokens <= 0:
            self._summary = ""
            return
        if not self._summary:
            return
        estimator = self._config.token_estimator or default_token_estimator
        tokens = estimator(self._summary)
        if tokens <= budget.summary_max_tokens:
            return
        if budget.overflow_policy == "error":
            raise MemoryBudgetExceeded("summary exceeded budget")

        ratio = budget.summary_max_tokens / max(tokens, 1)
        keep_chars = max(1, int(len(self._summary) * ratio))
        self._summary = self._summary[-keep_chars:]

    def _enforce_budget_locked(self) -> None:
        total_budget = self._config.budget.total_max_tokens
        if total_budget <= 0:
            return
        estimator = self._config.token_estimator or default_token_estimator

        while True:
            snapshot = self._estimate_payload_snapshot()
            tokens = estimator(_compact_json(snapshot))
            if tokens <= total_budget:
                return

            policy = self._config.budget.overflow_policy
            if policy == "error":
                raise MemoryBudgetExceeded("memory context exceeded total_max_tokens")

            if policy == "truncate_summary" and self._summary:
                self._truncate_summary_to_budget_locked()
                continue

            if self._pending:
                self._pending.pop(0)
                continue

            if self._turns:
                self._turns.pop(0)
                continue

            self._summary = ""
            return

    def _estimate_payload_snapshot(self) -> dict[str, Any]:
        include_digest = self._config.include_trajectory_digest
        if self._config.strategy == "truncation":
            return {"recent_turns": [_turn_to_llm_dict(t, include_digest=include_digest) for t in self._turns]}
        if self._config.strategy == "rolling_summary":
            payload: dict[str, Any] = {
                "recent_turns": [_turn_to_llm_dict(t, include_digest=include_digest) for t in self._turns]
            }
            if self._health != MemoryHealth.DEGRADED:
                if self._pending:
                    payload["pending_turns"] = [
                        _turn_to_llm_dict(t, include_digest=include_digest) for t in self._pending
                    ]
                if self._summary:
                    payload["summary"] = self._summary
            return payload
        return {}

    async def _maybe_fire_on_turn_added(self, turn: ConversationTurn) -> None:
        cb = self._config.on_turn_added
        if cb is None:
            return

        async def _run() -> None:
            try:
                await cb(turn)
            except Exception:
                return

        _safe_create_task(_run())

    async def _maybe_fire_on_summary_updated(self, old: str, new: str) -> None:
        cb = self._config.on_summary_updated
        if cb is None:
            return

        async def _run() -> None:
            try:
                await cb(old, new)
            except Exception:
                return

        _safe_create_task(_run())

    async def _maybe_fire_on_health_changed(self, old: MemoryHealth, new: MemoryHealth) -> None:
        if old == new:
            return
        cb = self._config.on_health_changed
        if cb is None:
            return

        async def _run() -> None:
            try:
                await cb(old, new)
            except Exception:
                return

        _safe_create_task(_run())


__all__ = [
    "ConversationTurn",
    "DefaultShortTermMemory",
    "MemoryBudget",
    "MemoryBudgetExceeded",
    "MemoryHealth",
    "MemoryIsolation",
    "MemoryKey",
    "ShortTermMemory",
    "ShortTermMemoryArtifacts",
    "ShortTermMemoryConfig",
    "ShortTermMemoryPersistence",
    "ShortTermMemorySerializable",
    "TrajectoryDigest",
    "default_token_estimator",
]
