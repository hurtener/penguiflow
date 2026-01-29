from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .models import (
    Artifact,
    ListTaskPushNotificationConfigResponse,
    ListTasksResponse,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _encode_page_token(offset: int) -> str:
    if offset <= 0:
        return ""
    raw = str(offset).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_page_token(token: str | None) -> int:
    if not token:
        return 0
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        return int(raw)
    except Exception as exc:  # pragma: no cover - validated by caller
        raise ValueError("invalid page token") from exc


def _apply_history_length(task: Task, history_length: int | None) -> Task:
    if history_length is None:
        return task
    history = list(task.history or [])
    if history_length <= 0:
        return task.model_copy(update={"history": None}, deep=True)
    return task.model_copy(update={"history": history[-history_length:]}, deep=True)


def _omit_artifacts(task: Task) -> Task:
    if task.artifacts is None:
        return task
    return task.model_copy(update={"artifacts": None}, deep=True)


class TaskEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamResponse]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self, task_id: str
    ) -> tuple[asyncio.Queue[StreamResponse], Callable[[], Coroutine[Any, Any, None]]]:
        queue: asyncio.Queue[StreamResponse] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(task_id, set()).add(queue)

        async def _unsubscribe() -> None:
            async with self._lock:
                subscribers = self._subscribers.get(task_id)
                if subscribers is None:
                    return
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(task_id, None)

        return queue, _unsubscribe

    def publish(self, task_id: str, event: StreamResponse) -> None:
        subscribers = self._subscribers.get(task_id)
        if not subscribers:
            return
        for queue in list(subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue


@dataclass(slots=True)
class StoredTask:
    task: Task
    created_at: datetime
    updated_at: datetime


class TaskStore(Protocol):
    async def create_task(self, task: Task) -> Task: ...

    async def add_history(self, task_id: str, message: Message) -> None: ...

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        final: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...

    async def add_artifact(
        self,
        task_id: str,
        artifact: Artifact,
        *,
        append: bool,
        last_chunk: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...

    async def get_task(self, task_id: str, history_length: int | None = None) -> Task: ...

    async def list_tasks(
        self,
        *,
        context_id: str | None,
        status: TaskState | None,
        page_size: int,
        page_token: str | None,
        history_length: int | None,
        status_timestamp_after: datetime | None,
        include_artifacts: bool,
    ) -> ListTasksResponse: ...

    async def subscribe(
        self, task_id: str
    ) -> tuple[asyncio.Queue[StreamResponse], Callable[[], Coroutine[Any, Any, None]]]: ...

    async def set_push_config(
        self,
        task_id: str,
        config_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig: ...

    async def get_push_config(self, task_id: str, config_id: str) -> TaskPushNotificationConfig: ...

    async def list_push_configs(
        self,
        task_id: str,
        *,
        page_size: int,
        page_token: str | None,
    ) -> ListTaskPushNotificationConfigResponse: ...

    async def delete_push_config(self, task_id: str, config_id: str) -> None: ...


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, StoredTask] = {}
        self._lock = asyncio.Lock()
        self._events = TaskEventBroker()
        self._push_configs: dict[str, dict[str, TaskPushNotificationConfig]] = {}

    async def create_task(self, task: Task) -> Task:
        now = _utc_now()
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task '{task.id}' already exists")
            stored = StoredTask(task=task, created_at=now, updated_at=now)
            self._tasks[task.id] = stored
            self._push_configs.setdefault(task.id, {})
        return task.model_copy(deep=True)

    async def add_history(self, task_id: str, message: Message) -> None:
        async with self._lock:
            stored = self._tasks.get(task_id)
            if stored is None:
                raise KeyError(task_id)
            history = list(stored.task.history or [])
            history.append(message)
            stored.task = stored.task.model_copy(update={"history": history}, deep=True)
            stored.updated_at = _utc_now()

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        final: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            stored = self._tasks.get(task_id)
            if stored is None:
                raise KeyError(task_id)
            stored.task = stored.task.model_copy(update={"status": status}, deep=True)
            stored.updated_at = _utc_now()
            event = TaskStatusUpdateEvent(
                task_id=stored.task.id,
                context_id=stored.task.context_id,
                status=status,
                final=final,
                metadata=dict(metadata or {}),
            )
        self._events.publish(task_id, StreamResponse(status_update=event))

    async def add_artifact(
        self,
        task_id: str,
        artifact: Artifact,
        *,
        append: bool,
        last_chunk: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            stored = self._tasks.get(task_id)
            if stored is None:
                raise KeyError(task_id)
            artifacts = list(stored.task.artifacts or [])
            if append:
                for idx, existing in enumerate(artifacts):
                    if existing.artifact_id == artifact.artifact_id:
                        combined_parts = list(existing.parts) + list(artifact.parts)
                        artifacts[idx] = existing.model_copy(update={"parts": combined_parts}, deep=True)
                        break
                else:
                    artifacts.append(artifact)
            else:
                artifacts.append(artifact)
            stored.task = stored.task.model_copy(update={"artifacts": artifacts}, deep=True)
            stored.updated_at = _utc_now()
            event = TaskArtifactUpdateEvent(
                task_id=stored.task.id,
                context_id=stored.task.context_id,
                artifact=artifact,
                append=append,
                last_chunk=last_chunk,
                metadata=dict(metadata or {}),
            )
        self._events.publish(task_id, StreamResponse(artifact_update=event))

    async def get_task(self, task_id: str, history_length: int | None = None) -> Task:
        async with self._lock:
            stored = self._tasks.get(task_id)
            if stored is None:
                raise KeyError(task_id)
            task = stored.task.model_copy(deep=True)
        task = _apply_history_length(task, history_length)
        return task

    async def list_tasks(
        self,
        *,
        context_id: str | None,
        status: TaskState | None,
        page_size: int,
        page_token: str | None,
        history_length: int | None,
        status_timestamp_after: datetime | None,
        include_artifacts: bool,
    ) -> ListTasksResponse:
        offset = _decode_page_token(page_token)
        async with self._lock:
            records = list(self._tasks.values())
        if context_id:
            records = [record for record in records if record.task.context_id == context_id]
        if status is not None:
            records = [record for record in records if record.task.status.state == status]
        if status_timestamp_after is not None:
            records = [record for record in records if record.updated_at > status_timestamp_after]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        total_size = len(records)
        page = records[offset : offset + page_size]
        tasks = [record.task.model_copy(deep=True) for record in page]
        processed = []
        for task in tasks:
            task = _apply_history_length(task, history_length)
            if not include_artifacts:
                task = _omit_artifacts(task)
            processed.append(task)
        next_offset = offset + len(page)
        next_token = _encode_page_token(next_offset) if next_offset < total_size else ""
        return ListTasksResponse(
            tasks=processed,
            next_page_token=next_token,
            page_size=page_size,
            total_size=total_size,
        )

    async def subscribe(
        self, task_id: str
    ) -> tuple[asyncio.Queue[StreamResponse], Callable[[], Coroutine[Any, Any, None]]]:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
        return await self._events.subscribe(task_id)

    async def set_push_config(
        self,
        task_id: str,
        config_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            configs = self._push_configs.setdefault(task_id, {})
            configs[config_id] = config
        return config.model_copy(deep=True)

    async def get_push_config(self, task_id: str, config_id: str) -> TaskPushNotificationConfig:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            config = self._push_configs.get(task_id, {}).get(config_id)
            if config is None:
                raise KeyError(config_id)
        return config.model_copy(deep=True)

    async def list_push_configs(
        self,
        task_id: str,
        *,
        page_size: int,
        page_token: str | None,
    ) -> ListTaskPushNotificationConfigResponse:
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        offset = _decode_page_token(page_token)
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            configs = list(self._push_configs.get(task_id, {}).values())
        configs.sort(key=lambda item: item.name or "")
        total_size = len(configs)
        page = configs[offset : offset + page_size]
        next_offset = offset + len(page)
        next_token = _encode_page_token(next_offset) if next_offset < total_size else ""
        return ListTaskPushNotificationConfigResponse(configs=page, next_page_token=next_token)

    async def delete_push_config(self, task_id: str, config_id: str) -> None:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            configs = self._push_configs.get(task_id, {})
            configs.pop(config_id, None)
