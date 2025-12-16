from __future__ import annotations

import asyncio
import json
import logging
import time
import warnings
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerEvent, PlannerPause, ReactPlanner
from penguiflow.planner.memory import MemoryBudget, MemoryKey, ShortTermMemoryConfig
from penguiflow.planner.react import (
    PlannerAction,
    ReflectionConfig,
    Trajectory,
    TrajectoryStep,
)
from penguiflow.registry import ModelRegistry


def _extract_read_only_conversation_memory(messages: list[Mapping[str, str]]) -> dict[str, Any] | None:
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if "<read_only_conversation_memory_json>" not in content:
            continue
        if "</read_only_conversation_memory_json>" not in content:
            continue
        start = content.index("<read_only_conversation_memory_json>") + len("<read_only_conversation_memory_json>")
        end = content.index("</read_only_conversation_memory_json>", start)
        return json.loads(content[start:end])
    return None


class Query(BaseModel):
    question: str


class Intent(BaseModel):
    intent: str


class Documents(BaseModel):
    documents: list[str]


class SearchResult(BaseModel):
    documents: list[str]


class Answer(BaseModel):
    answer: str


class ShardRequest(BaseModel):
    topic: str
    shard: int


class ShardPayload(BaseModel):
    shard: int
    text: str


class MergeArgs(BaseModel):
    expect: int
    results: list[ShardPayload]


class FlexibleMergeArgs(BaseModel):
    expected: int
    payloads: list[ShardPayload]
    branches: list[dict[str, Any]]
    success_total: int


class AuditArgs(BaseModel):
    branches: list[dict[str, Any]]
    failures: list[dict[str, Any]]


@tool(desc="Detect intent", tags=["nlp"])
async def triage(args: Query, ctx: object) -> Intent:
    return Intent(intent="docs")


@tool(desc="Search knowledge base", side_effects="read")
async def retrieve(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"Answering about {args.intent}"])


@tool(desc="Search knowledge base (cost tracking)")
async def search(args: Query, ctx: object) -> SearchResult:
    return SearchResult(documents=[f"Results for {args.question}"])


@tool(desc="Compose final answer")
async def respond(args: Answer, ctx: object) -> Answer:
    return args


CONTEXT_CAPTURE: dict[str, Any] = {}
RESUME_CAPTURE: dict[str, Any] = {}


@tool(desc="Echo answer while recording contexts")
async def capture_answer(args: Answer, ctx: Any) -> Answer:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        meta_view = dict(ctx.meta)
    CONTEXT_CAPTURE["llm_context"] = dict(ctx.llm_context)
    CONTEXT_CAPTURE["tool_context"] = dict(ctx.tool_context)
    CONTEXT_CAPTURE["meta"] = meta_view
    CONTEXT_CAPTURE["meta_warnings"] = [
        warning for warning in caught if issubclass(warning.category, DeprecationWarning)
    ]
    return Answer(answer=args.answer)


@tool(desc="Return invalid documents")
async def broken(args: Intent, ctx: object) -> Documents:  # type: ignore[return-type]
    return "boom"  # type: ignore[return-value]


@tool(desc="Fetch documents from primary shard", tags=["parallel"])
async def fetch_primary(args: ShardRequest, ctx: Any) -> ShardPayload:
    await asyncio.sleep(0.05)
    return ShardPayload(shard=args.shard, text=f"{args.topic}-primary")


@tool(desc="Fetch documents from secondary shard", tags=["parallel"])
async def fetch_secondary(args: ShardRequest, ctx: Any) -> ShardPayload:
    await asyncio.sleep(0.05)
    return ShardPayload(shard=args.shard, text=f"{args.topic}-secondary")


@tool(desc="Merge shard payloads")
async def merge_results(args: MergeArgs, ctx: Any) -> Documents:
    assert ctx.meta.get("parallel_success_count") == args.expect
    assert len(ctx.meta.get("parallel_results", [])) == args.expect
    return Documents(documents=[item.text for item in args.results])


@tool(desc="Merge shard payloads with explicit inject mapping")
async def merge_results_explicit(args: FlexibleMergeArgs, ctx: Any) -> Documents:
    assert args.success_total == args.expected == len(args.payloads)
    assert len(args.branches) == args.expected
    return Documents(documents=[item.text for item in args.payloads])


AUDIT_CALLS: list[dict[str, Any]] = []


@tool(desc="Audit failed branches")
async def audit_parallel(args: AuditArgs, ctx: Any) -> Documents:
    AUDIT_CALLS.append(args.model_dump())
    return Documents(documents=[f"{len(args.failures)} failures"])


@tool(desc="Approval required before proceeding")
async def approval_gate(args: Intent, ctx: Any) -> Intent:
    await ctx.pause("approval_required", {"intent": args.intent})
    return args


@tool(desc="Pause and record tool context")
async def pause_and_record(args: Intent, ctx: Any) -> Intent:
    RESUME_CAPTURE.setdefault("calls", []).append(
        {
            "llm_context": dict(ctx.llm_context),
            "tool_context": dict(ctx.tool_context),
        }
    )
    await ctx.pause("approval_required", {"intent": args.intent})
    return args


@tool(desc="Respond while recording tool context")
async def respond_with_context(args: Answer, ctx: Any) -> Answer:
    RESUME_CAPTURE["resumed_tool_context"] = dict(ctx.tool_context)
    RESUME_CAPTURE["resumed_llm_context"] = dict(ctx.llm_context)
    return args


class PlannerTimeout(RuntimeError):
    def __init__(self, message: str, suggestion: str) -> None:
        super().__init__(message)
        self.suggestion = suggestion


@tool(desc="Remote fetch that may timeout", side_effects="external")
async def unstable(args: Intent, ctx: object) -> Documents:
    raise PlannerTimeout("upstream timeout", "use_cache")


@tool(desc="Use cached retrieval", side_effects="read")
async def cached(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"Cached docs for {args.intent}"])


class StubClient:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del stream, on_stream_chunk
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


class SummarizerStub:
    def __init__(self) -> None:
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del stream, on_stream_chunk
        self.calls.append(list(messages))
        return (
            json.dumps(
                {
                    "goals": ["stub"],
                    "facts": {"note": "compact"},
                    "pending": [],
                    "last_output_digest": "stub",
                    "note": "stub",
                }
            ),
            0.0,
        )


class CostStubClient:
    """Stub client that also tracks synthetic cost values."""

    def __init__(self, responses: list[tuple[Mapping[str, object], float]]) -> None:
        self._responses = [(json.dumps(payload, ensure_ascii=False), float(cost)) for payload, cost in responses]
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del response_format, stream, on_stream_chunk
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0)


def make_planner(client: StubClient, **kwargs: object) -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("retrieve", Intent, Documents)
    registry.register("respond", Answer, Answer)
    registry.register("broken", Intent, Documents)

    nodes = [
        Node(triage, name="triage"),
        Node(retrieve, name="retrieve"),
        Node(respond, name="respond"),
        Node(broken, name="broken"),
    ]
    catalog = build_catalog(nodes, registry)
    return ReactPlanner(llm_client=client, catalog=catalog, **kwargs)


@pytest.mark.asyncio()
async def test_react_planner_runs_end_to_end() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What is PenguiFlow?"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "final",
                "next_node": None,
                "args": {"raw_answer": "PenguiFlow is lightweight."},
            },
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Tell me about PenguiFlow")

    assert result.reason == "answer_complete"
    # FinalPayload wraps the answer with standard fields
    assert result.payload["raw_answer"] == "PenguiFlow is lightweight."
    assert "artifacts" in result.payload
    assert result.metadata["step_count"] == 2


@pytest.mark.asyncio()
async def test_react_planner_collects_sources_into_payload() -> None:
    class SourceArgs(BaseModel):
        query: str

    class SourceDoc(BaseModel):
        model_config = ConfigDict(json_schema_extra={"produces_sources": True})

        title: str
        url: str | None = None
        snippet: str
        score: float = Field(
            default=0.0,
            json_schema_extra={"source_field": "relevance_score"},
        )

    class SourceResult(BaseModel):
        results: list[SourceDoc]

    @tool(desc="Return documents with source markers")
    async def retrieve_sources(args: SourceArgs, ctx: Any) -> SourceResult:
        del ctx
        return SourceResult(
            results=[
                SourceDoc(
                    title="Doc A",
                    url="https://example.com/a",
                    snippet="alpha",
                    score=0.9,
                ),
                SourceDoc(
                    title="Doc B",
                    url="https://example.com/b",
                    snippet="beta",
                    score=0.7,
                ),
            ]
        )

    registry = ModelRegistry()
    registry.register("retrieve_sources", SourceArgs, SourceResult)

    client = StubClient(
        [
            {
                "thought": "collect",
                "next_node": "retrieve_sources",
                "args": {"query": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(
            [Node(retrieve_sources, name="retrieve_sources")],
            registry,
        ),
    )

    result = await planner.run("Fetch docs")

    assert result.reason == "answer_complete"
    sources = result.payload["sources"]
    assert len(sources) == 2
    assert sources[0]["title"] == "Doc A"
    assert sources[0]["relevance_score"] == pytest.approx(0.9)
    assert result.metadata["sources"] == sources


@pytest.mark.asyncio()
async def test_tool_context_separation_and_meta_warning() -> None:
    CONTEXT_CAPTURE.clear()
    client = StubClient(
        [
            {
                "thought": "capture",
                "next_node": "capture",
                "args": {"answer": "ok"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "ok"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("capture", Answer, Answer)
    nodes = [Node(capture_answer, name="capture")]
    catalog = build_catalog(nodes, registry)
    planner = ReactPlanner(llm_client=client, catalog=catalog)

    sentinel = object()
    llm_ctx = {"visible": "memories"}
    tool_ctx = {"hidden": "secret", "sentinel": sentinel}

    result = await planner.run("Check context separation", llm_context=llm_ctx, tool_context=tool_ctx)

    assert result.reason == "answer_complete"
    assert CONTEXT_CAPTURE["llm_context"] == llm_ctx
    assert CONTEXT_CAPTURE["tool_context"]["hidden"] == "secret"
    assert CONTEXT_CAPTURE["tool_context"]["sentinel"] is sentinel
    assert CONTEXT_CAPTURE["meta"]["visible"] == "memories"
    assert CONTEXT_CAPTURE["meta"]["hidden"] == "secret"
    assert any(issubclass(warning.category, DeprecationWarning) for warning in CONTEXT_CAPTURE["meta_warnings"])
    first_user_messages = [msg["content"] for msg in client.calls[0] if msg.get("role") == "user"]
    assert any("memories" in content for content in first_user_messages)
    assert all("secret" not in content for content in first_user_messages)


@pytest.mark.asyncio()
async def test_react_planner_injects_short_term_memory_across_runs() -> None:
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}},
        ]
    )
    planner = make_planner(
        client,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")

    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)

    first_call = client.calls[0]
    memory1 = _extract_read_only_conversation_memory(first_call)
    assert memory1 is not None
    assert memory1["recent_turns"] == []

    second_call = client.calls[1]
    memory2 = _extract_read_only_conversation_memory(second_call)
    assert memory2 is not None
    recent = memory2["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio()
async def test_react_planner_rolling_summary_uses_llm_summarizer() -> None:
    class Client:
        def __init__(self) -> None:
            self._actions = [
                {"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}},
                {"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}},
            ]
            self.summary_calls = 0
            self.calls: list[tuple[str | None, list[Mapping[str, str]]]] = []

        async def complete(
            self,
            *,
            messages: list[Mapping[str, str]],
            response_format: Mapping[str, object] | None = None,
            stream: bool = False,
            on_stream_chunk: object = None,
        ) -> tuple[str, float]:
            del stream, on_stream_chunk
            schema_name = None
            if isinstance(response_format, Mapping):
                json_schema = response_format.get("json_schema")
                if isinstance(json_schema, Mapping):
                    schema_name = json_schema.get("name")
            self.calls.append((schema_name if isinstance(schema_name, str) else None, list(messages)))

            if schema_name == "short_term_memory_summary":
                self.summary_calls += 1
                return json.dumps({"summary": "<session_summary>ok</session_summary>"}), 0.0

            if not self._actions:
                raise AssertionError("No stub responses left")
            return json.dumps(self._actions.pop(0), ensure_ascii=False), 0.0

    client = Client()
    planner = make_planner(
        client,
        short_term_memory=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=0),
            retry_attempts=0,
            retry_backoff_base_s=0.0,
        ),
    )
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")

    await planner.run("q1", memory_key=key)
    memory = planner._memory_by_key[key.composite()]
    await memory.flush()
    await planner.run("q2", memory_key=key)

    assert client.summary_calls == 1
    assert any(name == "short_term_memory_summary" for name, _ in client.calls)

    second_messages = client.calls[-1][1]
    memory2 = _extract_read_only_conversation_memory(second_messages)
    assert memory2 is not None
    assert memory2["summary"] == "<session_summary>ok</session_summary>"


@pytest.mark.asyncio()
async def test_react_planner_extracts_memory_key_from_tool_context() -> None:
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}},
        ]
    )
    planner = make_planner(
        client,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    tool_ctx = {"tenant_id": "t", "user_id": "u", "session_id": "s"}

    await planner.run("q1", tool_context=tool_ctx)
    await planner.run("q2", tool_context=tool_ctx)

    second_messages = client.calls[1]
    memory2 = _extract_read_only_conversation_memory(second_messages)
    assert memory2 is not None
    recent = memory2["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio()
async def test_react_planner_persists_and_hydrates_short_term_memory() -> None:
    class Store:
        def __init__(self) -> None:
            self.data: dict[str, dict[str, Any]] = {}

        async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
            self.data[key] = state

        async def load_memory_state(self, key: str) -> dict[str, Any] | None:
            return self.data.get(key)

    store = Store()
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")

    client1 = StubClient([{"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}}])
    planner1 = make_planner(
        client1,
        state_store=store,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    await planner1.run("q1", memory_key=key)

    client2 = StubClient([{"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}}])
    planner2 = make_planner(
        client2,
        state_store=store,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    await planner2.run("q2", memory_key=key)

    second_messages = client2.calls[0]
    memory2 = _extract_read_only_conversation_memory(second_messages)
    assert memory2 is not None
    recent = memory2["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio()
async def test_react_planner_memory_fail_closed_without_key() -> None:
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}},
        ]
    )
    planner = make_planner(
        client,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )

    await planner.run("q1")
    await planner.run("q2")

    second_messages = client.calls[1]
    assert _extract_read_only_conversation_memory(second_messages) is None


@pytest.mark.asyncio()
async def test_react_planner_session_isolation_by_memory_key() -> None:
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a1"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "a2"}},
        ]
    )
    planner = make_planner(
        client,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    k1 = MemoryKey(tenant_id="t", user_id="u", session_id="s1")
    k2 = MemoryKey(tenant_id="t", user_id="u", session_id="s2")

    await planner.run("q1", memory_key=k1)
    await planner.run("q2", memory_key=k2)

    second_messages = client.calls[1]
    memory2 = _extract_read_only_conversation_memory(second_messages)
    assert memory2 is not None
    assert memory2.get("recent_turns") == []


@pytest.mark.asyncio()
async def test_llm_context_must_be_json_serialisable() -> None:
    planner = make_planner(StubClient([]))
    with pytest.raises(TypeError):
        await planner.run("invalid", llm_context={"bad": object()})


@pytest.mark.asyncio()
async def test_react_planner_recovers_from_invalid_node() -> None:
    client = StubClient(
        [
            {"thought": "invalid", "next_node": "missing", "args": {}},
            {"thought": "triage", "next_node": "triage", "args": {"question": "What?"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test invalid node")

    assert result.reason == "answer_complete"
    assert any("missing" in step["error"] for step in result.metadata["steps"])


@pytest.mark.asyncio()
async def test_react_planner_autofills_missing_args() -> None:
    """Test that missing required args are autofilled with defaults."""
    client = StubClient(
        [
            # Args empty - will be autofilled with defaults
            {"thought": "retrieve", "next_node": "retrieve", "args": {}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "ok"}},
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test autofill path")

    # Should complete without validation errors because autofill kicks in
    assert result.reason == "answer_complete"
    # First step should have observation (tool was called successfully with autofilled args)
    steps = result.metadata["steps"]
    assert len(steps) >= 1
    assert steps[0]["observation"] is not None


@pytest.mark.asyncio()
async def test_react_planner_reports_output_validation_error() -> None:
    client = StubClient(
        [
            {
                "thought": "broken",
                "next_node": "broken",
                "args": {"intent": "docs"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "fallback"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("broken", Intent, Documents)
    catalog = build_catalog([Node(broken, name="broken")], registry)
    planner = ReactPlanner(llm_client=client, catalog=catalog)

    result = await planner.run("Test output validation path")

    errors = [step["error"] for step in result.metadata["steps"] if step["error"]]
    assert any("returned data" in err for err in errors)


@pytest.mark.asyncio()
async def test_react_planner_replans_after_tool_failure() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Need docs"},
            },
            {
                "thought": "remote",
                "next_node": "unstable",
                "args": {"intent": "docs"},
            },
            {
                "thought": "fallback",
                "next_node": "cached",
                "args": {"intent": "docs"},
            },
            {
                "thought": "wrap",
                "next_node": "respond",
                "args": {"answer": "Using cached docs"},
            },
            {
                "thought": "final",
                "next_node": None,
                "args": {"raw_answer": "Using cached docs"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("unstable", Intent, Documents)
    registry.register("cached", Intent, Documents)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(unstable, name="unstable"),
        Node(cached, name="cached"),
        Node(respond, name="respond"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        max_iters=5,
    )

    result = await planner.run("Fetch docs with fallback")

    assert result.reason == "answer_complete"
    failure_step = next(
        (step for step in result.metadata["steps"] if step.get("failure")),
        None,
    )
    assert failure_step is not None
    assert failure_step["failure"]["node"] == "unstable"

    failure_prompt = json.loads(client.calls[2][-1]["content"])
    assert failure_prompt["failure"]["suggestion"] == "use_cache"
    assert failure_prompt["failure"]["error_code"] == "PlannerTimeout"


def test_react_planner_requires_catalog_or_nodes() -> None:
    client = StubClient([])
    with pytest.raises(ValueError):
        ReactPlanner(llm_client=client)


def test_react_planner_requires_llm_or_client() -> None:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    with pytest.raises(ValueError):
        ReactPlanner(nodes=nodes, registry=registry)


@pytest.mark.asyncio()
async def test_react_planner_iteration_limit_returns_no_path() -> None:
    client = StubClient(
        [
            {
                "thought": "loop",
                "next_node": "triage",
                "args": {"question": "still thinking"},
            }
        ]
    )
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(triage, name="triage")], registry),
        max_iters=1,
    )

    result = await planner.run("Explain")
    assert result.reason == "no_path"


@pytest.mark.asyncio()
async def test_react_planner_enforces_hop_budget_limits() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Budget"},
            },
            {
                "thought": "still need",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "retry",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("retrieve", Intent, Documents)

    nodes = [
        Node(triage, name="triage"),
        Node(retrieve, name="retrieve"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        hop_budget=1,
        max_iters=3,
    )

    result = await planner.run("Constrained plan")

    assert result.reason == "budget_exhausted"
    constraints = result.metadata["constraints"]
    assert constraints["hop_exhausted"] is True
    violation = json.loads(client.calls[2][-1]["content"])
    assert "Hop budget" in violation["error"]


@pytest.mark.asyncio()
async def test_react_planner_litellm_guard_raises_runtime_error() -> None:
    litellm = pytest.importorskip("litellm")

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    planner = ReactPlanner(llm="dummy", nodes=nodes, registry=registry)
    trajectory = Trajectory(query="hi")
    # When litellm is installed, it raises BadRequestError for invalid model names
    with pytest.raises((RuntimeError, litellm.exceptions.BadRequestError)) as exc:
        await planner.step(trajectory)
    # Accept either error message
    assert "LiteLLM is not installed" in str(exc.value) or "LLM Provider NOT provided" in str(exc.value)


@pytest.mark.asyncio()
async def test_react_planner_step_repairs_invalid_action() -> None:
    # Use input that cannot be salvaged - not valid JSON at all
    client = StubClient(
        [
            "not valid json {{{",
            {
                "thought": "recover",
                "next_node": "triage",
                "args": {"question": "fixed"},
            },
        ]
    )
    planner = make_planner(client)
    trajectory = Trajectory(query="recover")

    action = await planner.step(trajectory)
    assert action.next_node == "triage"
    assert len(client.calls) == 2
    repair_message = client.calls[1][-1]["content"]
    assert "invalid JSON" in repair_message


@pytest.mark.asyncio()
async def test_react_planner_step_salvages_empty_json() -> None:
    """Test that empty JSON {} is salvaged into a valid finish action."""
    client = StubClient(
        [
            "{}",
        ]
    )
    planner = make_planner(client)
    trajectory = Trajectory(query="salvage test")

    action = await planner.step(trajectory)
    # Empty JSON should be salvaged to a finish action (next_node=None)
    assert action.next_node is None
    assert action.thought == "planning next step"
    assert len(client.calls) == 1  # No repair needed


@pytest.mark.asyncio()
async def test_react_planner_compacts_history_when_budget_exceeded() -> None:
    long_answer = "PenguiFlow " * 30
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What is the plan?"},
            },
            {
                "thought": "respond",
                "next_node": "respond",
                "args": {"answer": long_answer},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    planner = make_planner(client, token_budget=180)

    result = await planner.run("Explain budget handling")

    assert result.reason == "answer_complete"
    assert any(msg["role"] == "system" and "Trajectory summary" in msg["content"] for msg in client.calls[1])


@pytest.mark.asyncio()
async def test_react_planner_invokes_summarizer_client() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Summarise"},
            },
            {
                "thought": "respond",
                "next_node": "respond",
                "args": {"answer": "value"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "ok"}},
        ]
    )
    planner = make_planner(client, token_budget=60)
    summarizer = SummarizerStub()
    planner._summarizer_client = summarizer  # type: ignore[attr-defined]

    await planner.run("Trigger summariser")

    assert summarizer.calls, "Expected summarizer to be invoked"


@pytest.mark.asyncio()
async def test_react_planner_pause_and_resume_flow() -> None:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("approval", Intent, Intent)
    registry.register("retrieve", Intent, Documents)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(approval_gate, name="approval"),
        Node(retrieve, name="retrieve"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Send report"},
            },
            {
                "thought": "approval",
                "next_node": "approval",
                "args": {"intent": "docs"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "Report sent"},
            },
        ]
    )
    planner = ReactPlanner(llm_client=client, catalog=catalog, pause_enabled=True)

    pause_result = await planner.run("Share metrics with approval")
    assert isinstance(pause_result, PlannerPause)
    assert pause_result.reason == "approval_required"

    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
    )
    assert resume_result.reason == "answer_complete"

    post_pause_calls = client.calls[2:]
    assert any("Resume input" in msg["content"] for call in post_pause_calls for msg in call)


@pytest.mark.asyncio()
async def test_resume_accepts_tool_context_override() -> None:
    RESUME_CAPTURE.clear()
    registry = ModelRegistry()
    registry.register("pause_and_record", Intent, Intent)
    registry.register("contextual_respond", Answer, Answer)

    nodes = [
        Node(pause_and_record, name="pause_and_record"),
        Node(respond_with_context, name="contextual_respond"),
    ]
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [
            {
                "thought": "pause",
                "next_node": "pause_and_record",
                "args": {"intent": "docs"},
            },
            {
                "thought": "respond",
                "next_node": "contextual_respond",
                "args": {"answer": "done"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    planner = ReactPlanner(llm_client=client, catalog=catalog, pause_enabled=True)

    pause_result = await planner.run("Need approval", llm_context={"user": "demo"}, tool_context={"initial": "one"})
    assert isinstance(pause_result, PlannerPause)
    assert RESUME_CAPTURE["calls"][0]["tool_context"]["initial"] == "one"

    finish_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
        tool_context={"resumed": "two"},
    )

    assert finish_result.reason == "answer_complete"
    assert RESUME_CAPTURE["resumed_tool_context"]["resumed"] == "two"


@pytest.mark.asyncio()
async def test_react_planner_resume_preserves_hop_budget() -> None:
    registry = ModelRegistry()
    registry.register("approval", Intent, Intent)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(approval_gate, name="approval"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [
            {
                "thought": "request approval",
                "next_node": "approval",
                "args": {"intent": "docs"},
            },
            {
                "thought": "follow up",
                "next_node": "respond",
                "args": {"answer": "Report"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "Report"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        pause_enabled=True,
        hop_budget=1,
    )

    pause_result = await planner.run("Send report with approval")
    assert isinstance(pause_result, PlannerPause)
    assert pause_result.reason == "approval_required"

    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
    )
    assert resume_result.reason == "answer_complete"

    steps = resume_result.metadata["steps"]
    assert any(step.get("error") and "Hop budget" in step["error"] for step in steps), (
        "expected hop budget violation after resume"
    )

    constraints = resume_result.metadata["constraints"]
    assert constraints["hops_used"] == 1
    assert constraints["hop_exhausted"] is True


@pytest.mark.asyncio()
async def test_react_planner_disallows_nodes_from_hints() -> None:
    client = StubClient(
        [
            {
                "thought": "bad",
                "next_node": "broken",
                "args": {"intent": "docs"},
            },
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Hi"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = make_planner(client, planning_hints={"disallow_nodes": ["broken"]})

    result = await planner.run("test hints")

    assert result.reason == "answer_complete"
    assert any(msg["role"] == "user" and "not permitted" in msg["content"] for msg in client.calls[1])


@pytest.mark.asyncio()
async def test_react_planner_emits_ordering_hint_once() -> None:
    client = StubClient(
        [
            {
                "thought": "early",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Order?"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = make_planner(
        client,
        planning_hints={"ordering_hints": ["triage", "retrieve"]},
    )

    result = await planner.run("ordering")

    assert result.reason == "answer_complete"
    assert any(msg["role"] == "user" and "Ordering hint" in msg["content"] for msg in client.calls[1])


@pytest.mark.asyncio()
async def test_react_planner_parallel_plan_executes_concurrently() -> None:
    client = StubClient(
        [
            {
                "thought": "fan out",
                "plan": [
                    {
                        "node": "fetch_primary",
                        "args": {"topic": "topic", "shard": 0},
                    },
                    {
                        "node": "fetch_secondary",
                        "args": {"topic": "topic", "shard": 1},
                    },
                ],
                "join": {"node": "merge_results"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("fetch_primary", ShardRequest, ShardPayload)
    registry.register("fetch_secondary", ShardRequest, ShardPayload)
    registry.register("merge_results", MergeArgs, Documents)

    nodes = [
        Node(fetch_primary, name="fetch_primary"),
        Node(fetch_secondary, name="fetch_secondary"),
        Node(merge_results, name="merge_results"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    start = time.perf_counter()
    result = await planner.run("parallel fan out")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    assert result.reason == "answer_complete"

    step = result.metadata["steps"][0]
    assert step["action"]["plan"]
    join_obs = step["observation"]["join"]["observation"]
    assert join_obs["documents"] == ["topic-primary", "topic-secondary"]
    assert step["observation"]["stats"] == {"success": 2, "failed": 0}


@pytest.mark.asyncio()
async def test_react_planner_parallel_join_explicit_inject_mapping() -> None:
    client = StubClient(
        [
            {
                "thought": "fan out",
                "plan": [
                    {
                        "node": "fetch_primary",
                        "args": {"topic": "topic", "shard": 0},
                    },
                    {
                        "node": "fetch_secondary",
                        "args": {"topic": "topic", "shard": 1},
                    },
                ],
                "join": {
                    "node": "merge_results_explicit",
                    "inject": {
                        "payloads": "$results",
                        "expected": "$expect",
                        "branches": "$branches",
                        "success_total": "$success_count",
                    },
                },
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("fetch_primary", ShardRequest, ShardPayload)
    registry.register("fetch_secondary", ShardRequest, ShardPayload)
    registry.register("merge_results_explicit", FlexibleMergeArgs, Documents)

    nodes = [
        Node(fetch_primary, name="fetch_primary"),
        Node(fetch_secondary, name="fetch_secondary"),
        Node(merge_results_explicit, name="merge_results_explicit"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("parallel explicit inject")
    assert result.reason == "answer_complete"

    step = result.metadata["steps"][0]
    join_obs = step["observation"]["join"]["observation"]
    assert join_obs["documents"] == ["topic-primary", "topic-secondary"]
    assert step["observation"]["stats"] == {"success": 2, "failed": 0}


@pytest.mark.asyncio()
async def test_react_planner_parallel_join_magic_injection_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = StubClient(
        [
            {
                "thought": "fan out",
                "plan": [
                    {
                        "node": "fetch_primary",
                        "args": {"topic": "topic", "shard": 0},
                    },
                    {
                        "node": "fetch_secondary",
                        "args": {"topic": "topic", "shard": 1},
                    },
                ],
                "join": {"node": "merge_results"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("fetch_primary", ShardRequest, ShardPayload)
    registry.register("fetch_secondary", ShardRequest, ShardPayload)
    registry.register("merge_results", MergeArgs, Documents)

    nodes = [
        Node(fetch_primary, name="fetch_primary"),
        Node(fetch_secondary, name="fetch_secondary"),
        Node(merge_results, name="merge_results"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    with caplog.at_level(logging.WARNING, logger="penguiflow.planner"):
        result = await planner.run("parallel implicit inject")

    assert result.reason == "answer_complete"
    assert any("Implicit join injection is deprecated" in record.message for record in caplog.records)


@pytest.mark.asyncio()
async def test_react_planner_parallel_plan_handles_branch_failure() -> None:
    AUDIT_CALLS.clear()
    client = StubClient(
        [
            {
                "thought": "fan out",
                "plan": [
                    {"node": "retrieve", "args": {"intent": "docs"}},
                    {"node": "broken", "args": {"intent": "docs"}},
                ],
                "join": {"node": "audit_parallel"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("retrieve", Intent, Documents)
    registry.register("broken", Intent, Documents)
    registry.register("audit_parallel", AuditArgs, Documents)

    nodes = [
        Node(retrieve, name="retrieve"),
        Node(broken, name="broken"),
        Node(audit_parallel, name="audit_parallel"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("parallel failure")

    assert result.reason == "answer_complete"
    step = result.metadata["steps"][0]
    branches = step["observation"]["branches"]
    failures = [entry for entry in branches if "error" in entry]
    assert len(failures) == 1
    assert "did not validate" in failures[0]["error"]

    join_info = step["observation"]["join"]
    assert join_info["status"] == "skipped"
    assert join_info["reason"] == "branch_failures"
    assert join_info["failures"][0]["node"] == "broken"
    assert AUDIT_CALLS == []


@pytest.mark.asyncio()
async def test_react_planner_parallel_plan_rejects_invalid_node() -> None:
    client = StubClient(
        [
            {
                "thought": "invalid",
                "plan": [{"node": "missing", "args": {}}],
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("respond", Answer, Answer)
    nodes = [Node(respond, name="respond")]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("invalid parallel plan")

    first_step = result.metadata["steps"][0]
    assert "Parallel plan invalid" in first_step["error"]


@pytest.mark.asyncio()
async def test_react_planner_deadline_enforcement() -> None:
    """Planner should respect deadline_s and return budget_exhausted."""
    # Provide enough responses so stub doesn't run out
    client = StubClient(
        [
            {
                "thought": "step1",
                "next_node": "triage",
                "args": {"question": "step1"},
            },
            {
                "thought": "step2",
                "next_node": "triage",
                "args": {"question": "step2"},
            },
            {
                "thought": "step3",
                "next_node": "triage",
                "args": {"question": "step3"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)

    # Use custom time source to control deadline precisely
    start_time = time.monotonic()

    def controlled_time() -> float:
        # After first call, advance past deadline
        if hasattr(controlled_time, "calls"):
            controlled_time.calls += 1
            if controlled_time.calls > 5:  # After a few calls, trigger deadline
                return start_time + 10.0  # Way past 0.01s deadline
        else:
            controlled_time.calls = 0
        return start_time

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(triage, name="triage")], registry),
        deadline_s=0.01,  # 10ms deadline
        max_iters=10,
        time_source=controlled_time,
    )

    result = await planner.run("Test deadline")

    assert result.reason == "budget_exhausted"
    assert result.metadata["constraints"]["deadline_triggered"] is True


@pytest.mark.asyncio()
async def test_react_planner_absolute_max_parallel_enforced() -> None:
    """System-level max_parallel should prevent resource exhaustion."""
    # Try to request more than the absolute limit
    excessive_plan = [
        {"node": "respond", "args": {"answer": f"branch_{i}"}}
        for i in range(100)  # Way over default limit of 50
    ]

    client = StubClient(
        [
            {"thought": "excessive", "plan": excessive_plan},
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("respond", Answer, Answer)
    nodes = [Node(respond, name="respond")]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        absolute_max_parallel=50,
    )

    result = await planner.run("test absolute limit")

    # Should have error about parallel limit in the first step
    steps = result.metadata["steps"]
    assert len(steps) > 0
    first_step = steps[0]
    # The error should be present
    assert first_step.get("error") is not None
    assert "50" in first_step["error"]


@pytest.mark.asyncio()
async def test_react_planner_event_callback_receives_events() -> None:
    """Event callback should receive all planner events."""

    events: list[PlannerEvent] = []

    def callback(event: PlannerEvent) -> None:
        events.append(event)

    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Test"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(triage, name="triage")], registry),
        event_callback=callback,
    )

    await planner.run("Test events")

    # Should have received events
    assert len(events) > 0

    event_types = {e.event_type for e in events}
    # Expect at least step_start, step_complete, finish
    assert "step_start" in event_types
    assert "step_complete" in event_types
    assert "finish" in event_types


@pytest.mark.asyncio()
async def test_react_planner_captures_stream_chunks() -> None:
    """Streaming chunks should be emitted as events and persisted."""

    chunk_events: list[dict[str, Any]] = []

    def event_callback(event: PlannerEvent) -> None:
        if event.event_type == "stream_chunk":
            chunk_events.append(dict(event.extra))

    @tool(desc="Stream partial answer")
    async def stream_tool(args: Query, ctx: Any) -> Answer:
        for i in range(5):
            await ctx.emit_chunk("test_stream", i, f"token_{i} ", done=i == 4)
        return Answer(answer="Complete")

    registry = ModelRegistry()
    registry.register("stream_tool", Query, Answer)

    client = StubClient(
        [
            {
                "thought": "stream",
                "next_node": "stream_tool",
                "args": {"question": "test"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "Complete"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(stream_tool, name="stream_tool")], registry),
        event_callback=event_callback,
    )

    result = await planner.run("Test streaming")

    assert len(chunk_events) == 5
    for index, event_payload in enumerate(chunk_events):
        assert event_payload["stream_id"] == "test_stream"
        assert event_payload["seq"] == index
        assert event_payload["text"] == f"token_{index} "
        assert event_payload["done"] == (index == 4)

    steps = result.metadata["steps"]
    assert len(steps) >= 1
    first_step_streams = steps[0]["streams"]["test_stream"]
    assert len(first_step_streams) == 5
    for index, chunk in enumerate(first_step_streams):
        assert chunk["seq"] == index
        assert chunk["text"] == f"token_{index} "
        assert chunk["done"] == (index == 4)


@pytest.mark.asyncio()
async def test_react_planner_streams_artifacts() -> None:
    """Artifact chunks should be emitted separately from text chunks."""
    artifact_events: list[dict[str, Any]] = []

    def event_callback(event: PlannerEvent) -> None:
        if event.event_type == "artifact_chunk":
            artifact_events.append(dict(event.extra))

    @tool(desc="Stream artifact data")
    async def artifact_tool(args: Query, ctx: Any) -> Answer:
        await ctx.emit_artifact("chart", {"step": 0})
        await ctx.emit_artifact(
            "chart",
            {"step": 1},
            done=True,
            artifact_type="chart_config",
        )
        return Answer(answer=f"complete:{args.question}")

    registry = ModelRegistry()
    registry.register("artifact_tool", Query, Answer)

    client = StubClient(
        [
            {
                "thought": "artifacts",
                "next_node": "artifact_tool",
                "args": {"question": "test"},
            },
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(artifact_tool, name="artifact_tool")], registry),
        event_callback=event_callback,
    )

    result = await planner.run("Test artifact streaming")

    assert len(artifact_events) == 2
    assert artifact_events[0]["stream_id"] == "chart"
    assert artifact_events[0]["seq"] == 0
    assert artifact_events[0]["chunk"]["step"] == 0
    assert artifact_events[1]["done"] is True
    assert artifact_events[1]["artifact_type"] == "chart_config"

    step_streams = result.metadata["steps"][0]["streams"]["chart"]
    assert step_streams[0]["chunk"]["step"] == 0
    assert step_streams[1]["done"] is True


def test_trajectory_serialisation_preserves_stream_chunks() -> None:
    """Trajectory serialisation should retain stream chunk history."""
    action = PlannerAction(
        thought="thinking",
        next_node="stream_tool",
        args={"question": "test"},
    )
    step = TrajectoryStep(
        action=action,
        streams={
            "test_stream": (
                {"seq": 0, "text": "token_0 ", "done": False},
                {"seq": 1, "text": "token_1 ", "done": True},
            )
        },
    )

    trajectory = Trajectory(query="Test streaming persistence")
    trajectory.steps.append(step)

    payload = trajectory.serialise()
    hydrated = Trajectory.from_serialised(payload)

    assert hydrated.steps, "Expected at least one step after hydration"
    hydrated_streams = hydrated.steps[0].streams
    assert hydrated_streams is not None
    assert "test_stream" in hydrated_streams
    hydrated_chunks = [dict(chunk) for chunk in hydrated_streams["test_stream"]]
    assert hydrated_chunks == [
        {"seq": 0, "text": "token_0 ", "done": False},
        {"seq": 1, "text": "token_1 ", "done": True},
    ]


@pytest.mark.asyncio()
async def test_react_planner_improved_token_estimation() -> None:
    """Token estimation should account for message structure overhead."""
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )

    planner = make_planner(client)

    # Create messages and estimate tokens
    messages = [
        {"role": "system", "content": "a" * 100},
        {"role": "user", "content": "b" * 100},
        {"role": "assistant", "content": "c" * 100},
    ]

    tokens = planner._estimate_size(messages)

    # Should be approximately (300 chars + overhead) / 3.5
    # Overhead = 3 * (role length + 20) ≈ 3 * 30 = 90
    # Total ≈ 390 / 3.5 ≈ 111 tokens
    assert 100 < tokens < 130


@pytest.mark.asyncio()
async def test_react_planner_state_store_save_error_handled_gracefully() -> None:
    """State store save errors should not crash pause operation."""

    class FailingSaver:
        def save_planner_state(self, token: str, payload: dict) -> None:
            raise RuntimeError("Storage failed")

    client = StubClient(
        [
            {
                "thought": "approval",
                "next_node": "approval",
                "args": {"intent": "docs"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("approval", Intent, Intent)
    nodes = [Node(approval_gate, name="approval")]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        pause_enabled=True,
        state_store=FailingSaver(),
    )

    # Should still pause successfully despite state store failure
    result = await planner.run("Test state store error")
    assert isinstance(result, PlannerPause)
    assert result.resume_token is not None


@pytest.mark.asyncio()
async def test_planner_tracks_llm_costs() -> None:
    """Planner should accumulate cost across main LLM calls."""

    client = CostStubClient(
        [
            (
                {
                    "thought": "Search",
                    "next_node": "search",
                    "args": {"question": "test"},
                },
                0.0015,
            ),
            (
                {"thought": "Done", "next_node": None, "args": {"raw_answer": "Result"}},
                0.0020,
            ),
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(search, name="search")], registry),
    )

    result = await planner.run("Test query")

    assert result.reason == "answer_complete"
    cost_info = result.metadata["cost"]
    assert cost_info["total_cost_usd"] == pytest.approx(0.0035)
    assert cost_info["main_llm_calls"] == 2
    assert cost_info["reflection_llm_calls"] == 0
    assert cost_info["summarizer_llm_calls"] == 0


@pytest.mark.asyncio()
async def test_cost_tracking_with_reflection_and_summarizer() -> None:
    """Costs should be tracked per call type, including reflection and summariser."""

    main_client = CostStubClient(
        [
            (
                {
                    "thought": "Search",
                    "next_node": "search",
                    "args": {"question": "test"},
                },
                0.001,
            ),
            (
                {
                    "thought": "Answer",
                    "next_node": None,
                    "args": {"raw_answer": "First"},
                },
                0.002,
            ),
            (
                {
                    "thought": "Revised",
                    "next_node": None,
                    "args": {"raw_answer": "Better"},
                },
                0.002,
            ),
        ]
    )

    reflection_client = CostStubClient(
        [
            (
                {
                    "score": 0.5,
                    "passed": False,
                    "feedback": "Bad",
                    "issues": [],
                    "suggestions": [],
                },
                0.0005,
            ),
            (
                {
                    "score": 0.9,
                    "passed": True,
                    "feedback": "Good",
                    "issues": [],
                    "suggestions": [],
                },
                0.0005,
            ),
        ]
    )

    summarizer_client = CostStubClient(
        [
            (
                {
                    "goals": ["stub"],
                    "facts": {},
                    "pending": [],
                    "last_output_digest": "stub",
                    "note": "stub",
                },
                0.0002,
            )
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=main_client,
        catalog=build_catalog([Node(search, name="search")], registry),
        reflection_config=ReflectionConfig(
            enabled=True,
            max_revisions=2,
            use_separate_llm=True,
        ),
        token_budget=1,
        reflection_llm="stub-reflection",
    )
    planner._reflection_client = reflection_client
    planner._summarizer_client = summarizer_client

    result = await planner.run("Test")

    cost_info = result.metadata["cost"]
    expected_total = pytest.approx(0.001 + 0.002 + 0.002 + 0.0005 + 0.0005 + 0.0002)
    assert cost_info["total_cost_usd"] == expected_total
    assert cost_info["main_llm_calls"] == 3
    assert cost_info["reflection_llm_calls"] == 2
    assert cost_info["summarizer_llm_calls"] == 1


@pytest.mark.asyncio()
async def test_cost_tracking_graceful_when_unavailable() -> None:
    """Planner should gracefully handle clients without cost support."""

    class NoCostClient:
        async def complete(
            self,
            *,
            messages: list[Mapping[str, str]],
            response_format: Mapping[str, object] | None = None,
            stream: bool = False,
            on_stream_chunk: object = None,
        ) -> str:
            del messages, response_format, stream, on_stream_chunk
            return json.dumps({"thought": "Done", "next_node": None, "args": {"raw_answer": "OK"}})

    planner = ReactPlanner(
        llm_client=NoCostClient(),
        catalog=build_catalog([], ModelRegistry()),
    )

    result = await planner.run("Test")

    cost_info = result.metadata["cost"]
    assert cost_info["total_cost_usd"] == 0.0
    assert cost_info["main_llm_calls"] == 1


def test_json_schema_sanitizer_removes_constraints():
    """Test that the JSON schema sanitizer removes advanced constraints for compatibility."""
    from penguiflow.planner.react import _sanitize_json_schema

    # Schema with unsupported constraints
    schema = {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "pattern": "^[a-z]+$",
                "format": "email",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 10,
                "uniqueItems": True,
            },
            "nested": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "minimum": 0,
                        "exclusiveMaximum": 100,
                    }
                },
            },
        },
        "required": ["score", "name"],
    }

    sanitized = _sanitize_json_schema(schema)

    # Verify top-level structure preserved
    assert sanitized["type"] == "object"
    assert "properties" in sanitized
    assert "required" in sanitized
    assert sanitized["required"] == ["score", "name"]

    # Verify number constraints removed
    score_schema = sanitized["properties"]["score"]
    assert score_schema["type"] == "number"
    assert "minimum" not in score_schema
    assert "maximum" not in score_schema

    # Verify string constraints removed
    name_schema = sanitized["properties"]["name"]
    assert name_schema["type"] == "string"
    assert "minLength" not in name_schema
    assert "maxLength" not in name_schema
    assert "pattern" not in name_schema
    assert "format" not in name_schema

    # Verify array constraints removed
    items_schema = sanitized["properties"]["items"]
    assert items_schema["type"] == "array"
    assert "items" in items_schema  # items definition preserved
    assert "minItems" not in items_schema
    assert "maxItems" not in items_schema
    assert "uniqueItems" not in items_schema

    # Verify nested constraints removed
    nested_count = sanitized["properties"]["nested"]["properties"]["count"]
    assert nested_count["type"] == "integer"
    assert "minimum" not in nested_count
    assert "exclusiveMaximum" not in nested_count


def test_json_schema_sanitizer_preserves_structure():
    """Test that sanitizer preserves essential schema structure."""
    from penguiflow.planner.react import _sanitize_json_schema

    schema = {
        "type": "object",
        "properties": {
            "data": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {
                    "value": {"type": "number"},
                },
                "required": ["value"],
            },
        },
        "required": ["data"],
        "additionalProperties": False,
    }

    sanitized = _sanitize_json_schema(schema)

    # All essential structure preserved
    assert sanitized == schema  # Should be identical since no constraints to remove
