from __future__ import annotations

import pytest

from penguiflow.sessions.models import TaskContextSnapshot, TaskStatus, TaskType
from penguiflow.sessions.registry import TaskRegistry


def _snapshot(session_id: str, task_id: str) -> TaskContextSnapshot:
    return TaskContextSnapshot(session_id=session_id, task_id=task_id)


@pytest.mark.asyncio
async def test_task_registry_create_update_and_remove() -> None:
    registry = TaskRegistry()
    snapshot = _snapshot("session-1", "task-1")
    task = await registry.create_task(
        session_id="session-1",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=snapshot,
    )
    assert task.status == TaskStatus.PENDING

    await registry.update_status(task.task_id, TaskStatus.RUNNING)
    updated = await registry.get_task(task.task_id)
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING

    await registry.update_task(
        task.task_id,
        result={"answer": "ok"},
        error="",
        trace_id="trace-1",
        priority=3,
        progress={"step": 1},
    )
    updated = await registry.get_task(task.task_id)
    assert updated is not None
    assert updated.result == {"answer": "ok"}
    assert updated.trace_id == "trace-1"
    assert updated.priority == 3
    assert updated.progress == {"step": 1}

    await registry.remove_task(task.task_id)
    assert await registry.get_task(task.task_id) is None


@pytest.mark.asyncio
async def test_task_registry_list_active_filters_status() -> None:
    registry = TaskRegistry()
    task_a = await registry.create_task(
        session_id="session-2",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=_snapshot("session-2", "task-a"),
    )
    task_b = await registry.create_task(
        session_id="session-2",
        task_type=TaskType.BACKGROUND,
        priority=0,
        context_snapshot=_snapshot("session-2", "task-b"),
    )
    await registry.update_status(task_b.task_id, TaskStatus.COMPLETE)
    active = await registry.list_active("session-2")
    active_ids = {task.task_id for task in active}
    assert task_a.task_id in active_ids
    assert task_b.task_id not in active_ids
