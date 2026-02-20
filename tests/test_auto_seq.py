from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerAction, ReactPlanner, ToolPolicy, Trajectory, TrajectoryStep
from penguiflow.planner.react_runtime import _detect_deterministic_transition, _emit_auto_seq_detection_event
from penguiflow.registry import ModelRegistry


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


class CountArgs(BaseModel):
    count: int


class CountOut(BaseModel):
    count: int


class UpperArgs(BaseModel):
    text: str


class UpperOut(BaseModel):
    result: str


class NoOpArgs(BaseModel):
    ok: bool = True


class NoOpOut(BaseModel):
    ok: bool = True


@tool(desc="Echo text.", extra={"auto_seq": True})
async def echo(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text)


@tool(desc="Shout text.", extra={"auto_seq": True})
async def shout(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text.upper())


@tool(desc="Count items.", extra={"auto_seq": False})
async def count_items(args: CountArgs, ctx: Any) -> CountOut:
    _ = ctx
    return CountOut(count=args.count)


@tool(desc="Uppercase text.", extra={"auto_seq": True, "auto_seq_execute": True})
async def uppercase(args: UpperArgs, ctx: Any) -> UpperOut:
    _ = ctx
    return UpperOut(result=args.text.upper())


class StubClient:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        del response_format, stream, on_stream_chunk
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


def _build_planner(nodes: list[Node], *, event_callback: Any | None = None) -> ReactPlanner:
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
    return ReactPlanner(llm="stub-llm", catalog=catalog, event_callback=event_callback)


def _trajectory_with_step(action: PlannerAction, observation: Any) -> Trajectory:
    trajectory = Trajectory(query="test")
    trajectory.steps.append(TrajectoryStep(action=action, observation=observation))
    return trajectory


def test_auto_seq_detector_skips_non_mapping_observation() -> None:
    planner = _build_planner([Node(echo, name="echo")])
    trajectory = _trajectory_with_step(PlannerAction(next_node="echo"), observation="raw text")

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "skipped"
    assert result.reason == "non_structured_observation"


def test_auto_seq_detector_skips_after_parallel_action() -> None:
    planner = _build_planner([Node(echo, name="echo")])
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="parallel", args={"steps": []}),
        observation={"text": "hello"},
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "skipped"
    assert result.reason == "previous_step_parallel"


def test_auto_seq_detector_returns_none_when_no_candidates() -> None:
    planner = _build_planner([Node(count_items, name="count_items")])
    trajectory = _trajectory_with_step(
        PlannerAction(next_node="count_items"),
        observation={"count": 3},
    )

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "none"


def test_auto_seq_detector_returns_ambiguous_when_multiple_candidates() -> None:
    planner = _build_planner([Node(echo, name="echo"), Node(shout, name="shout")])
    trajectory = _trajectory_with_step(PlannerAction(next_node="echo"), observation={"text": "hi"})

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "ambiguous"
    assert set(result.candidates or []) == {"echo", "shout"}


def test_auto_seq_detector_returns_unique_action_with_validated_args() -> None:
    planner = _build_planner([Node(echo, name="echo"), Node(count_items, name="count_items")])
    trajectory = _trajectory_with_step(PlannerAction(next_node="echo"), observation={"text": "hey"})

    result = _detect_deterministic_transition(planner, trajectory)

    assert result.status == "unique"
    assert result.selected_action is not None
    assert result.selected_action.next_node == "echo"
    assert result.selected_action.args == {"text": "hey"}


def test_auto_seq_emits_unique_detection_event() -> None:
    events: list[Any] = []
    planner = _build_planner([Node(echo, name="echo")], event_callback=events.append)
    trajectory = _trajectory_with_step(PlannerAction(next_node="echo"), observation={"text": "hey"})

    result = _detect_deterministic_transition(planner, trajectory)
    _emit_auto_seq_detection_event(planner, trajectory, result)

    detection_events = [event for event in events if event.event_type == "auto_seq_detected_unique"]
    assert len(detection_events) == 1
    event = detection_events[0]
    assert "payload_fingerprint" in event.extra
    assert "payload_keys_count" in event.extra
    assert "payload_type" in event.extra
    assert "payload" not in event.extra


def test_auto_seq_emits_ambiguous_detection_event_with_tool_names() -> None:
    events: list[Any] = []
    planner = _build_planner(
        [Node(echo, name="echo"), Node(shout, name="shout")],
        event_callback=events.append,
    )
    trajectory = _trajectory_with_step(PlannerAction(next_node="echo"), observation={"text": "hi"})

    result = _detect_deterministic_transition(planner, trajectory)
    _emit_auto_seq_detection_event(planner, trajectory, result)

    detection_events = [event for event in events if event.event_type == "auto_seq_detected_ambiguous"]
    assert len(detection_events) == 1
    event = detection_events[0]
    assert event.extra.get("candidate_count") == 2
    assert set(event.extra.get("candidates", [])) == {"echo", "shout"}


@pytest.mark.asyncio()
async def test_auto_seq_detection_does_not_reduce_llm_calls_when_auto_exec_disabled() -> None:
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "echo", "next_node": "echo", "args": {"text": "first"}},
            {"thought": "count", "next_node": "count_items", "args": {"count": 1}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("count_items", CountArgs, CountOut)
    catalog = build_catalog([Node(echo, name="echo"), Node(count_items, name="count_items")], registry)
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
        in {"auto_seq_detected_unique", "auto_seq_detected_ambiguous", "auto_seq_detected_none", "auto_seq_skipped"}
    ]
    assert detection_events


@tool(desc="Echo with upper payload.", extra={"auto_seq": False})
async def echo_upper(args: EchoArgs, ctx: Any) -> UpperArgs:
    _ = ctx
    return UpperArgs(text=args.text)


@pytest.mark.asyncio()
async def test_auto_seq_auto_exec_reduces_llm_calls_by_one() -> None:
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "echo", "next_node": "echo_upper", "args": {"text": "hi"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("echo_upper", EchoArgs, UpperArgs)
    registry.register("uppercase", UpperArgs, UpperOut)
    catalog = build_catalog([Node(echo_upper, name="echo_upper"), Node(uppercase, name="uppercase")], registry)
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        event_callback=events.append,
        auto_seq_enabled=True,
        auto_seq_execute=True,
    )

    await planner.run("Test auto-exec")

    assert len(client.calls) == 2
    tool_events = [event for event in events if event.event_type == "tool_call_start"]
    assert [event.extra.get("tool_name") for event in tool_events] == ["echo_upper", "uppercase"]
    assert any(event.event_type == "auto_seq_executed" for event in events)


@tool(desc="Uppercase text (no auto-exec).", extra={"auto_seq": True})
async def uppercase_no_exec(args: UpperArgs, ctx: Any) -> UpperOut:
    _ = ctx
    return UpperOut(result=args.text.upper())


@tool(desc="No-op tool.", extra={"auto_seq": True, "auto_seq_execute": True})
async def noop(args: NoOpArgs, ctx: Any) -> NoOpOut:
    _ = ctx
    return NoOpOut(ok=args.ok)


@pytest.mark.asyncio()
async def test_auto_seq_auto_exec_requires_tool_opt_in() -> None:
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "echo", "next_node": "echo_upper", "args": {"text": "hi"}},
            {"thought": "uppercase", "next_node": "uppercase_no_exec", "args": {"text": "hi"}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("echo_upper", EchoArgs, UpperArgs)
    registry.register("uppercase_no_exec", UpperArgs, UpperOut)
    catalog = build_catalog(
        [Node(echo_upper, name="echo_upper"), Node(uppercase_no_exec, name="uppercase_no_exec")],
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
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "noop", "next_node": "noop", "args": {"ok": True}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
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

    class HideAllTools:
        def visible_tools(self, specs: Sequence[Any], tool_context: Mapping[str, Any]) -> Sequence[Any]:
            _ = specs
            _ = tool_context
            return []

    await planner.run("Test visibility", tool_visibility=HideAllTools())

    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_respects_tool_policy_filtering() -> None:
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "noop", "next_node": "noop", "args": {"ok": True}},
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
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
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
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
    trajectory.metadata["pending_actions"] = [{"next_node": "noop", "args": {"ok": True}}]
    trajectory.steps.append(TrajectoryStep(action=PlannerAction(next_node="noop"), observation={"ok": True}))

    await planner._run_loop(trajectory, tracker=None)

    assert any(event.event_type == "tool_call_start" and event.extra.get("tool_name") == "noop" for event in events)
    assert not any(event.event_type == "auto_seq_executed" for event in events)


@pytest.mark.asyncio()
async def test_auto_seq_never_auto_executes_after_parallel() -> None:
    events: list[Any] = []
    client = StubClient(
        [
            {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
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

    await planner._run_loop(trajectory, tracker=None)

    assert not any(event.event_type == "auto_seq_executed" for event in events)
