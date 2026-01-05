"""Task registry implementation for streaming sessions."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from .models import TaskContextSnapshot, TaskState, TaskStatus, TaskType, _utc_now


class TaskRegistry:
    """In-memory registry that tracks task state per session."""

    def __init__(
        self,
        *,
        persist_task: Callable[[TaskState], Awaitable[None]] | None = None,
    ) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._by_session: dict[str, list[str]] = {}
        self._children: dict[str, list[str]] = {}
        self._parent: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._persist_task = persist_task

    async def create_task(
        self,
        *,
        session_id: str,
        task_type: TaskType,
        priority: int,
        context_snapshot: TaskContextSnapshot,
        description: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
    ) -> TaskState:
        task_id = task_id or secrets.token_hex(8)
        state = TaskState(
            task_id=task_id,
            session_id=session_id,
            status=TaskStatus.PENDING,
            task_type=task_type,
            priority=priority,
            context_snapshot=context_snapshot,
            trace_id=trace_id,
            description=description,
        )
        async with self._lock:
            self._tasks[task_id] = state
            self._by_session.setdefault(session_id, []).append(task_id)
            parent_id = context_snapshot.spawned_from_task_id if context_snapshot else None
            if parent_id and parent_id != task_id:
                self._parent[task_id] = parent_id
                children = self._children.setdefault(parent_id, [])
                if task_id not in children:
                    children.append(task_id)
        if self._persist_task is not None:
            await self._persist_task(state)
        return state

    async def get_task(self, task_id: str) -> TaskState | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(
        self,
        session_id: str,
        *,
        status: TaskStatus | None = None,
    ) -> list[TaskState]:
        async with self._lock:
            ids = list(self._by_session.get(session_id, []))
            tasks = [self._tasks[task_id] for task_id in ids if task_id in self._tasks]
        if status is None:
            return tasks
        return [task for task in tasks if task.status == status]

    async def update_status(self, task_id: str, status: TaskStatus) -> TaskState | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task.update_status(status)
        if task is not None and self._persist_task is not None:
            await self._persist_task(task)
        return task

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        result: Any | None = None,
        error: str | None = None,
        trace_id: str | None = None,
        priority: int | None = None,
        progress: dict[str, Any] | None = None,
    ) -> TaskState | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if status is not None:
                task.update_status(status)
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            if trace_id is not None:
                task.trace_id = trace_id
            if priority is not None:
                task.priority = priority
            if progress is not None:
                task.progress = dict(progress)
            task.updated_at = _utc_now()
        if task is not None and self._persist_task is not None:
            await self._persist_task(task)
        return task

    async def remove_task(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return
            ids = self._by_session.get(task.session_id)
            if ids is not None:
                self._by_session[task.session_id] = [tid for tid in ids if tid != task_id]
            parent = self._parent.pop(task_id, None)
            if parent is not None:
                children = self._children.get(parent)
                if children is not None:
                    self._children[parent] = [tid for tid in children if tid != task_id]

    async def update_priority(self, task_id: str, priority: int) -> TaskState | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task.priority = priority
            task.updated_at = _utc_now()
        if task is not None and self._persist_task is not None:
            await self._persist_task(task)
        return task

    async def list_active(self, session_id: str) -> list[TaskState]:
        tasks = await self.list_tasks(session_id)
        return [
            task
            for task in tasks
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED}
        ]

    async def seed_tasks(self, tasks: list[TaskState]) -> None:
        async with self._lock:
            for task in tasks:
                self._tasks[task.task_id] = task
                self._by_session.setdefault(task.session_id, []).append(task.task_id)
                parent_id = task.context_snapshot.spawned_from_task_id if task.context_snapshot else None
                if parent_id and parent_id != task.task_id:
                    self._parent[task.task_id] = parent_id
                    children = self._children.setdefault(parent_id, [])
                    if task.task_id not in children:
                        children.append(task.task_id)

    async def get_parent(self, task_id: str) -> str | None:
        async with self._lock:
            return self._parent.get(task_id)

    async def list_children(self, task_id: str) -> list[str]:
        async with self._lock:
            return list(self._children.get(task_id, []))


__all__ = ["TaskRegistry"]
