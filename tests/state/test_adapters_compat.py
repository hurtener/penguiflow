"""Tests for adapters compat."""

from __future__ import annotations

import pytest
from typing_extensions import override

from penguiflow.planner import PlannerEvent
from penguiflow.state import RemoteBinding
from penguiflow.state.adapters import (
    list_planner_events_compat,
    list_steering_compat,
    list_tasks_compat,
    list_updates_compat,
    maybe_save_remote_binding,
    save_planner_event_compat,
    save_steering_compat,
    save_task_compat,
    save_update_compat,
)
from penguiflow.state.models import (
    StateUpdate,
    SteeringEvent,
    SteeringEventType,
    TaskContextSnapshot,
    TaskState,
    TaskStatus,
    TaskType,
    UpdateType,
)
from penguiflow.state.protocol import SupportsPlannerEvents, SupportsSteering

from ..state_store_doubles import RecordingStateStore


class _CompatStore(
    RecordingStateStore, SupportsSteering, SupportsPlannerEvents
):
    def __init__(self) -> None:
        super().__init__()
        self.saved_updates: list[StateUpdate] = []
        self.saved_events: list[tuple[str, PlannerEvent]] = []
        self.saved_tasks: list[TaskState] = []
        self.saved_steering: list[SteeringEvent] = []
        self.saved_bindings: list[RemoteBinding] = []
        self.update_queries: list[tuple[str | None, str | None, int]] = []

    async def save_task_update(self, update: StateUpdate) -> None:
        self.saved_updates.append(update)

    async def list_task_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> list[StateUpdate]:
        self.update_queries.append((task_id, since_id, limit))
        return [u for u in self.saved_updates if u.session_id == session_id]

    @override
    async def save_planner_event(
        self, trace_id: str, event: PlannerEvent
    ) -> None:
        self.saved_events.append((trace_id, event))

    @override
    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]:
        return [event for tid, event in self.saved_events if tid == trace_id]

    async def save_task(self, state: TaskState) -> None:
        self.saved_tasks.append(state)

    async def list_tasks(self, session_id: str) -> list[TaskState]:
        return [
            task for task in self.saved_tasks if task.session_id == session_id
        ]

    @override
    async def save_steering(self, event: SteeringEvent) -> None:
        self.saved_steering.append(event)

    @override
    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> list[SteeringEvent]:
        events = [
            evt for evt in self.saved_steering if evt.session_id == session_id
        ]
        if task_id is not None:
            events = [evt for evt in events if evt.task_id == task_id]
        return events

    @override
    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        self.saved_bindings.append(binding)


class _NoPlannerEventsStore:
    pass


@pytest.mark.asyncio
async def test_update_compat_uses_legacy_method_names() -> None:
    """Update compat uses legacy method names."""
    store = _CompatStore()
    update = StateUpdate(
        session_id="s",
        task_id="t",
        update_type=UpdateType.THINKING,
        content={"x": 1},
    )
    await save_update_compat(store, update)
    assert store.saved_updates == [update]

    updates = await list_updates_compat(store, "s")
    assert updates == [update]


@pytest.mark.asyncio
async def test_planner_event_compat_and_errors() -> None:
    """Planner event compat and errors."""
    store = _CompatStore()
    event = PlannerEvent(event_type="step_start", ts=0.0, trajectory_step=0)
    await save_planner_event_compat(store, "trace", event)
    events = await list_planner_events_compat(store, "trace")
    assert events == [event]

    with pytest.raises(
        TypeError, match="StateStore missing save_planner_event"
    ):
        await save_planner_event_compat(
            _NoPlannerEventsStore(), "trace", event
        )
    with pytest.raises(
        TypeError, match="StateStore missing list_planner_events"
    ):
        await list_planner_events_compat(_NoPlannerEventsStore(), "trace")


@pytest.mark.asyncio
async def test_task_and_steering_compat_helpers_and_remote_binding() -> None:
    """Task and steering compat helpers and remote binding."""
    store = _CompatStore()
    snapshot = TaskContextSnapshot(session_id="s", task_id="t")
    task = TaskState(
        task_id="t",
        session_id="s",
        status=TaskStatus.PENDING,
        task_type=TaskType.FOREGROUND,
        priority=0,
        context_snapshot=snapshot,
    )
    await save_task_compat(store, task)
    assert await list_tasks_compat(store, "s") == [task]

    steering = SteeringEvent(
        session_id="s",
        task_id="t",
        event_type=SteeringEventType.USER_MESSAGE,
        payload={"msg": "hi"},
    )
    await save_steering_compat(store, steering)
    assert await list_steering_compat(store, "s", task_id="t") == [steering]

    binding = RemoteBinding(
        trace_id="trace",
        context_id="ctx",
        task_id="task",
        agent_url="http://a",
    )
    await maybe_save_remote_binding(store, binding)
    assert store.saved_bindings == [binding]
