from __future__ import annotations

import pytest

from penguiflow.sessions.models import TaskContextSnapshot, TaskStatus, TaskType
from penguiflow.sessions.policy import ControlPolicy
from penguiflow.sessions.registry import TaskRegistry
from penguiflow.steering import SteeringEvent, SteeringEventType


@pytest.mark.asyncio
async def test_task_registry_create_update_list_remove_seed() -> None:
    persisted: list[str] = []

    async def _persist(task):  # type: ignore[no-untyped-def]
        persisted.append(task.task_id)

    registry = TaskRegistry(persist_task=_persist)
    snapshot = TaskContextSnapshot(session_id="s1", task_id="t1")
    task = await registry.create_task(
        session_id="s1",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=snapshot,
        description="d",
        task_id="t1",
    )
    assert task.task_id == "t1"
    assert persisted == ["t1"]
    assert await registry.get_task("missing") is None

    updated = await registry.update_status("t1", TaskStatus.RUNNING)
    assert updated is not None and updated.status == TaskStatus.RUNNING
    await registry.update_task("t1", result={"ok": True}, progress={"p": 1})
    task = await registry.get_task("t1")
    assert task is not None and task.result == {"ok": True}
    assert task.progress == {"p": 1}

    await registry.update_priority("t1", 5)
    task = await registry.get_task("t1")
    assert task is not None and task.priority == 5

    tasks = await registry.list_tasks("s1")
    assert [t.task_id for t in tasks] == ["t1"]

    await registry.remove_task("t1")
    assert await registry.get_task("t1") is None
    assert await registry.list_tasks("s1") == []

    seeded_snapshot = TaskContextSnapshot(session_id="s1", task_id="t2")
    seeded = await registry.create_task(
        session_id="s1",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=seeded_snapshot,
        task_id="t2",
    )
    new_registry = TaskRegistry()
    await new_registry.seed_tasks([seeded])
    assert (await new_registry.get_task("t2")) is not None

    parent_snapshot = TaskContextSnapshot(session_id="s1", task_id="parent", spawned_from_task_id="root")
    await new_registry.create_task(
        session_id="s1",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=parent_snapshot,
        task_id="parent",
    )
    child_snapshot = TaskContextSnapshot(session_id="s1", task_id="child", spawned_from_task_id="parent")
    await new_registry.create_task(
        session_id="s1",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=child_snapshot,
        task_id="child",
    )
    assert await new_registry.get_parent("child") == "parent"
    assert await new_registry.list_children("parent") == ["child"]


def test_control_policy_requires_confirmation_for_destructive() -> None:
    policy = ControlPolicy()
    event = SteeringEvent(
        session_id="s1",
        task_id="t1",
        event_type=SteeringEventType.CANCEL,
        payload={},
        source="agent",
    )
    assert policy.requires_confirmation(event) is True

    user_event = event.model_copy(update={"source": "user"})
    assert policy.requires_confirmation(user_event) is False

    confirmed = event.model_copy(update={"payload": {"confirmed": True}})
    assert policy.requires_confirmation(confirmed) is False

    non_destructive = event.model_copy(update={"event_type": SteeringEventType.PAUSE})
    assert policy.requires_confirmation(non_destructive) is False

    policy.require_confirmation = False
    assert policy.requires_confirmation(event) is False
