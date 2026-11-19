"""Tests for ReAct planner."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.llm.types import ImagePart, TextPart
from penguiflow.node import Node
from penguiflow.planner import (
    PlannerEvent,
    PlannerFinish,
    PlannerPause,
    ReactPlanner,
)
from penguiflow.planner.guardrails import (
    GuardrailAction,
    GuardrailContext,
    GuardrailDecision,
    GuardrailEvent,
    RetrySpec,
)
from penguiflow.planner.guardrails.async_eval import AsyncRuleEvaluator
from penguiflow.planner.guardrails.gateway import (
    GatewayConfig,
    GuardrailGateway,
)
from penguiflow.planner.guardrails.registry import RuleRegistry
from penguiflow.planner.llm_context_hooks import LLMContextHook
from penguiflow.planner.memory import (
    MemoryBudget,
    MemoryKey,
    ShortTermMemoryConfig,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.planner.react import (
    PlannerAction,
    ReflectionConfig,
    Trajectory,
    TrajectoryStep,
)
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import (
    RichOutputConfig,
    attach_rich_output_nodes,
    configure_rich_output,
    reset_runtime,
)
from penguiflow.state.protocol import SupportsPlannerState
from penguiflow.steering.guard_inbox import InMemoryGuardInbox

from .state_store_doubles import RecordingStateStore

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from penguiflow.planner.context import ToolContext

    from .planner_options import PlannerOptions


def _extract_read_only_conversation_memory(
    messages: list[Mapping[str, str]],
) -> dict[str, Any] | None:
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if "<read_only_conversation_memory_json>" not in content:
            continue
        if "</read_only_conversation_memory_json>" not in content:
            continue
        start = content.index("<read_only_conversation_memory_json>") + len(
            "<read_only_conversation_memory_json>"
        )
        end = content.index("</read_only_conversation_memory_json>", start)
        parsed = json.loads(content[start:end])
        assert isinstance(parsed, dict)
        return parsed
    return None


def _extract_read_only_external_memory(
    messages: list[Mapping[str, str]],
) -> dict[str, Any] | None:
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if "<read_only_external_memory_json>" not in content:
            continue
        if "</read_only_external_memory_json>" not in content:
            continue
        start = content.index("<read_only_external_memory_json>") + len(
            "<read_only_external_memory_json>"
        )
        end = content.index("</read_only_external_memory_json>", start)
        parsed = json.loads(content[start:end])
        assert isinstance(parsed, dict)
        return parsed
    return None


class Query(BaseModel):
    """Query payload."""

    question: str


class Intent(BaseModel):
    """Intent payload."""

    intent: str


class Documents(BaseModel):
    """Documents payload."""

    documents: list[str]


class SearchResult(BaseModel):
    """Output of the search tool."""

    documents: list[str]


class Answer(BaseModel):
    """Answer payload."""

    answer: str


class ShardRequest(BaseModel):
    """Shard request payload."""

    topic: str
    shard: int


class ShardPayload(BaseModel):
    """Documents of one shard."""

    shard: int
    text: str


class MergeArgs(BaseModel):
    """Arguments of the merge tool."""

    expect: int
    results: list[ShardPayload]


class FlexibleMergeArgs(BaseModel):
    """Arguments of the flexible merge tool."""

    expected: int
    payloads: list[ShardPayload]
    branches: list[dict[str, Any]]
    success_total: int


class AuditArgs(BaseModel):
    """Arguments of the audit tool."""

    branches: list[dict[str, Any]]
    failures: list[dict[str, Any]]


class StrictArgs(BaseModel):
    """Arguments of the strict tool."""

    query: str


class StrictResult(BaseModel):
    """Output of the strict tool."""

    result: str


@tool(
    desc="Strict tool with placeholder validation",
    arg_validation={
        "emit_suspect": True,
        "reject_placeholders": True,
        "reject_autofill": False,
        "placeholders": ["<auto>"],
    },
)
async def strict_tool(args: StrictArgs, _ctx: object) -> StrictResult:
    """Accept only valid arguments."""
    return StrictResult(result=args.query)


@tool(desc="Detect intent", tags=["nlp"])
async def triage(_args: Query, _ctx: object) -> Intent:
    """Classify the question into an intent."""
    return Intent(intent="docs")


@tool(desc="Search knowledge base", side_effects="read")
async def retrieve(args: Intent, _ctx: object) -> Documents:
    """Return canned documents for the intent."""
    return Documents(documents=[f"Answering about {args.intent}"])


@tool(desc="Search knowledge base (cost tracking)")
async def search(args: Query, _ctx: object) -> SearchResult:
    """Return canned search results."""
    return SearchResult(documents=[f"Results for {args.question}"])


@tool(desc="Compose final answer")
async def respond(args: Answer, _ctx: object) -> Answer:
    """Compose the final answer."""
    return args


CONTEXT_CAPTURE: dict[str, Any] = {}
RESUME_CAPTURE: dict[str, Any] = {}


@tool(desc="Echo answer while recording contexts")
async def capture_answer(args: Answer, ctx: ToolContext) -> Answer:
    """Return the answer the planner passed in."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        meta_view = dict(ctx.meta)
    CONTEXT_CAPTURE["llm_context"] = dict(ctx.llm_context)
    CONTEXT_CAPTURE["tool_context"] = dict(ctx.tool_context)
    CONTEXT_CAPTURE["meta"] = meta_view
    CONTEXT_CAPTURE["meta_warnings"] = [
        warning
        for warning in caught
        if issubclass(warning.category, DeprecationWarning)
    ]
    return Answer(answer=args.answer)


@tool(desc="Return invalid documents")
async def broken(_args: Intent, _ctx: object) -> object:
    # Deliberately not a Documents instance: the planner must reject it.
    """Return an invalid result."""
    return "boom"


@tool(desc="Fetch documents from primary shard", tags=["parallel"])
async def fetch_primary(args: ShardRequest, _ctx: ToolContext) -> ShardPayload:
    """Fetch documents from the primary shard."""
    await asyncio.sleep(0.05)
    return ShardPayload(shard=args.shard, text=f"{args.topic}-primary")


@tool(desc="Fetch documents from secondary shard", tags=["parallel"])
async def fetch_secondary(
    args: ShardRequest, _ctx: ToolContext
) -> ShardPayload:
    """Fetch documents from the secondary shard."""
    await asyncio.sleep(0.05)
    return ShardPayload(shard=args.shard, text=f"{args.topic}-secondary")


@tool(desc="Merge shard payloads")
async def merge_results(args: MergeArgs, ctx: ToolContext) -> Documents:
    """Merge the documents of all shards."""
    assert ctx.meta.get("parallel_success_count") == args.expect
    assert len(ctx.meta.get("parallel_results", [])) == args.expect
    return Documents(documents=[item.text for item in args.results])


@tool(desc="Merge shard payloads with explicit inject mapping")
async def merge_results_explicit(
    args: FlexibleMergeArgs, _ctx: ToolContext
) -> Documents:
    """Merge shard results from explicit arguments."""
    assert args.success_total == args.expected == len(args.payloads)
    assert len(args.branches) == args.expected
    return Documents(documents=[item.text for item in args.payloads])


AUDIT_CALLS: list[dict[str, Any]] = []


@tool(desc="Audit failed branches")
async def audit_parallel(args: AuditArgs, _ctx: ToolContext) -> Documents:
    """Fail, so a parallel branch errors."""
    AUDIT_CALLS.append(args.model_dump())
    return Documents(documents=[f"{len(args.failures)} failures"])


@tool(desc="Approval required before proceeding")
async def approval_gate(args: Intent, ctx: ToolContext) -> Intent:
    """Pause until the user approves."""
    await ctx.pause("approval_required", {"intent": args.intent})
    return args


@tool(desc="Pause and record tool context")
async def pause_and_record(args: Intent, ctx: ToolContext) -> Intent:
    """Pause and record that the pause happened."""
    RESUME_CAPTURE.setdefault("calls", []).append(
        {
            "llm_context": dict(ctx.llm_context),
            "tool_context": dict(ctx.tool_context),
        }
    )
    await ctx.pause("approval_required", {"intent": args.intent})
    return args


@tool(desc="Respond while recording tool context")
async def respond_with_context(args: Answer, ctx: ToolContext) -> Answer:
    """Answer using the tool context."""
    RESUME_CAPTURE["resumed_tool_context"] = dict(ctx.tool_context)
    RESUME_CAPTURE["resumed_llm_context"] = dict(ctx.llm_context)
    return args


class PlannerTimeoutError(RuntimeError):
    """Planner timeout error raised by a test double."""

    def __init__(self, message: str, suggestion: str) -> None:
        """Store ``message`` and ``suggestion``."""
        super().__init__(message)
        self.suggestion = suggestion


@tool(desc="Remote fetch that may timeout", side_effects="external")
async def unstable(_args: Intent, _ctx: object) -> Documents:
    """Fail the first call, then answer."""
    msg = "upstream timeout"
    raise PlannerTimeoutError(msg, "use_cache")


@tool(desc="Use cached retrieval", side_effects="read")
async def cached(args: Intent, _ctx: object) -> Documents:
    """Return a cached answer."""
    return Documents(documents=[f"Cached docs for {args.intent}"])


class StubClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: Sequence[object]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, Any]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.calls.append(list(messages))
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


class SummarizerStub(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.calls: list[list[Mapping[str, Any]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
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


class CostStubClient(JSONLLMClient):
    """Stub client that also tracks synthetic cost values."""

    def __init__(
        self, responses: list[tuple[Mapping[str, object], float]]
    ) -> None:
        """Replay ``responses`` in order."""
        self._responses = [
            (json.dumps(payload, ensure_ascii=False), float(cost))
            for payload, cost in responses
        ]
        self.calls: list[list[Mapping[str, Any]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.calls.append(list(messages))
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0)


def make_planner(
    client: JSONLLMClient, **kwargs: Unpack[PlannerOptions]
) -> ReactPlanner:
    """Build the default test planner over ``client``."""
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


@pytest.mark.asyncio
async def test_arg_validation_emits_events_for_placeholders() -> None:
    """Arg validation emits events for placeholders."""
    registry = ModelRegistry()
    registry.register("strict_tool", StrictArgs, StrictResult)
    catalog = build_catalog([Node(strict_tool, name="strict_tool")], registry)

    responses: list[dict[str, object]] = [
        {
            "thought": "Call strict tool without required args",
            "next_node": "strict_tool",
            "args": {},
            "plan": None,
            "join": None,
        }
    ]
    events: list[PlannerEvent] = []

    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=catalog,
        max_iters=1,
        event_callback=events.append,
    )
    result = await planner.run("test strict validation")

    assert isinstance(result, PlannerFinish)
    assert result.metadata["args_invalid_count"] == 1
    assert isinstance(result, PlannerFinish)
    assert result.metadata["args_suspect_count"] == 1

    event_types = {evt.event_type for evt in events}
    assert "planner_args_invalid" in event_types
    assert "planner_args_suspect" in event_types

    assert isinstance(result, PlannerFinish)
    trajectory_meta = result.metadata.get("trajectory_metadata", {})
    invalid_args = trajectory_meta.get("invalid_args")
    assert isinstance(invalid_args, list)
    assert invalid_args and invalid_args[0]["tool"] == "strict_tool"


@pytest.mark.asyncio
async def test_finish_metadata_includes_answer_action_seq() -> None:
    """The final 'done' chunk carries the step_start action_seq."""
    events: list[PlannerEvent] = []
    planner = make_planner(
        StubClient(
            [
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "Done"},
                }
            ]
        ),
        max_iters=3,
        event_callback=events.append,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert isinstance(result.metadata.get("answer_action_seq"), int)
    assert result.metadata["answer_action_seq"] == 1

    step_start = next(
        (evt for evt in events if evt.event_type == "step_start"), None
    )
    assert step_start is not None
    assert (
        step_start.extra.get("action_seq")
        == result.metadata["answer_action_seq"]
    )


@pytest.mark.asyncio
async def test_malformed_json_finish_still_sets_answer_action_seq() -> None:
    """Fallback answers for malformed JSON still reach the stream."""

    class MalformedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    events: list[PlannerEvent] = []
    planner = make_planner(
        MalformedClient(
            [
                '{"next_node":"triage","args":{"question":"hi"}',  # missing }
                # missing }
                '{"next_node":"retrieve","args":{"intent":"docs"}',
                # missing }}
                '{"next_node":"final_response","args":{"raw_answer":"Done"}',
            ]
        ),
        repair_attempts=3,
        max_iters=3,
        event_callback=events.append,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "Done"
    assert result.metadata["answer_action_seq"] == 1

    step_start = next(
        (evt for evt in events if evt.event_type == "step_start"), None
    )
    assert step_start is not None
    assert (
        step_start.extra.get("action_seq")
        == result.metadata["answer_action_seq"]
    )


@pytest.mark.asyncio
async def test_finish_repair_fills_missing_answer() -> None:
    """When model finishes without an answer, finish_repair should fill it."""

    class FinishRepairClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    events: list[PlannerEvent] = []
    planner = make_planner(
        FinishRepairClient(
            [
                # Finish without answer (triggers finish_repair).
                '{"next_node":"final_response","args":{}}',
                # Finish repair response.
                '{"raw_answer":"Fixed"}',
            ]
        ),
        max_iters=1,
        event_callback=events.append,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "Fixed"
    assert any(evt.event_type == "finish_repair_attempt" for evt in events)
    assert any(evt.event_type == "finish_repair_success" for evt in events)
    assert any(
        evt.event_type == "llm_stream_chunk"
        and evt.extra.get("channel") == "answer"
        for evt in events
    )


@pytest.mark.asyncio
async def test_mixed_json_objects_output_executes_tool_call() -> None:
    """With several JSON objects in the output, run the action one."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    events: list[PlannerEvent] = []
    planner = make_planner(
        MixedClient(
            [
                # Tool call JSON, plus a second JSON object that previously
                # broke naive extraction.
                '{"next_node":"retrieve","args":{"intent":"docs"}}\n'
                '{"answer":"```json\\n{\\n  \\"next_node\\": '
                '\\"retrieve\\"\\n}\\n```"}',
                # Normal finish.
                '{"next_node":"final_response","args":{"answer":"ok"}}',
            ]
        ),
        max_iters=5,
        event_callback=events.append,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "ok"
    assert any(evt.event_type == "tool_call_start" for evt in events)
    assert any(evt.event_type == "step_complete" for evt in events)


@pytest.mark.asyncio
async def test_step_records_llm_parse_extras_in_trajectory_metadata() -> None:
    """Ensure parse extras are persisted for later inspection."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    planner = make_planner(
        MixedClient(
            [
                'PREFACE\n{"next_node":"retrieve","args":{"intent":"docs"}}\n{"foo":"bar"}\n',
                '{"next_node":"final_response","args":{"answer":"ok"}}',
            ]
        ),
        max_iters=5,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    trajectory_meta = result.metadata.get("trajectory_metadata", {})
    extras = trajectory_meta.get("llm_parse_extras")
    assert isinstance(extras, list)
    assert extras, "Expected at least one llm_parse_extras entry"
    assert extras[0].get("selected_next_node") == "retrieve"


@pytest.mark.asyncio
async def test_multi_action_falls_back_to_alternate_tool() -> None:
    """On failure, fall back to the next valid emitted tool action."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)
            self.calls: int = 0

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            self.calls += 1
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    events: list[PlannerEvent] = []
    client = MixedClient(
        [
            # First candidate is an unknown tool; second candidate is a real
            # tool.
            '{"next_node":"not_a_real_tool","args":{"x":1}}\\n{"next_node":"triage","args":{"question":"hi"}}',
            '{"next_node":"final_response","args":{"answer":"ok"}}',
        ]
    )
    planner = make_planner(client, max_iters=5, event_callback=events.append)
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "ok"
    assert any(
        evt.event_type == "multi_action_fallback_used" for evt in events
    )
    tool_starts = [
        evt for evt in events if evt.event_type == "tool_call_start"
    ]
    assert tool_starts and tool_starts[0].extra.get("tool_name") == "triage"


@pytest.mark.asyncio
async def test_multi_action_runs_extra_read_only_tools() -> None:
    """Extra tool calls from one response run in sequence if enabled."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)
            self.calls: int = 0

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            self.calls += 1
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    events: list[PlannerEvent] = []
    client = MixedClient(
        [
            # Two tool calls in one response (triage + retrieve).
            # final_response is ignored and requested next.
            '{"next_node":"triage","args":{"question":"hi"}}\\n'
            '{"next_node":"retrieve","args":{"intent":"docs"}}\\n'
            '{"next_node":"final_response","args":{"answer":"premature"}}',
            '{"next_node":"final_response","args":{"answer":"ok"}}',
        ]
    )
    planner = make_planner(
        client,
        max_iters=10,
        event_callback=events.append,
        multi_action_sequential=True,
        multi_action_read_only_only=True,
        multi_action_max_tools=2,
    )
    result = await planner.run("hi")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "ok"
    # Only two LLM calls: first mixed output, then final answer (retrieve was
    # executed from queue).
    assert client.calls == 2
    tool_names = [
        evt.extra.get("tool_name")
        for evt in events
        if evt.event_type == "tool_call_start"
    ]
    assert tool_names == ["triage", "retrieve"]


@pytest.mark.asyncio
async def test_multi_action_skips_render_report_wrapper() -> None:
    """Render wrappers stay blocked as alternate actions."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)
            self.calls: int = 0

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            self.calls += 1
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    reset_runtime()
    config = RichOutputConfig(enabled=True, allowlist=["report"])
    configure_rich_output(config)

    registry = ModelRegistry()
    registry.register("retrieve", Intent, Documents)
    nodes = [
        *list(attach_rich_output_nodes(registry, config=config)),
        Node(retrieve, name="retrieve"),
    ]
    catalog = build_catalog(nodes, registry)

    events: list[PlannerEvent] = []
    client = MixedClient(
        [
            '{"next_node":"retrieve","args":{"intent":"docs"}}\n'
            '{"next_node":"render_report","args":{"title":"Status","sections":[{"title":"Summary","content":"ok"}]}}',
            '{"next_node":"final_response","args":{"answer":"ok"}}',
        ]
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=10,
        event_callback=events.append,
        multi_action_sequential=True,
        multi_action_read_only_only=True,
        multi_action_max_tools=2,
    )
    result = await planner.run("show a report")

    assert result.reason == "answer_complete"
    tool_names = [
        evt.extra.get("tool_name")
        for evt in events
        if evt.event_type == "tool_call_start"
    ]
    assert tool_names == ["retrieve"]
    assert not any(evt.event_type == "multi_action_enqueued" for evt in events)


@pytest.mark.asyncio
async def test_multi_action_sequential_can_enqueue_build_table_wrapper() -> (
    None
):
    """Silent builder tools can run as alternate actions."""

    class MixedClient(JSONLLMClient):
        def __init__(self, raw_responses: list[str]) -> None:
            self._responses = list(raw_responses)
            self.calls: int = 0

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            self.calls += 1
            if not self._responses:
                msg = "No stub responses left"
                raise AssertionError(msg)
            return self._responses.pop(0), 0.0

    reset_runtime()
    config = RichOutputConfig(enabled=True, allowlist=["datagrid"])
    configure_rich_output(config)

    registry = ModelRegistry()
    registry.register("retrieve", Intent, Documents)
    nodes = [
        *list(attach_rich_output_nodes(registry, config=config)),
        Node(retrieve, name="retrieve"),
    ]
    catalog = build_catalog(nodes, registry)

    events: list[PlannerEvent] = []
    client = MixedClient(
        [
            '{"next_node":"retrieve","args":{"intent":"docs"}}\n'
            '{"next_node":"build_table","args":{"columns":[{"field":"name","header":"Name"}],"rows":[{"name":"ok"}]}}',
            '{"next_node":"final_response","args":{"answer":"ok"}}',
        ]
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=10,
        event_callback=events.append,
        multi_action_sequential=True,
        multi_action_read_only_only=True,
        multi_action_max_tools=2,
    )
    result = await planner.run("prepare a reusable table")

    assert result.reason == "answer_complete"
    tool_names = [
        evt.extra.get("tool_name")
        for evt in events
        if evt.event_type == "tool_call_start"
    ]
    assert tool_names == ["retrieve", "build_table"]
    assert any(evt.event_type == "multi_action_enqueued" for evt in events)


@pytest.mark.asyncio
async def test_consecutive_arg_failures_force_finish() -> None:
    """Test that consecutive arg validation failures trigger early finish."""
    registry = ModelRegistry()
    registry.register("strict_tool", StrictArgs, StrictResult)
    catalog = build_catalog([Node(strict_tool, name="strict_tool")], registry)

    # LLM keeps sending invalid args with placeholder values
    responses = [
        {
            "thought": "First try with placeholder",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},  # Placeholder - will be rejected
        },
        {
            "thought": "Second try with placeholder",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},  # Still placeholder
        },
        {
            "thought": "Third try with placeholder",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},  # Threshold reached
        },
    ]
    events: list[PlannerEvent] = []

    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=catalog,
        max_iters=10,  # High limit to prove threshold works
        max_consecutive_arg_failures=3,
        event_callback=events.append,
    )
    result = await planner.run("test consecutive failures")

    # Should finish early due to threshold, not max_iters
    assert result.reason == "no_path"
    assert result.metadata.get("requires_followup") is True
    assert result.metadata["consecutive_arg_failures"] == 3

    # Payload should indicate why we stopped
    assert result.payload.get("requires_followup") is True
    assert result.payload.get("failure_reason") == "consecutive_arg_failures"
    assert result.payload.get("tool") == "strict_tool"


@pytest.mark.asyncio
async def test_consecutive_arg_failures_reset_on_success() -> None:
    """The consecutive failure counter resets after a success.

    Note: Per-tool counters are tracked separately and only reset when THAT
        tool succeeds.
    This test validates the global counter reset behavior using a threshold
    high enough to not trigger the per-tool force_finish (strict_tool fails
    twice = 2, threshold = 3).
    """
    registry = ModelRegistry()
    registry.register("strict_tool", StrictArgs, StrictResult)
    registry.register("triage", Query, Intent)
    catalog = build_catalog(
        [
            Node(strict_tool, name="strict_tool"),
            Node(triage, name="triage"),
        ],
        registry,
    )

    responses = [
        # First failure
        {
            "thought": "Try with placeholder",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},
        },
        # Successful call resets global counter (but per-tool counter for
        # strict_tool stays)
        {
            "thought": "Call triage instead",
            "next_node": "triage",
            "args": {"question": "real query"},
        },
        # New failure - global counter starts fresh, per-tool increments
        {
            "thought": "Try placeholder again",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},
        },
        # Finish normally
        {
            "thought": "Give up and finish",
            "next_node": None,
            "args": {"raw_answer": "Done"},
        },
    ]

    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=catalog,
        max_iters=10,
        # Threshold 3 so per-tool counter (2 for strict_tool) doesn't trigger
        # force_finish
        max_consecutive_arg_failures=3,
    )
    result = await planner.run("test reset behavior")

    # Should complete normally - global counter was reset by successful triage
    # call
    assert result.reason == "answer_complete"
    # Final global consecutive count should be 1 (reset by triage success)
    assert result.metadata["consecutive_arg_failures"] == 1
    # Note: per-tool counter (consecutive_arg_failures_strict_tool = 2) is
    # tracked internally but not exposed in result metadata


@pytest.mark.asyncio
async def test_per_tool_arg_failures_force_finish() -> None:
    """A per-tool failure limit ends the run even if others succeed.

    This prevents infinite loops where the model repeatedly fails on one tool
    but the global counter keeps getting reset by successful calls to other
    tools. When the threshold is reached, the model is asked to generate a
    user-friendly response instead of returning a technical error.
    """
    registry = ModelRegistry()
    registry.register("strict_tool", StrictArgs, StrictResult)
    registry.register("triage", Query, Intent)
    catalog = build_catalog(
        [
            Node(strict_tool, name="strict_tool"),
            Node(triage, name="triage"),
        ],
        registry,
    )

    responses = [
        # First failure on strict_tool
        {
            "thought": "Try with placeholder",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},
        },
        # Successful call to triage - resets global but not per-tool
        {
            "thought": "Call triage instead",
            "next_node": "triage",
            "args": {"question": "real query"},
        },
        # Second failure on strict_tool - per-tool counter = 2, hits threshold
        {
            "thought": "Try placeholder again",
            "next_node": "strict_tool",
            "args": {"query": "<auto>"},
        },
        # Graceful failure response - model provides user-friendly message
        {
            "raw_answer": "I apologize, but I'm having "
            "trouble completing that "
            "action. Let me help you in another way."
        },
    ]

    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=catalog,
        max_iters=10,
        # Threshold 2 so per-tool counter triggers graceful failure
        max_consecutive_arg_failures=2,
    )
    result = await planner.run("test per-tool force finish")

    # Should gracefully finish with a user-friendly message instead of no_path
    assert result.reason == "answer_complete"
    assert result.payload.get("graceful_failure") is True
    assert result.payload.get("failure_reason") == "consecutive_arg_failures"
    assert "apologize" in result.payload.get("raw_answer", "").lower()
    # Global counter was reset by triage success (only 1 failure since reset)
    assert result.metadata["consecutive_arg_failures"] == 1


@pytest.mark.asyncio
async def test_autofill_rejection_force_finish_after_retry() -> None:
    """Autofill then reject allows one retry, then forces a finish."""
    registry = ModelRegistry()
    registry.register("strict_tool", StrictArgs, StrictResult)
    catalog = build_catalog([Node(strict_tool, name="strict_tool")], registry)

    # Model keeps forgetting 'query' arg - autofill injects default, validation
    # rejects
    responses = [
        {
            "thought": "First call without query",
            "next_node": "strict_tool",
            "args": {},  # Missing 'query' - will be autofilled then rejected
        },
        {
            "thought": "Second call still without query",
            "next_node": "strict_tool",
            # Still missing - second autofill rejection = force finish
            "args": {},
        },
    ]
    events: list[PlannerEvent] = []

    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=catalog,
        max_iters=10,
        # Disable arg-fill to test pure autofill rejection
        arg_fill_enabled=False,
        event_callback=events.append,
    )
    result = await planner.run("test autofill rejection")

    # Should force finish after 2nd autofill rejection (gave one chance)
    assert result.reason == "no_path"
    assert result.metadata.get("requires_followup") is True
    assert result.payload.get("failure_reason") == "autofill_rejection"
    assert "query" in (result.payload.get("missing_fields") or [])


@pytest.mark.asyncio()
async def test_react_planner_runs_end_to_end() -> None:
    """ReAct planner runs end to end."""
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
    """ReAct planner collects sources into payload."""

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
    async def retrieve_sources(
        _args: SourceArgs, _ctx: ToolContext
    ) -> SourceResult:
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
    """Tool context separation and meta warning."""
    CONTEXT_CAPTURE.clear()
    client = StubClient(
        [
            {
                "thought": "capture",
                "next_node": "capture",
                "args": {"answer": "ok"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "ok"},
            },
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

    result = await planner.run(
        "Check context separation", llm_context=llm_ctx, tool_context=tool_ctx
    )

    assert result.reason == "answer_complete"
    assert CONTEXT_CAPTURE["llm_context"] == llm_ctx
    assert CONTEXT_CAPTURE["tool_context"]["hidden"] == "secret"
    assert CONTEXT_CAPTURE["tool_context"]["sentinel"] is sentinel
    assert CONTEXT_CAPTURE["meta"]["visible"] == "memories"
    assert CONTEXT_CAPTURE["meta"]["hidden"] == "secret"
    assert any(
        issubclass(warning.category, DeprecationWarning)
        for warning in CONTEXT_CAPTURE["meta_warnings"]
    )
    first_user_messages = [
        msg["content"] for msg in client.calls[0] if msg.get("role") == "user"
    ]
    assert any("memories" in content for content in first_user_messages)
    assert all("secret" not in content for content in first_user_messages)


@pytest.mark.asyncio()
async def test_react_planner_injects_short_term_memory_across_runs() -> None:
    """ReAct planner injects short term memory across runs."""
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            },
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
async def test_planner_injects_external_memory_hook() -> None:
    """React planner injects external memory hook as system message."""

    class Hook(LLMContextHook):
        def __init__(self) -> None:
            self.calls = 0

        @override
        async def before_run(self, inp: Any) -> Mapping[str, Any]:
            self.calls += 1
            assert inp.query == "q1"
            return {"external_memory": {"foo": "bar"}}

    hook = Hook()
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "ok"},
            }
        ]
    )
    planner = make_planner(client, llm_context_hooks=[hook])

    await planner.run("q1")

    assert hook.calls == 1
    first_call = client.calls[0]
    external = _extract_read_only_external_memory(first_call)
    assert external == {"foo": "bar"}

    # Ensure external_memory is NOT embedded in the user JSON context.
    first_user = next(msg for msg in first_call if msg.get("role") == "user")
    payload = json.loads(first_user["content"])
    ctx = payload.get("context") or {}
    assert "external_memory" not in ctx


@pytest.mark.asyncio()
async def test_react_planner_external_memory_hook_not_called_on_resume() -> (
    None
):
    """ReAct planner external memory hook not called on resume."""

    class Hook(LLMContextHook):
        def __init__(self) -> None:
            self.calls = 0

        @override
        async def before_run(self, inp: Any) -> Mapping[str, Any]:
            self.calls += 1
            return {"external_memory": {"foo": "bar"}}

    @tool(desc="Pause tool")
    async def pause_tool(_args: Query, ctx: ToolContext) -> Intent:
        await ctx.pause("await_input", {"need": "user"})
        return Intent(intent="paused")

    hook = Hook()
    registry = ModelRegistry()
    registry.register("pause_tool", Query, Intent)
    registry.register("respond", Answer, Answer)
    catalog = build_catalog(
        [Node(pause_tool, name="pause_tool"), Node(respond, name="respond")],
        registry,
    )
    client = StubClient(
        [
            {
                "thought": "pause",
                "next_node": "pause_tool",
                "args": {"question": "x"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = ReactPlanner(
        llm_client=client, catalog=catalog, llm_context_hooks=[hook]
    )

    paused = await planner.run("q1")
    assert isinstance(paused, PlannerPause)
    resumed = await planner.resume(paused.resume_token, user_input="ok")
    assert resumed.reason == "answer_complete"
    assert hook.calls == 1

    # The resume LLM call should still include the original external memory
    # system message.
    resumed_messages = client.calls[1]
    external = _extract_read_only_external_memory(resumed_messages)
    assert external == {"foo": "bar"}


@pytest.mark.asyncio()
async def test_react_planner_rolling_summary_uses_llm_summarizer() -> None:
    """ReAct planner rolling summary uses LLM summarizer."""

    class Client(JSONLLMClient):
        def __init__(self) -> None:
            self._actions = [
                {
                    "thought": "finish",
                    "next_node": None,
                    "args": {"raw_answer": "a1"},
                },
                {
                    "thought": "finish",
                    "next_node": None,
                    "args": {"raw_answer": "a2"},
                },
            ]
            self.summary_calls = 0
            self.calls: list[tuple[str | None, list[Mapping[str, str]]]] = []

        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> tuple[str, float]:
            schema_name = None
            if isinstance(response_format, Mapping):
                json_schema = response_format.get("json_schema")
                if isinstance(json_schema, Mapping):
                    schema_name = json_schema.get("name")
            self.calls.append(
                (
                    schema_name if isinstance(schema_name, str) else None,
                    list(messages),
                )
            )

            if schema_name == "short_term_memory_summary":
                self.summary_calls += 1
                return json.dumps(
                    {"summary": "<session_summary>ok</session_summary>"}
                ), 0.0

            if not self._actions:
                msg = "No stub responses left"
                raise AssertionError(msg)
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
    memory = planner.memory_by_key[key.composite()]
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
    """ReAct planner extracts memory key from tool context."""
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            },
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
    """ReAct planner persists and hydrates short term memory."""

    class Store(RecordingStateStore):
        """State store that also keeps memory state, in a dict."""

        def __init__(self) -> None:
            super().__init__()
            self.data: dict[str, dict[str, Any]] = {}

        async def save_memory_state(
            self, key: str, state: dict[str, Any]
        ) -> None:
            self.data[key] = state

        async def load_memory_state(self, key: str) -> dict[str, Any] | None:
            return self.data.get(key)

    store = Store()
    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")

    client1 = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            }
        ]
    )
    planner1 = make_planner(
        client1,
        state_store=store,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    await planner1.run("q1", memory_key=key)

    client2 = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            }
        ]
    )
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
    """ReAct planner memory fail closed without key."""
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            },
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
    """ReAct planner session isolation by memory key."""
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            },
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
    """LLM context must be JSON serialisable."""
    planner = make_planner(StubClient([]))
    with pytest.raises(TypeError):
        await planner.run("invalid", llm_context={"bad": object()})


@pytest.mark.asyncio()
async def test_react_planner_recovers_from_invalid_node() -> None:
    """ReAct planner recovers from invalid node."""
    client = StubClient(
        [
            {"thought": "invalid", "next_node": "missing", "args": {}},
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What?"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test invalid node")

    assert result.reason == "answer_complete"
    assert any("missing" in step["error"] for step in result.metadata["steps"])


@pytest.mark.asyncio()
async def test_react_planner_autofills_missing_args() -> None:
    """Test that missing required string args trigger arg-fill recovery.

    When the model omits required args, the planner:
    1. Autofills with placeholder values (e.g., "<auto>" for strings)
    2. Detects the placeholder and rejects it (triggers arg validation error)
    3. Attempts arg-fill to get proper values from the model
    4. Merges filled values and proceeds with tool execution
    """
    client = StubClient(
        [
            # Model provides empty args - triggers autofill with <auto>
            # placeholder
            {"thought": "retrieve", "next_node": "retrieve", "args": {}},
            # Arg-fill response - model provides the missing "intent" value
            {"intent": "docs"},
            # Model finishes
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "ok"},
            },
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test autofill path")

    # Should complete successfully after arg-fill recovery
    assert result.reason == "answer_complete"
    # First step should have observation (tool was called with arg-fill values)
    steps = result.metadata["steps"]
    assert len(steps) >= 1
    assert steps[0]["observation"] is not None
    # Verify arg-fill was used (not just silent autofill acceptance)
    assert result.metadata.get("arg_fill_success_count", 0) >= 1


@pytest.mark.asyncio()
async def test_react_planner_reports_output_validation_error() -> None:
    """ReAct planner reports output validation error."""
    client = StubClient(
        [
            {
                "thought": "broken",
                "next_node": "broken",
                "args": {"intent": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "fallback"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("broken", Intent, Documents)
    catalog = build_catalog([Node(broken, name="broken")], registry)
    planner = ReactPlanner(llm_client=client, catalog=catalog)

    result = await planner.run("Test output validation path")

    assert isinstance(result, PlannerFinish)
    errors = [
        step["error"] for step in result.metadata["steps"] if step["error"]
    ]
    assert any("returned data" in err for err in errors)


@pytest.mark.asyncio()
async def test_react_planner_replans_after_tool_failure() -> None:
    """ReAct planner replans after tool failure."""
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
    assert failure_prompt["failure"]["error_code"] == "PlannerTimeoutError"


def test_react_planner_requires_catalog_or_nodes() -> None:
    """ReAct planner requires catalog or nodes."""
    client = StubClient([])
    with pytest.raises(ValueError):
        ReactPlanner(llm_client=client)


def test_react_planner_requires_llm_or_client() -> None:
    """ReAct planner requires LLM or client."""
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    with pytest.raises(ValueError):
        ReactPlanner(nodes=nodes, registry=registry)


@pytest.mark.asyncio()
async def test_react_planner_iteration_limit_returns_no_path() -> None:
    """ReAct planner iteration limit returns no path."""
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
    """ReAct planner enforces hop budget limits."""
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
    """ReAct planner LiteLLM guard raises runtime error."""
    litellm = pytest.importorskip("litellm")

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    planner = ReactPlanner(llm="dummy", nodes=nodes, registry=registry)
    trajectory = Trajectory(query="hi")
    # When litellm is installed, it raises BadRequestError for invalid model
    # names
    with pytest.raises(
        (RuntimeError, litellm.exceptions.BadRequestError)
    ) as exc:
        await planner.step(trajectory)
    # Accept either error message
    assert "LiteLLM is not installed" in str(
        exc.value
    ) or "LLM Provider NOT provided" in str(exc.value)


@pytest.mark.asyncio()
async def test_react_planner_step_repairs_invalid_action() -> None:
    # Use input that cannot be salvaged - not valid JSON at all
    """ReAct planner step repairs invalid action."""
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
    repair_idx = next(
        i
        for i, msg in enumerate(client.calls[1])
        if "invalid JSON" in (msg.get("content") or "")
    )
    first_non_system_idx = next(
        i
        for i, msg in enumerate(client.calls[1])
        if msg.get("role") != "system"
    )
    repair_message = client.calls[1][repair_idx]["content"]
    assert repair_idx < first_non_system_idx
    assert "invalid JSON" in repair_message


@pytest.mark.asyncio()
async def test_guardrail_retry_message_is_system_prefixed() -> None:
    """React planner guardrail retry corrective message is system prefixed."""

    class _RetryGateway(GuardrailGateway):
        """Gateway whose every evaluation asks for one corrective retry."""

        @override
        async def evaluate(
            self, ctx: GuardrailContext, event: GuardrailEvent
        ) -> GuardrailDecision:
            return GuardrailDecision(
                action=GuardrailAction.RETRY,
                rule_id="retry-rule",
                reason="retry",
                retry=RetrySpec(
                    max_attempts=2,
                    corrective_message="Use strict JSON output.",
                ),
            )

    client = StubClient(
        [
            {
                "thought": "recover",
                "next_node": "triage",
                "args": {"question": "fixed"},
            },
        ]
    )
    planner = make_planner(client)
    rules = RuleRegistry()
    planner.guardrail_gateway = _RetryGateway(
        registry=rules,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(rules)),
        config=GatewayConfig(mode="enforce"),
    )
    planner.guardrail_context = GuardrailContext(run_id="run-1")
    trajectory = Trajectory(query="recover")

    action = await planner.step(trajectory)
    assert action.next_node == "triage"

    first_call = client.calls[0]
    corrective_idx = next(
        i
        for i, msg in enumerate(first_call)
        if "Use strict JSON output." in (msg.get("content") or "")
    )
    first_non_system_idx = next(
        i for i, msg in enumerate(first_call) if msg.get("role") != "system"
    )
    assert corrective_idx < first_non_system_idx


@pytest.mark.asyncio()
async def test_finish_invalid_numeric_answer_falls_back() -> None:
    """Finish invalid numeric answer falls back to default message."""
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"answer": -0.0}},
            {"note": "still missing answer"},
        ]
    )
    events: list[PlannerEvent] = []
    planner = make_planner(client, event_callback=events.append)

    result = await planner.run("numeric finish edge case")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "No answer produced."
    assert result.payload["raw_answer"] != "-0.0"
    assert any(
        evt.event_type == "finish_invalid_answer_type_discarded"
        for evt in events
    )


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
    # Empty JSON should be salvaged to a finish action.
    assert action.next_node == "final_response"
    assert action.thought == "planning next step"
    assert len(client.calls) == 1  # No repair needed


@pytest.mark.asyncio()
async def test_react_planner_compacts_history_when_budget_exceeded() -> None:
    """ReAct planner compacts history when budget exceeded."""
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
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = make_planner(client, token_budget=180)

    result = await planner.run("Explain budget handling")

    assert result.reason == "answer_complete"
    assert any(
        msg["role"] == "system" and "Trajectory summary" in msg["content"]
        for msg in client.calls[1]
    )


@pytest.mark.asyncio()
async def test_react_planner_input_parts_survive_messages_and_summary() -> (
    None
):
    """ReAct planner input parts survive messages and summary."""
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What is in the image?"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = make_planner(client, token_budget=120)
    image_bytes = b"\x89PNG\r\nphase3"

    result = await planner.run(
        "Describe the image",
        input_parts=[
            ImagePart(data=image_bytes, media_type="image/png", detail="low")
        ],
    )

    assert result.reason == "answer_complete"
    first_user = next(msg for msg in client.calls[0] if msg["role"] == "user")
    assert isinstance(first_user["content"], list)
    assert isinstance(first_user["content"][0], TextPart)
    assert isinstance(first_user["content"][1], ImagePart)
    summary_message = next(
        msg
        for msg in client.calls[1]
        if "Trajectory summary" in str(msg["content"])
    )
    summary_text = str(summary_message["content"])
    assert "image/png" in summary_text
    assert "bytes" in summary_text
    assert "phase3" not in summary_text


@pytest.mark.asyncio()
async def test_react_planner_invokes_summarizer_client() -> None:
    """ReAct planner invokes summarizer client."""
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
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "ok"},
            },
        ]
    )
    planner = make_planner(client, token_budget=60)
    summarizer = SummarizerStub()
    planner.summarizer_client = summarizer

    await planner.run("Trigger summariser")

    assert summarizer.calls, "Expected summarizer to be invoked"


@pytest.mark.asyncio()
async def test_react_planner_pause_and_resume_flow() -> None:
    """ReAct planner pause and resume flow."""
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
    planner = ReactPlanner(
        llm_client=client, catalog=catalog, pause_enabled=True
    )

    pause_result = await planner.run("Share metrics with approval")
    assert isinstance(pause_result, PlannerPause)
    assert pause_result.reason == "approval_required"

    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
    )
    assert resume_result.reason == "answer_complete"

    post_pause_calls = client.calls[2:]
    assert any(
        "Resume input" in msg["content"]
        for call in post_pause_calls
        for msg in call
    )


@pytest.mark.asyncio()
async def test_resume_accepts_tool_context_override() -> None:
    """Resume accepts tool context override."""
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
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    planner = ReactPlanner(
        llm_client=client, catalog=catalog, pause_enabled=True
    )

    pause_result = await planner.run(
        "Need approval",
        llm_context={"user": "demo"},
        tool_context={"initial": "one"},
    )
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
    """ReAct planner resume preserves hop budget."""
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
    assert any(
        step.get("error") and "Hop budget" in step["error"] for step in steps
    ), "expected hop budget violation after resume"

    constraints = resume_result.metadata["constraints"]
    assert constraints["hops_used"] == 1
    assert constraints["hop_exhausted"] is True


@pytest.mark.asyncio()
async def test_react_planner_disallows_nodes_from_hints() -> None:
    """ReAct planner disallows nodes from hints."""
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
    planner = make_planner(
        client, planning_hints={"disallow_nodes": ["broken"]}
    )

    result = await planner.run("test hints")

    assert result.reason == "answer_complete"
    assert any(
        msg["role"] == "user" and "not permitted" in msg["content"]
        for msg in client.calls[1]
    )


@pytest.mark.asyncio()
async def test_react_planner_emits_ordering_hint_once() -> None:
    """ReAct planner emits ordering hint once."""
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
    assert any(
        msg["role"] == "user" and "Ordering hint" in msg["content"]
        for msg in client.calls[1]
    )


@pytest.mark.asyncio()
async def test_react_planner_parallel_plan_executes_concurrently() -> None:
    """ReAct planner parallel plan executes concurrently."""
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
async def test_parallel_plan_tool_call_events_with_join() -> None:
    """Parallel branches emit tool_call_* events for live UIs."""
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

    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        event_callback=events.append,
    )

    result = await planner.run("parallel fan out")
    assert result.reason == "answer_complete"

    tool_starts = [
        evt for evt in events if evt.event_type == "tool_call_start"
    ]
    tool_results = [
        evt for evt in events if evt.event_type == "tool_call_result"
    ]
    assert len(tool_starts) == 3
    assert len(tool_results) == 3
    assert {evt.extra.get("tool_name") for evt in tool_starts} == {
        "fetch_primary",
        "fetch_secondary",
        "merge_results",
    }


@pytest.mark.asyncio()
async def test_parallel_plan_tool_call_events_without_join() -> None:
    """Parallel plan without join emits tool call events for branches."""
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

    nodes = [
        Node(fetch_primary, name="fetch_primary"),
        Node(fetch_secondary, name="fetch_secondary"),
    ]

    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        event_callback=events.append,
    )

    result = await planner.run("parallel fan out")
    assert result.reason == "answer_complete"

    tool_starts = [
        evt for evt in events if evt.event_type == "tool_call_start"
    ]
    tool_results = [
        evt for evt in events if evt.event_type == "tool_call_result"
    ]
    assert len(tool_starts) == 2
    assert len(tool_results) == 2
    assert {evt.extra.get("tool_name") for evt in tool_starts} == {
        "fetch_primary",
        "fetch_secondary",
    }
    assert "join" not in result.metadata["steps"][0]["observation"]


@pytest.mark.asyncio()
async def test_steering_user_message_is_consumed_after_next_llm_call() -> None:
    """A steering USER_MESSAGE is injected once, not every step."""
    from penguiflow.planner.react_runtime import run_loop
    from penguiflow.state.models import SteeringEvent, SteeringEventType
    from penguiflow.steering import SteeringInbox

    class EchoArgs(BaseModel):
        text: str

    class EchoOut(BaseModel):
        echoed: str

    @tool(desc="Echo tool", side_effects="read")
    async def echo(args: EchoArgs, _ctx: object) -> EchoOut:
        return EchoOut(echoed=args.text)

    client = StubClient(
        [
            {"next_node": "echo", "args": {"text": "hi"}},
            {
                "next_node": "final_response",
                "args": {"answer": "done", "raw_answer": "done"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(echo, name="echo")], registry),
    )

    inbox = SteeringInbox()
    planner.steering = inbox
    await inbox.push(
        SteeringEvent(
            session_id="s",
            task_id="t",
            event_type=SteeringEventType.USER_MESSAGE,
            payload={"text": "do this once"},
            source="user",
        )
    )

    trajectory = Trajectory(query="test", llm_context={}, tool_context={})
    result = await run_loop(
        planner, trajectory, tracker=None, error_recovery_config=None
    )
    assert result.reason == "answer_complete"

    assert len(client.calls) >= 2
    first_call = client.calls[0]
    second_call = client.calls[1]
    assert any(
        "Steering input:" in msg.get("content", "") for msg in first_call
    )
    assert not any(
        "Steering input:" in msg.get("content", "") for msg in second_call
    )


@pytest.mark.asyncio()
async def test_rich_output_prompt_injected_with_renderer() -> None:
    """Rich output prompt is injected when render component is present."""
    from penguiflow.rich_output.runtime import (
        RichOutputConfig,
        attach_rich_output_nodes,
        configure_rich_output,
        reset_runtime,
    )

    reset_runtime()
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            include_prompt_catalog=True,
            allowlist=["markdown", "report"],
        )
    )

    registry = ModelRegistry()
    nodes = list(
        attach_rich_output_nodes(
            registry,
            config=RichOutputConfig(
                enabled=True, allowlist=["markdown", "report"]
            ),
        )
    )
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [{"next_node": "final_response", "args": {"raw_answer": "ok"}}]
    )
    planner = ReactPlanner(
        llm="gpt-oss-120b", llm_client=client, catalog=catalog
    )

    assert "# Rich Output Components" in planner.system_prompt


@pytest.mark.asyncio()
async def test_react_planner_parallel_join_explicit_inject_mapping() -> None:
    """ReAct planner parallel join explicit inject mapping."""
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
    """ReAct planner parallel join magic injection warns."""
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
    assert any(
        "Implicit join injection is deprecated" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio()
async def test_react_planner_parallel_plan_handles_branch_failure() -> None:
    """ReAct planner parallel plan handles branch failure."""
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
    """ReAct planner parallel plan rejects invalid node."""
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

    assert isinstance(result, PlannerFinish)
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

    calls = 0

    def controlled_time() -> float:
        # After a few calls, jump way past the 0.01s deadline.
        nonlocal calls
        calls += 1
        if calls > 6:
            return start_time + 10.0
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
    assert isinstance(result, PlannerFinish)
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
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
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
async def test_react_planner_emits_tool_call_events() -> None:
    """Tool execution should emit tool_call_start/end/result events."""
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
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
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

    tool_events = [e for e in events if e.event_type.startswith("tool_call")]
    assert {e.event_type for e in tool_events} == {
        "tool_call_start",
        "tool_call_end",
        "tool_call_result",
    }

    start_event = next(
        e for e in tool_events if e.event_type == "tool_call_start"
    )
    end_event = next(e for e in tool_events if e.event_type == "tool_call_end")
    result_event = next(
        e for e in tool_events if e.event_type == "tool_call_result"
    )

    assert start_event.extra["tool_name"] == "triage"
    assert (
        start_event.extra["tool_call_id"]
        == end_event.extra["tool_call_id"]
        == result_event.extra["tool_call_id"]
    )
    assert "args_json" in start_event.extra
    assert "result_json" in result_event.extra


def test_react_planner_registers_resource_callbacks() -> None:
    """ToolNode resource updates should emit resource_updated events."""
    events: list[PlannerEvent] = []

    def callback(event: PlannerEvent) -> None:
        events.append(event)

    class FakeToolNode:
        def __init__(self) -> None:
            self.callback: Callable[[str], None] | None = None

        def set_resource_updated_callback(
            self, cb: Callable[[str], None]
        ) -> None:
            self.callback = cb

    fake_node = FakeToolNode()

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    spec = build_catalog([Node(triage, name="triage")], registry)[0]
    spec = spec.__class__(
        node=spec.node,
        name=spec.name,
        desc=spec.desc,
        args_model=spec.args_model,
        out_model=spec.out_model,
        side_effects=spec.side_effects,
        tags=spec.tags,
        auth_scopes=spec.auth_scopes,
        cost_hint=spec.cost_hint,
        latency_hint_ms=spec.latency_hint_ms,
        safety_notes=spec.safety_notes,
        extra={"tool_node": fake_node, "namespace": "fake"},
    )

    ReactPlanner(
        llm_client=StubClient(
            [
                {
                    "thought": "finish",
                    "next_node": None,
                    "args": {"raw_answer": "done"},
                }
            ]
        ),
        catalog=[spec],
        event_callback=callback,
    )

    assert fake_node.callback is not None
    fake_node.callback("file:///test.txt")

    assert any(event.event_type == "resource_updated" for event in events)


@pytest.mark.asyncio()
async def test_react_planner_captures_stream_chunks() -> None:
    """Streaming chunks should be emitted as events and persisted."""
    chunk_events: list[dict[str, Any]] = []

    def event_callback(event: PlannerEvent) -> None:
        if event.event_type == "stream_chunk":
            chunk_events.append(dict(event.extra))

    @tool(desc="Stream partial answer")
    async def stream_tool(_args: Query, ctx: ToolContext) -> Answer:
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
        catalog=build_catalog(
            [Node(stream_tool, name="stream_tool")], registry
        ),
        event_callback=event_callback,
    )

    result = await planner.run("Test streaming")

    assert len(chunk_events) == 5
    for index, event_payload in enumerate(chunk_events):
        assert event_payload["stream_id"] == "test_stream"
        assert event_payload["seq"] == index
        assert event_payload["text"] == f"token_{index} "
        assert event_payload["done"] == (index == 4)

    assert isinstance(result, PlannerFinish)
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
    async def artifact_tool(args: Query, ctx: ToolContext) -> Answer:
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
            [Node(artifact_tool, name="artifact_tool")], registry
        ),
        event_callback=event_callback,
    )

    result = await planner.run("Test artifact streaming")

    assert len(artifact_events) == 2
    assert artifact_events[0]["stream_id"] == "chart"
    assert artifact_events[0]["seq"] == 0
    assert artifact_events[0]["chunk"]["step"] == 0
    assert artifact_events[1]["done"] is True
    assert artifact_events[1]["artifact_type"] == "chart_config"

    assert isinstance(result, PlannerFinish)
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
    hydrated_chunks = [
        dict(chunk) for chunk in hydrated_streams["test_stream"]
    ]
    assert hydrated_chunks == [
        {"seq": 0, "text": "token_0 ", "done": False},
        {"seq": 1, "text": "token_1 ", "done": True},
    ]


@pytest.mark.asyncio()
async def test_react_planner_improved_token_estimation() -> None:
    """Token estimation should account for message structure overhead."""
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )

    planner = make_planner(client)

    # Create messages and estimate tokens
    messages = [
        {"role": "system", "content": "a" * 100},
        {"role": "user", "content": "b" * 100},
        {"role": "assistant", "content": "c" * 100},
    ]

    tokens = planner.estimate_size(messages)

    # Should be approximately (300 chars + overhead) / 3.5
    # Overhead = 3 * (role length + 20) ≈ 3 * 30 = 90
    # Total ≈ 390 / 3.5 ≈ 111 tokens
    assert 100 < tokens < 130


@pytest.mark.asyncio()
async def test_react_planner_state_store_save_error_handled_gracefully() -> (
    None
):
    """State store save errors should not crash pause operation."""

    class FailingSaver(RecordingStateStore, SupportsPlannerState):
        """State store whose planner-state saves always fail."""

        @override
        async def save_planner_state(
            self, token: str, payload: dict[str, Any]
        ) -> None:
            msg = "Storage failed"
            raise RuntimeError(msg)

        @override
        async def load_planner_state(
            self, token: str
        ) -> dict[str, Any] | None:
            return None

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
                {
                    "thought": "Done",
                    "next_node": None,
                    "args": {"raw_answer": "Result"},
                },
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
    """Costs are tracked per call type, reflection included."""
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
    planner.reflection_client = reflection_client
    planner.summarizer_client = summarizer_client

    result = await planner.run("Test")

    assert isinstance(result, PlannerFinish)
    cost_info = result.metadata["cost"]
    expected_total = pytest.approx(
        0.001 + 0.002 + 0.002 + 0.0005 + 0.0005 + 0.0002
    )
    assert cost_info["total_cost_usd"] == expected_total
    assert cost_info["main_llm_calls"] == 3
    assert cost_info["reflection_llm_calls"] == 2
    assert cost_info["summarizer_llm_calls"] == 1


@pytest.mark.asyncio()
async def test_cost_tracking_graceful_when_unavailable() -> None:
    """Planner should gracefully handle clients without cost support."""

    class NoCostClient(JSONLLMClient):
        @override
        async def complete(
            self,
            *,
            messages: Sequence[Mapping[str, Any]],
            response_format: Mapping[str, Any] | None = None,
            stream: bool = False,
            on_stream_chunk: Callable[[str, bool], None] | None = None,
        ) -> str:
            return json.dumps(
                {
                    "thought": "Done",
                    "next_node": None,
                    "args": {"raw_answer": "OK"},
                }
            )

    planner = ReactPlanner(
        llm_client=NoCostClient(),
        catalog=build_catalog([], ModelRegistry()),
    )

    result = await planner.run("Test")

    assert isinstance(result, PlannerFinish)
    cost_info = result.metadata["cost"]
    assert cost_info["total_cost_usd"] == 0.0
    assert cost_info["main_llm_calls"] == 1


def test_json_schema_sanitizer_removes_constraints() -> None:
    """The schema sanitizer removes advanced constraints."""
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


def test_json_schema_sanitizer_preserves_structure() -> None:
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
    assert (
        sanitized == schema
    )  # Should be identical since no constraints to remove
