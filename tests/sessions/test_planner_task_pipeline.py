from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerEvent, PlannerPause, ReactPlanner, ToolSearchConfig
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.models import TaskStatus, TaskType
from penguiflow.sessions.planner import PlannerTaskPipeline
from penguiflow.state.models import SteeringEvent, SteeringEventType, TaskContextSnapshot, TaskState
from penguiflow.steering import SteeringCancelled


@dataclass(slots=True)
class _Registry:
    statuses: list[tuple[str, TaskStatus]] = field(default_factory=list)

    async def update_status(self, task_id: str, status: TaskStatus) -> None:
        self.statuses.append((task_id, status))


@dataclass(slots=True)
class _Session:
    registry: _Registry = field(default_factory=_Registry)
    published: list[Any] = field(default_factory=list)

    def _publish(self, update: Any) -> None:
        self.published.append(update)


@dataclass(slots=True)
class _Steering:
    cancelled: bool = False
    cancel_reason: str = "cancelled"
    next_event: SteeringEvent | None = None

    def has_event(self) -> bool:
        return self.next_event is not None

    def drain(self) -> list[SteeringEvent]:
        if self.next_event is None:
            return []
        event = self.next_event
        self.next_event = None
        return [event]

    async def wait_if_paused(self) -> None:
        return None

    async def next(self) -> SteeringEvent:
        assert self.next_event is not None
        return self.next_event


@dataclass(slots=True)
class _Runtime:
    state: TaskState
    context_snapshot: TaskContextSnapshot
    session: _Session
    steering: _Steering
    updates: list[tuple[str, Any]] = field(default_factory=list)

    def emit_update(self, update_type: Any, payload: Any) -> None:  # noqa: ANN001 - test stub
        self.updates.append((str(update_type), payload))


class _StubPlanner:
    def __init__(self, pause_token: str) -> None:
        self._event_callback = None
        self._pause_token = pause_token

    async def run(self, *args: Any, **kwargs: Any) -> PlannerPause:  # noqa: ANN002, ANN003 - test stub
        if callable(self._event_callback):
            self._event_callback(PlannerEvent(event_type="step_start", ts=0.0, trajectory_step=0))
        return PlannerPause(reason="approval_required", resume_token=self._pause_token)

    async def resume(self, *args: Any, **kwargs: Any) -> PlannerPause:  # noqa: ANN002, ANN003 - not exercised
        raise AssertionError("resume should not be called in these tests")


class _EchoArgs(BaseModel):
    text: str


class _EchoOut(BaseModel):
    text: str


@tool(desc="Deferred echo tool.", loading_mode=ToolLoadingMode.DEFERRED)
async def _deferred_echo(args: _EchoArgs, ctx: Any) -> _EchoOut:
    del ctx
    return _EchoOut(text=args.text)


class _ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, messages, response_format=None, stream=False, on_stream_chunk=None):  # type: ignore[no-untyped-def]
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No scripted responses left")
        return self._responses.pop(0), 0.0


def _make_runtime(*, task_type: TaskType, steering: _Steering) -> _Runtime:
    snapshot = TaskContextSnapshot(session_id="s", task_id="t", query="q")
    state = TaskState(
        task_id="t",
        session_id="s",
        status=TaskStatus.RUNNING,
        task_type=task_type,
        priority=0,
        context_snapshot=snapshot,
    )
    return _Runtime(state=state, context_snapshot=snapshot, session=_Session(), steering=steering)


@pytest.mark.asyncio
async def test_pipeline_raises_on_cancelled_flag_and_calls_event_sink() -> None:
    events: list[PlannerEvent] = []
    pause_token = "p1"
    runtime = _make_runtime(task_type=TaskType.FOREGROUND, steering=_Steering(cancelled=True, cancel_reason="bye"))

    pipeline = PlannerTaskPipeline(
        planner_factory=lambda: _StubPlanner(pause_token),  # type: ignore[return-value]
        event_sink=lambda event, trace_id: events.append(event),  # noqa: ARG005 - trace_id unused
    )

    with pytest.raises(SteeringCancelled, match="bye"):
        await pipeline(runtime)  # type: ignore[arg-type]

    assert events


@pytest.mark.asyncio
async def test_pipeline_raises_on_cancel_event() -> None:
    pause_token = "p2"
    steering = _Steering(
        next_event=SteeringEvent(
            session_id="s",
            task_id="t",
            event_type=SteeringEventType.CANCEL,
            payload={"reason": "no"},
        ),
    )
    runtime = _make_runtime(task_type=TaskType.FOREGROUND, steering=steering)
    pipeline = PlannerTaskPipeline(planner_factory=lambda: _StubPlanner(pause_token))  # type: ignore[arg-type]

    with pytest.raises(SteeringCancelled, match="no"):
        await pipeline(runtime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pipeline_raises_on_reject_event() -> None:
    pause_token = "p3"
    steering = _Steering(
        next_event=SteeringEvent(
            session_id="s",
            task_id="t",
            event_type=SteeringEventType.REJECT,
            payload={"resume_token": pause_token},
        ),
    )
    runtime = _make_runtime(task_type=TaskType.FOREGROUND, steering=steering)
    pipeline = PlannerTaskPipeline(planner_factory=lambda: _StubPlanner(pause_token))  # type: ignore[arg-type]

    with pytest.raises(SteeringCancelled, match="pause_rejected"):
        await pipeline(runtime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_background_pipeline_parallel_can_activate_deferred_tool(tmp_path) -> None:
    registry = ModelRegistry()
    registry.register("deferred_echo", _EchoArgs, _EchoOut)
    catalog = build_catalog(
        [Node(_deferred_echo, name="deferred_echo")],
        registry,
        default_loading_mode=ToolLoadingMode.DEFERRED,
    )

    def _planner_factory() -> ReactPlanner:
        return ReactPlanner(
            llm_client=_ScriptedClient(
                [
                    '{"next_node":"parallel","args":{"steps":[{"node":"deferred_echo","args":{"text":"hello"}}]}}',
                    '{"next_node":"final_response","args":{"answer":"done"}}',
                ]
            ),
            catalog=catalog,
            max_iters=2,
            tool_search=ToolSearchConfig(
                enabled=True,
                cache_dir=str(tmp_path / "tool_cache_bg"),
                default_loading_mode=ToolLoadingMode.DEFERRED,
            ),
        )

    events: list[PlannerEvent] = []
    runtime = _make_runtime(task_type=TaskType.BACKGROUND, steering=_Steering())
    pipeline = PlannerTaskPipeline(
        planner_factory=_planner_factory,
        event_sink=lambda event, trace_id: events.append(event),  # noqa: ARG005
    )

    result = await pipeline(runtime)  # type: ignore[arg-type]

    assert result.payload["raw_answer"] == "done"
    assert any(
        event.event_type == "tool_activated" and event.extra.get("tool_name") == "deferred_echo"
        for event in events
    )
    assert any(
        event.event_type == "tool_call_result" and event.extra.get("tool_name") == "deferred_echo"
        for event in events
    )
