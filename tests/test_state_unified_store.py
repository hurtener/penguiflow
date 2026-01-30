from __future__ import annotations

import time

import pytest

from penguiflow.planner import PlannerEvent, Trajectory
from penguiflow.state import (
    InMemoryStateStore,
    RemoteBinding,
    StateUpdate,
    SteeringEvent,
    SteeringEventType,
    StoredEvent,
    TaskContextSnapshot,
    TaskState,
    TaskStatus,
    TaskType,
    UpdateType,
    require_capabilities,
)
from penguiflow.state.adapters import (
    list_planner_events_compat,
    list_updates_compat,
    save_planner_event_compat,
    save_update_compat,
)


@pytest.mark.asyncio
async def test_inmemory_statestore_events_idempotent_and_ordered() -> None:
    store = InMemoryStateStore(max_events_per_trace=10)
    event_a = StoredEvent(trace_id="t", ts=1.0, kind="k", node_name=None, node_id=None, payload={"x": 1})
    event_b = StoredEvent(trace_id="t", ts=1.0, kind="k2", node_name=None, node_id=None, payload={"x": 2})

    await store.save_event(event_a)
    await store.save_event(event_a)  # idempotent
    await store.save_event(event_b)

    history = await store.load_history("t")
    assert [e.kind for e in history] == ["k", "k2"]


@pytest.mark.asyncio
async def test_inmemory_statestore_remote_binding_upsert() -> None:
    store = InMemoryStateStore()
    binding = RemoteBinding(trace_id="t", context_id=None, task_id="x", agent_url="a")
    await store.save_remote_binding(binding)
    await store.save_remote_binding(RemoteBinding(trace_id="t", context_id=None, task_id="x", agent_url="b"))
    # No public read API; this just ensures upserts don't crash.


@pytest.mark.asyncio
async def test_inmemory_statestore_planner_and_memory_state_roundtrip() -> None:
    store = InMemoryStateStore()
    await store.save_planner_state("token", {"a": 1})
    assert await store.load_planner_state("token") == {"a": 1}
    assert await store.load_planner_state("token") is None

    await store.save_memory_state("mem-key", {"k": "v"})
    assert await store.load_memory_state("mem-key") == {"k": "v"}
    assert await store.load_memory_state("missing") is None


@pytest.mark.asyncio
async def test_inmemory_statestore_tasks_updates_and_aliases() -> None:
    store = InMemoryStateStore()
    snapshot = TaskContextSnapshot(session_id="s", task_id="t", llm_context={"x": 1})
    task = TaskState(
        task_id="t",
        session_id="s",
        status=TaskStatus.PENDING,
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=snapshot,
    )
    await store.save_task(task)
    tasks = await store.list_tasks("s")
    assert len(tasks) == 1
    assert tasks[0].task_id == "t"

    update1 = StateUpdate(session_id="s", task_id="t", update_type=UpdateType.RESULT, content={"ok": True})
    await store.save_update(update1)
    update2 = StateUpdate(session_id="s", task_id="t", update_type=UpdateType.PROGRESS, content={"n": 1})
    await store.save_task_update(update2)

    updates = await store.list_updates("s")
    assert len(updates) == 2
    updates2 = await store.list_task_updates("s", since_id=update1.update_id)
    assert [u.update_id for u in updates2] == [update2.update_id]


@pytest.mark.asyncio
async def test_inmemory_statestore_steering_sanitised_and_cursor() -> None:
    store = InMemoryStateStore(max_steering_events_per_session=10)
    large_payload = {"items": list(range(10_000))}
    event1 = SteeringEvent(
        session_id="s",
        task_id="t",
        event_type=SteeringEventType.USER_MESSAGE,
        payload=large_payload,
    )
    event2 = SteeringEvent(session_id="s", task_id="t", event_type=SteeringEventType.CANCEL, payload={"reason": "x"})
    await store.save_steering(event1)
    await store.save_steering(event2)

    steering = await store.list_steering("s")
    assert len(steering) == 2
    # Ensure sanitization produces JSON-serialisable content and doesn't crash
    assert isinstance(steering[0].payload, dict)

    steering_since = await store.list_steering("s", since_id=event1.event_id)
    assert [e.event_id for e in steering_since] == [event2.event_id]
    assert '"steering"' in event2.to_injection()


@pytest.mark.asyncio
async def test_inmemory_statestore_trajectory_isolation_and_traces() -> None:
    store = InMemoryStateStore()
    await store.save_trajectory("trace-1", "s", Trajectory(query="hello"))
    await store.save_trajectory("trace-2", "s", Trajectory(query="world"))

    assert await store.get_trajectory("trace-1", "other") is None
    t1 = await store.get_trajectory("trace-1", "s")
    assert t1 is not None and t1.query == "hello"
    assert await store.list_traces("s") == ["trace-2", "trace-1"]


@pytest.mark.asyncio
async def test_inmemory_statestore_planner_events_and_alias() -> None:
    store = InMemoryStateStore()
    event = PlannerEvent(event_type="step_start", ts=time.time(), trajectory_step=0)
    await store.save_planner_event("trace", event)
    assert await store.list_planner_events("trace") == [event]
    assert await store.get_events("trace") == [event]


def test_require_capabilities_raises() -> None:
    store = object()
    with pytest.raises(TypeError):
        require_capabilities(store, feature="tasks", methods=("save_task", "list_tasks"))


class _LegacyTaskStore:
    def __init__(self) -> None:
        self._updates: list[StateUpdate] = []

    async def save_task_update(self, update: StateUpdate) -> None:
        self._updates.append(update)

    async def list_task_updates(self, session_id: str, *, task_id=None, since_id=None, limit=500):  # type: ignore[no-untyped-def]
        del session_id, task_id
        updates = list(self._updates)
        if since_id:
            for idx, update in enumerate(updates):
                if update.update_id == since_id:
                    updates = updates[idx + 1 :]
                    break
        return updates[-limit:]


@pytest.mark.asyncio
async def test_adapters_update_compat_uses_legacy_names() -> None:
    store = _LegacyTaskStore()
    update1 = StateUpdate(session_id="s", task_id="t", update_type=UpdateType.RESULT, content={"ok": True})
    update2 = StateUpdate(session_id="s", task_id="t", update_type=UpdateType.RESULT, content={"ok": True})
    await save_update_compat(store, update1)
    await save_update_compat(store, update2)

    updates = await list_updates_compat(store, "s", since_id=update1.update_id)
    assert [u.update_id for u in updates] == [update2.update_id]


class _LegacyPlaygroundStore:
    def __init__(self) -> None:
        self.events: dict[str, list[PlannerEvent]] = {}

    async def save_event(self, trace_id: str, event: PlannerEvent) -> None:
        self.events.setdefault(trace_id, []).append(event)

    async def get_events(self, trace_id: str) -> list[PlannerEvent]:
        return list(self.events.get(trace_id, []))


@pytest.mark.asyncio
async def test_adapters_planner_event_compat_uses_legacy_playground_names() -> None:
    store = _LegacyPlaygroundStore()
    event = PlannerEvent(event_type="finish", ts=time.time(), trajectory_step=1)
    await save_planner_event_compat(store, "trace", event)
    assert await list_planner_events_compat(store, "trace") == [event]
