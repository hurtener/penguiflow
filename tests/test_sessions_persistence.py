from __future__ import annotations

from collections.abc import Sequence

import pytest

from penguiflow.sessions.models import StateUpdate, TaskContextSnapshot, TaskState, TaskStatus, TaskType, UpdateType
from penguiflow.sessions.persistence import InMemorySessionStateStore, StateStoreSessionAdapter
from penguiflow.state import RemoteBinding, StateStore, StoredEvent
from penguiflow.steering import SteeringEvent, SteeringEventType


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.events: list[StoredEvent] = []

    async def save_event(self, event: StoredEvent) -> None:
        self.events.append(event)

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        return [event for event in self.events if event.trace_id == trace_id]

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        _ = binding
        return None


@pytest.mark.asyncio
async def test_inmemory_session_state_store_lists_with_filters() -> None:
    store = InMemorySessionStateStore(max_updates_per_session=0, max_steering_events_per_session=0)
    session_id = "s-persist"
    await store.save_update(
        StateUpdate(session_id=session_id, task_id="t1", update_type=UpdateType.RESULT, content={"ok": True})
    )
    await store.save_update(
        StateUpdate(session_id=session_id, task_id="t2", update_type=UpdateType.ERROR, content={"ok": False})
    )
    updates = await store.list_updates(session_id, task_id="t1")
    assert [u.task_id for u in updates] == ["t1"]

    first_id = updates[0].update_id
    await store.save_update(
        StateUpdate(session_id=session_id, task_id="t1", update_type=UpdateType.PROGRESS, content={"n": 1})
    )
    updates_since = await store.list_updates(session_id, since_id=first_id)
    assert len(updates_since) == 2

    event1 = SteeringEvent(session_id=session_id, task_id="t1", event_type=SteeringEventType.CANCEL, payload={})
    event2 = SteeringEvent(session_id=session_id, task_id="t2", event_type=SteeringEventType.PAUSE, payload={})
    await store.save_steering(event1)
    await store.save_steering(event2)
    steering = await store.list_steering(session_id, task_id="t2")
    assert [e.task_id for e in steering] == ["t2"]

    steering_since = await store.list_steering(session_id, since_id=event1.event_id)
    assert [e.event_id for e in steering_since] == [event2.event_id]


@pytest.mark.asyncio
async def test_state_store_session_adapter_roundtrip() -> None:
    core_store = InMemoryStateStore()
    adapter = StateStoreSessionAdapter(core_store)

    snapshot = TaskContextSnapshot(session_id="s1", task_id="t1", llm_context={"k": "v"})
    state = TaskState(
        task_id="t1",
        session_id="s1",
        status=TaskStatus.PENDING,
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=snapshot,
    )
    update = StateUpdate(session_id="s1", task_id="t1", update_type=UpdateType.STATUS_CHANGE, content={"s": "x"})
    steering = SteeringEvent(
        session_id="s1",
        task_id="t1",
        event_type=SteeringEventType.REDIRECT,
        payload={"instruction": "new"},
        source="user",
    )

    await adapter.save_task(state)
    await adapter.save_update(update)
    await adapter.save_steering(steering)

    tasks = await adapter.list_tasks("s1")
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"
    assert tasks[0].context_snapshot.llm_context["k"] == "v"

    updates = await adapter.list_updates("s1", task_id="t1")
    assert len(updates) == 1
    assert updates[0].update_type == UpdateType.STATUS_CHANGE

    steering_events = await adapter.list_steering("s1")
    assert len(steering_events) == 1
    assert steering_events[0].event_type == SteeringEventType.REDIRECT

    extra = StoredEvent(trace_id="session:s1", ts=0.0, kind="other", node_name=None, node_id=None, payload={})
    bad_payload = StoredEvent(  # type: ignore[arg-type]
        trace_id="session:s1",
        ts=0.0,
        kind="session.update",
        node_name=None,
        node_id="t1",
        payload="x",
    )
    core_store.events.extend([extra, bad_payload])
    updates = await adapter.list_updates("s1")
    assert len(updates) == 1
