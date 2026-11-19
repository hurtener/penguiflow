"""Tests for planner task pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    PlannerEvent,
    PlannerPause,
    ReactPlanner,
    ToolSearchConfig,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.models import TaskStatus, TaskType
from penguiflow.sessions.planner import PlannerTaskPipeline, TaskPlanner
from penguiflow.sessions.session import StreamingSession, TaskRuntime
from penguiflow.state.models import (
    SteeringEvent,
    SteeringEventType,
    TaskContextSnapshot,
    TaskState,
)
from penguiflow.steering import SteeringCancelled, SteeringInbox

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from penguiflow.planner import PlannerEventCallback
    from penguiflow.planner.context import ToolContext


class _StubPlanner(TaskPlanner):
    """Planner that emits one event and always pauses for approval."""

    def __init__(self, pause_token: str) -> None:
        """Pause with ``pause_token``."""
        self.event_callback: PlannerEventCallback | None = None
        self._pause_token = pause_token
        self.queries: list[str] = []

    @override
    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
    ) -> PlannerPause:
        self.queries.append(query)
        if callable(self.event_callback):
            self.event_callback(
                PlannerEvent(
                    event_type="step_start", ts=0.0, trajectory_step=0
                )
            )
        return PlannerPause(
            reason="approval_required", resume_token=self._pause_token
        )

    @override
    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
    ) -> PlannerPause:
        msg = "resume should not be called in these tests"
        raise AssertionError(msg)


class _EchoArgs(BaseModel):
    text: str


class _EchoOut(BaseModel):
    text: str


@tool(desc="Deferred echo tool.", loading_mode=ToolLoadingMode.DEFERRED)
async def _deferred_echo(args: _EchoArgs, _ctx: ToolContext) -> _EchoOut:
    return _EchoOut(text=args.text)


class _ScriptedClient(JSONLLMClient):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        if not self._responses:
            msg = "No scripted responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


async def _make_runtime(
    *, task_type: TaskType, steering_events: Sequence[SteeringEvent] = ()
) -> TaskRuntime:
    """Build the runtime of task ``t`` with ``steering_events`` queued."""
    snapshot = TaskContextSnapshot(session_id="s", task_id="t", query="q")
    state = TaskState(
        task_id="t",
        session_id="s",
        status=TaskStatus.RUNNING,
        task_type=task_type,
        priority=0,
        context_snapshot=snapshot,
    )
    steering = SteeringInbox()
    for event in steering_events:
        assert await steering.push(event)
    return TaskRuntime(
        session=StreamingSession("s"),
        state=state,
        steering=steering,
        context_snapshot=snapshot,
    )


@pytest.mark.asyncio
async def test_pipeline_raises_on_cancelled_flag_and_calls_event_sink() -> (
    None
):
    """Pipeline raises on cancelled flag and calls event sink."""
    events: list[PlannerEvent] = []
    pause_token = "p1"
    runtime = await _make_runtime(
        task_type=TaskType.FOREGROUND,
        steering_events=[
            SteeringEvent(
                session_id="s",
                task_id="t",
                event_type=SteeringEventType.CANCEL,
                payload={"reason": "bye"},
            )
        ],
    )

    pipeline = PlannerTaskPipeline(
        planner_factory=lambda: _StubPlanner(pause_token),
        event_sink=lambda event, _trace_id: events.append(event),
    )

    with pytest.raises(SteeringCancelled, match="bye"):
        await pipeline(runtime)

    assert events


@pytest.mark.asyncio
async def test_pipeline_raises_on_cancel_event() -> None:
    """Pipeline raises on cancel event."""
    pause_token = "p2"
    runtime = await _make_runtime(
        task_type=TaskType.FOREGROUND,
        steering_events=[
            SteeringEvent(
                session_id="s",
                task_id="t",
                event_type=SteeringEventType.CANCEL,
                payload={"reason": "no"},
            )
        ],
    )
    pipeline = PlannerTaskPipeline(
        planner_factory=lambda: _StubPlanner(pause_token)
    )

    with pytest.raises(SteeringCancelled, match="no"):
        await pipeline(runtime)


@pytest.mark.asyncio
async def test_pipeline_raises_on_reject_event() -> None:
    """Pipeline raises on reject event."""
    pause_token = "p3"
    runtime = await _make_runtime(
        task_type=TaskType.FOREGROUND,
        steering_events=[
            SteeringEvent(
                session_id="s",
                task_id="t",
                event_type=SteeringEventType.REJECT,
                payload={"resume_token": pause_token},
            )
        ],
    )
    pipeline = PlannerTaskPipeline(
        planner_factory=lambda: _StubPlanner(pause_token)
    )

    with pytest.raises(SteeringCancelled, match="pause_rejected"):
        await pipeline(runtime)


@pytest.mark.asyncio
async def test_background_pipeline_parallel_can_activate_deferred_tool(
    tmp_path: Path,
) -> None:
    """Background pipeline parallel can activate deferred tool."""
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
    runtime = await _make_runtime(task_type=TaskType.BACKGROUND)
    pipeline = PlannerTaskPipeline(
        planner_factory=_planner_factory,
        event_sink=lambda event, _trace_id: events.append(event),
    )

    result = await pipeline(runtime)

    assert result.payload is not None
    assert result.payload["raw_answer"] == "done"
    assert any(
        event.event_type == "tool_activated"
        and event.extra.get("tool_name") == "deferred_echo"
        for event in events
    )
    assert any(
        event.event_type == "tool_call_result"
        and event.extra.get("tool_name") == "deferred_echo"
        for event in events
    )
