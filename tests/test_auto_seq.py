"""Tests for auto seq."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    PlannerAction,
    ReactPlanner,
    ToolPolicy,
    Trajectory,
    TrajectoryStep,
)
from penguiflow.planner.models import JSONLLMClient, ToolVisibilityPolicy
from penguiflow.planner.react_runtime import (
    _detect_deterministic_transition,
    _emit_auto_seq_detection_event,
)
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner import PlannerEventCallback
    from penguiflow.planner.context import ToolContext


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    text: str


class CountArgs(BaseModel):
    """Arguments of the count tool."""

    count: int


class CountOut(BaseModel):
    """Output of the count tool."""

    count: int


class UpperArgs(BaseModel):
    """Arguments of the upper tool."""

    text: str


class UpperOut(BaseModel):
    """Output of the upper tool."""

    result: str


class NoOpArgs(BaseModel):
    """Arguments of the no-op tool."""

    ok: bool = True


class NoOpOut(BaseModel):
    """Output of the no-op tool."""

    ok: bool = True


@tool(desc="Echo text.", extra={"auto_seq": True})
async def echo(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Echo the input back."""
    return EchoOut(text=args.text)


@tool(desc="Shout text.", extra={"auto_seq": True})
async def shout(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Return the input text upper-cased."""
    return EchoOut(text=args.text.upper())


@tool(desc="Count items.", extra={"auto_seq": False})
async def count_items(args: CountArgs, _ctx: ToolContext) -> CountOut:
    """Count the input items."""
    return CountOut(count=args.count)


@tool(
    desc="Uppercase text.", extra={"auto_seq": True, "auto_seq_execute": True}
)
async def uppercase(args: UpperArgs, _ctx: ToolContext) -> UpperOut:
    """Return the input text upper-cased."""
    return UpperOut(result=args.text.upper())


class StubClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, str]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        self.calls.append(list(messages))
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _build_planner(
    nodes: list[Node], *, event_callback: PlannerEventCallback | None = None
) -> ReactPlanner:
    registry = ModelRegistry()
    for node in nodes:
        node_name = node.name or node.func.__name__
        if node_name == "echo" or node_name == "shout":
            registry.register(node_name, EchoArgs, EchoOut)
        elif node_name == "count_items":
            registry.register(node_name, CountArgs, CountOut)
        elif node_name == "uppercase":
            registry.register(node_name, UpperArgs, UpperOut)
    catalog = build_catalog(nodes, registry)
    return ReactPlanner(
        llm="stub-llm", catalog=catalog, event_callback=event_callback
    )


def _trajectory_with_step(
    action: PlannerAction, observation: str | dict[str, str] | dict[str, int]
) -> Trajectory:
    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(action=action, observation=observation)
    )
    return trajectory


def test_auto_seq_detector_skips_non_mapping_observation() -> None:
    """Auto seq detector skips non mapping observation."""
    planner = _build_planner([Node(echo, name="echo")])
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="echo"), observation="raw text"
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "skipped"
    assert result.reason == "non_structured_observation"


def test_auto_seq_detector_skips_after_parallel_action() -> None:
    """Auto seq detector skips after parallel action."""
    planner = _build_planner([Node(echo, name="echo")])
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="parallel", args={"steps": []}),
        observation={"text": "hello"},
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "skipped"
    assert result.reason == "previous_step_parallel"


def test_auto_seq_detector_returns_none_when_no_candidates() -> None:
    """Auto seq detector returns none when no candidates."""
    planner = _build_planner([Node(count_items, name="count_items")])
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="count_items"),
        observation={"count": 3},
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "none"


def test_auto_seq_detector_returns_ambiguous_when_multiple_candidates() -> (
    None
):
    """Auto seq detector returns ambiguous when multiple candidates."""
    planner = _build_planner(
        [Node(echo, name="echo"), Node(shout, name="shout")]
    )
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="echo"), observation={"text": "hi"}
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "ambiguous"
    assert set(result.candidates or []) == {"echo", "shout"}


def test_auto_seq_detector_returns_unique_action_with_validated_args() -> None:
    """Auto seq detector returns unique action with validated args."""
    planner = _build_planner(
        [Node(echo, name="echo"), Node(count_items, name="count_items")]
    )
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="echo"), observation={"text": "hey"}
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "unique"
    assert result.selected_action is not None
    assert result.selected_action.next_node == "echo"
    assert result.selected_action.args == {"text": "hey"}


def test_auto_seq_emits_unique_detection_event() -> None:
    """Auto seq emits unique detection event."""
    events: list[Any] = []
    planner = _build_planner(
        [Node(echo, name="echo")], event_callback=events.append
    )
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="echo"), observation={"text": "hey"}
    )

    result = _detect_deterministic_transition(planner, trajectory)
    _emit_auto_seq_detection_event(planner, trajectory, result)

    detection_events = [
        event
        for event in events
        if event.event_type == "auto_seq_detected_unique"
    ]
    assert len(detection_events) == 1
    event = detection_events[0]
    assert "payload_fingerprint" in event.extra
    assert "payload_keys_count" in event.extra
    assert "payload_type" in event.extra
    assert "payload" not in event.extra


def test_auto_seq_emits_ambiguous_detection_event_with_tool_names() -> None:
    """Auto seq emits ambiguous detection event with tool names."""
    events: list[Any] = []
    planner = _build_planner(
        [Node(echo, name="echo"), Node(shout, name="shout")],
        event_callback=events.append,
    )
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="echo"), observation={"text": "hi"}
    )

    result = _detect_deterministic_transition(planner, trajectory)
    _emit_auto_seq_detection_event(planner, trajectory, result)

    detection_events = [
        event
        for event in events
        if event.event_type == "auto_seq_detected_ambiguous"
    ]
    assert len(detection_events) == 1
    event = detection_events[0]
    assert event.extra.get("candidate_count") == 2
    assert set(event.extra.get("candidates", [])) == {"echo", "shout"}


@pytest.mark.asyncio()
async def test_auto_seq_detection_alone_keeps_llm_calls() -> None:
    """Auto seq detection does not reduce llm calls when auto exec disabled."""
    events: list[Any] = []
    client = StubClient(
        [
            {
                "thought": "echo",
                "next_node": "echo",
                "args": {"text": "first"},
            },
            {
                "thought": "count",
                "next_node": "count_items",
                "args": {"count": 1},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("count_items", CountArgs, CountOut)
    catalog = build_catalog(
        [Node(echo, name="echo"), Node(count_items, name="count_items")],
        registry,
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=False,
    )

    await planner.run("Test auto-seq detection")

    assert len(client.calls) == 3
    detection_events = [
        event
        for event in events
        if event.event_type
        in {
            "auto_seq_detected_unique",
            "auto_seq_detected_ambiguous",
            "auto_seq_detected_none",
            "auto_seq_skipped",
        }
    ]
    assert detection_events


@tool(desc="Echo with upper payload.", extra={"auto_seq": False})
async def echo_upper(args: EchoArgs, _ctx: ToolContext) -> UpperArgs:
    """Echo the input upper-cased."""
    return UpperArgs(text=args.text)


@pytest.mark.asyncio()
async def test_auto_seq_auto_exec_reduces_llm_calls_by_one() -> None:
    """Auto seq auto exec reduces LLM calls by one."""
    events: list[Any] = []
    client = StubClient(
        [
            {
                "thought": "echo",
                "next_node": "echo_upper",
                "args": {"text": "hi"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("echo_upper", EchoArgs, UpperArgs)
    registry.register("uppercase", UpperArgs, UpperOut)
    catalog = build_catalog(
        [
            Node(echo_upper, name="echo_upper"),
            Node(uppercase, name="uppercase"),
        ],
        registry,
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )

    await planner.run("Test auto-exec")

    assert len(client.calls) == 2
    tool_events = [
        event for event in events if event.event_type == "tool_call_start"
    ]
    assert [event.extra.get("tool_name") for event in tool_events] == [
        "echo_upper",
        "uppercase",
    ]
    assert any(event.event_type == "auto_seq_executed" for event in events)


@tool(desc="Uppercase text (no auto-exec).", extra={"auto_seq": True})
async def uppercase_no_exec(args: UpperArgs, _ctx: ToolContext) -> UpperOut:
    """Upper-case the input (never executed)."""
    return UpperOut(result=args.text.upper())


@tool(desc="No-op tool.", extra={"auto_seq": True, "auto_seq_execute": True})
async def noop(args: NoOpArgs, _ctx: ToolContext) -> NoOpOut:
    """Do nothing and return an empty result."""
    return NoOpOut(ok=args.ok)


@pytest.mark.asyncio()
async def test_auto_seq_auto_exec_requires_tool_opt_in() -> None:
    """Auto seq auto exec requires tool opt in."""
    events: list[Any] = []
    client = StubClient(
        [
            {
                "thought": "echo",
                "next_node": "echo_upper",
                "args": {"text": "hi"},
            },
            {
                "thought": "uppercase",
                "next_node": "uppercase_no_exec",
                "args": {"text": "hi"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("echo_upper", EchoArgs, UpperArgs)
    registry.register("uppercase_no_exec", UpperArgs, UpperOut)
    catalog = build_catalog(
        [
            Node(echo_upper, name="echo_upper"),
            Node(uppercase_no_exec, name="uppercase_no_exec"),
        ],
        registry,
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )

    await planner.run("Test auto-exec tool opt-in")

    assert len(client.calls) == 3
    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_respects_tool_visibility_scope() -> None:
    """Auto seq respects tool visibility scope."""
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "noop", "next_node": "noop", "args": {"ok": True}},
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("noop", NoOpArgs, NoOpOut)
    catalog = build_catalog([Node(noop, name="noop")], registry)
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )

    class HideAllTools(ToolVisibilityPolicy):
        @override
        def visible_tools(
            self, specs: Sequence[Any], tool_context: Mapping[str, Any]
        ) -> Sequence[Any]:
            return []

    await planner.run("Test visibility", tool_visibility=HideAllTools())

    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_respects_tool_policy_filtering() -> None:
    """Auto seq respects tool policy filtering."""
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "noop", "next_node": "noop", "args": {"ok": True}},
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("noop", NoOpArgs, NoOpOut)
    catalog = build_catalog([Node(noop, name="noop")], registry)
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
        tool_policy=ToolPolicy(denied_tools={"noop"}),
    )

    await planner.run("Test tool policy")

    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_skips_when_pending_actions_present() -> None:
    """Auto seq skips when pending actions present."""
    events: list[Any] = []
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("noop", NoOpArgs, NoOpOut)
    catalog = build_catalog([Node(noop, name="noop")], registry)
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )
    trajectory = Trajectory(query="Test pending")
    trajectory.metadata["pending_actions"] = [
        {"next_node": "noop", "args": {"ok": True}}
    ]
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(next_node="noop"), observation={"ok": True}
        )
    )

    await planner.run_loop(trajectory, tracker=None)

    assert any(
        event.event_type == "tool_call_start"
        and event.extra.get("tool_name") == "noop"
        for event in events
    )
    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_never_auto_executes_after_parallel() -> None:
    """Auto seq never auto executes after parallel."""
    events: list[Any] = []
    client = StubClient(
        [
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "done"},
            },
        ]
    )
    registry = ModelRegistry()
    registry.register("noop", NoOpArgs, NoOpOut)
    catalog = build_catalog([Node(noop, name="noop")], registry)
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )
    trajectory = Trajectory(query="Test parallel")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(next_node="parallel", args={"steps": []}),
            observation={"ok": True},
        )
    )

    await planner.run_loop(trajectory, tracker=None)

    assert not any(event.event_type == "auto_seq_executed" for event in events)
