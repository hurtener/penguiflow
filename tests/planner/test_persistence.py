"""Tests for fire-and-forget trajectory and event persistence."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

import penguiflow.planner.react_runtime as rt_mod
from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    PlannerEvent,
    PlannerFinish,
    PlannerPause,
    ReactPlanner,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.state.in_memory import InMemoryStateStore

from ..state_store_doubles import RecordingStateStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext
    from penguiflow.planner.trajectory import Trajectory
    from penguiflow.state import StateStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain_persistence_tasks() -> None:
    """Wait for all fire-and-forget persistence tasks to complete."""
    tasks = [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("penguiflow-persist")
    ]
    if tasks:
        await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Tool definitions for test scaffolding
# ---------------------------------------------------------------------------


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    answer: str


@tool(desc="echo")
async def echo(args: EchoArgs, _ctx: ToolContext) -> dict[str, str]:
    """Echo the input back."""
    return {"answer": args.text}


class PauseArgs(BaseModel):
    """Arguments of the pause tool."""

    prompt: str


class PauseOut(BaseModel):
    """Output of the pause tool."""

    ok: bool


@tool(desc="Trigger a pause")
async def pause_tool(args: PauseArgs, ctx: ToolContext) -> PauseOut:
    """Pause the run for user input."""
    await ctx.pause("await_input", {"prompt": args.prompt})
    return PauseOut(ok=True)


# ---------------------------------------------------------------------------
# Scripted LLM clients
# ---------------------------------------------------------------------------


class ScriptedClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


class PauseClient(JSONLLMClient):
    """Scripted client: pause_tool, then echo after resume, then finish."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.calls = 0

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                '{"thought":"pause","next_node":"pause_tool",'
                '"args":{"prompt":"Confirm?"}}'
            )
        if self.calls == 2:
            return (
                '{"thought":"echo after '
                'resume","next_node":"echo","args":{"text":"resumed"}}'
            )
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_echo_planner(
    store: StateStore,
) -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    return ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
        state_store=store,
    )


def _make_pause_planner(
    store: InMemoryStateStore,
) -> tuple[ReactPlanner, PauseClient]:
    registry = ModelRegistry()
    registry.register("pause_tool", PauseArgs, PauseOut)
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog(
        [Node(pause_tool, name="pause_tool"), Node(echo, name="echo")],
        registry,
    )
    client = PauseClient()
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=4,
        state_store=store,
        pause_enabled=True,
    )
    return planner, client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_persists_trajectory() -> None:
    """Run persists trajectory."""
    store = InMemoryStateStore()
    planner = _make_echo_planner(store)
    result = await planner.run(
        "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None
    assert traj.query == "hi"


async def test_run_persists_events() -> None:
    """Run persists events."""
    store = InMemoryStateStore()
    planner = _make_echo_planner(store)
    await planner.run(
        "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    await _drain_persistence_tasks()

    events = await store.list_planner_events("t1")
    assert len(events) >= 1


async def test_pause_persists_trajectory() -> None:
    """Pause persists trajectory."""
    store = InMemoryStateStore()
    planner, _ = _make_pause_planner(store)
    result = await planner.run(
        "pause please", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(result, PlannerPause)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None


async def test_no_state_store_no_error() -> None:
    """No state store no error."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
    )
    result = await planner.run(
        "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()
    # No assertions on store -- just verifying no error occurred


async def test_missing_methods_silently_skipped() -> None:
    """Missing methods silently skipped."""

    class BareStore(RecordingStateStore):
        """A store that has neither save_trajectory nor save_planner_event."""

    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
        state_store=BareStore(),
    )
    result = await planner.run(
        "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()


async def test_resume_persists_trajectory_and_events() -> None:
    """Resume persists trajectory and events."""
    store = InMemoryStateStore()
    planner, _ = _make_pause_planner(store)

    # Phase 1: Run -> PlannerPause
    pause_result = await planner.run(
        "pause please", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(pause_result, PlannerPause)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None

    # Phase 2: Resume -> PlannerFinish
    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="confirmed",
        tool_context={"session_id": "s1", "trace_id": "t1"},
    )
    assert isinstance(resume_result, PlannerFinish)
    await _drain_persistence_tasks()

    # Trajectory updated after resume (more steps)
    traj_after = await store.get_trajectory("t1", "s1")
    assert traj_after is not None
    assert len(traj_after.steps) > len(traj.steps)

    # Events persisted for both phases
    events = await store.list_planner_events("t1")
    assert len(events) >= 2
    assert any(e.event_type == "resume" for e in events)


async def test_trajectory_persistence_failure_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Trajectory persistence failure logged."""

    class FailingStore(RecordingStateStore):
        """Store whose trajectory saves fail; planner events are kept."""

        def __init__(self) -> None:
            super().__init__()
            self.planner_events: list[tuple[str, PlannerEvent]] = []

        async def save_trajectory(
            self, trace_id: str, session_id: str, trajectory: Trajectory
        ) -> None:
            msg = (
                f"disk full ({trace_id}/{session_id}: "
                f"{len(trajectory.steps)} steps)"
            )
            raise RuntimeError(msg)

        async def save_planner_event(
            self, trace_id: str, event: PlannerEvent
        ) -> None:
            self.planner_events.append((trace_id, event))

    store = FailingStore()
    planner = _make_echo_planner(store)

    with caplog.at_level(logging.WARNING, logger="penguiflow.planner"):
        result = await planner.run(
            "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
        )
        assert isinstance(result, PlannerFinish)
        await _drain_persistence_tasks()

    assert any(
        "Background trajectory persistence failed" in r.message
        for r in caplog.records
    )


async def test_event_persistence_failure_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Event persistence failure logged."""

    class FailingEventStore(RecordingStateStore):
        """Store whose planner-event saves fail; trajectories are kept."""

        def __init__(self) -> None:
            super().__init__()
            self.trajectories: list[tuple[str, str, Trajectory]] = []

        async def save_trajectory(
            self, trace_id: str, session_id: str, trajectory: Trajectory
        ) -> None:
            self.trajectories.append((trace_id, session_id, trajectory))

        async def save_planner_event(
            self, trace_id: str, event: PlannerEvent
        ) -> None:
            msg = f"disk full ({trace_id}: {event.event_type})"
            raise RuntimeError(msg)

    store = FailingEventStore()
    planner = _make_echo_planner(store)

    with caplog.at_level(logging.WARNING, logger="penguiflow.planner"):
        result = await planner.run(
            "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
        )
        assert isinstance(result, PlannerFinish)
        await _drain_persistence_tasks()

    assert any(
        "Background planner-event persistence failed" in r.message
        for r in caplog.records
    )


async def test_events_persisted_on_run_loop_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events persisted on run loop error."""

    async def _failing_run_loop(
        planner: ReactPlanner, _trajectory: Trajectory, **_: None
    ) -> None:
        """Stub that buffers an event, then raises."""
        planner.emit_event(
            PlannerEvent(event_type="step_start", ts=1.0, trajectory_step=0)
        )
        msg = "Simulated run_loop failure"
        raise RuntimeError(msg)

    store = InMemoryStateStore()
    planner = _make_echo_planner(store)

    monkeypatch.setattr(rt_mod, "run_loop", _failing_run_loop)

    with pytest.raises(RuntimeError, match="Simulated"):
        await planner.run(
            "hi", tool_context={"session_id": "s1", "trace_id": "t1"}
        )

    await _drain_persistence_tasks()
    events = await store.list_planner_events("t1")
    assert len(events) >= 1
    assert events[0].event_type == "step_start"
