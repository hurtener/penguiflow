"""Persistence adapters for session/task state."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol

from penguiflow.state import StateStore, StoredEvent
from penguiflow.steering import SteeringEvent

from .models import StateUpdate, TaskState, TaskStateModel


class SessionStateStore(Protocol):
    async def save_task(self, state: TaskState) -> None: ...

    async def save_update(self, update: StateUpdate) -> None: ...

    async def save_steering(self, event: SteeringEvent) -> None: ...

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]: ...

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]: ...

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]: ...


def _clone_task(state: TaskState) -> TaskState:
    return TaskState(
        task_id=state.task_id,
        session_id=state.session_id,
        status=state.status,
        task_type=state.task_type,
        priority=state.priority,
        context_snapshot=state.context_snapshot,
        trace_id=state.trace_id,
        result=state.result,
        error=state.error,
        description=state.description,
        progress=dict(state.progress) if state.progress else None,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


class InMemorySessionStateStore(SessionStateStore):
    """In-memory state store for session/task persistence and replay."""

    def __init__(
        self,
        *,
        max_updates_per_session: int = 10_000,
        max_steering_events_per_session: int = 10_000,
    ) -> None:
        self._tasks: dict[str, dict[str, TaskState]] = {}
        self._updates: dict[str, list[StateUpdate]] = {}
        self._steering: dict[str, list[SteeringEvent]] = {}
        self._max_updates = max_updates_per_session
        self._max_steering = max_steering_events_per_session
        self._lock = asyncio.Lock()

    async def save_task(self, state: TaskState) -> None:
        async with self._lock:
            self._tasks.setdefault(state.session_id, {})[state.task_id] = _clone_task(state)

    async def save_update(self, update: StateUpdate) -> None:
        async with self._lock:
            updates = self._updates.setdefault(update.session_id, [])
            updates.append(update)
            if self._max_updates and len(updates) > self._max_updates:
                self._updates[update.session_id] = updates[-self._max_updates :]

    async def save_steering(self, event: SteeringEvent) -> None:
        async with self._lock:
            events = self._steering.setdefault(event.session_id, [])
            events.append(event)
            if self._max_steering and len(events) > self._max_steering:
                self._steering[event.session_id] = events[-self._max_steering :]

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
        async with self._lock:
            tasks = list(self._tasks.get(session_id, {}).values())
        return [_clone_task(state) for state in tasks]

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]:
        async with self._lock:
            updates = list(self._updates.get(session_id, []))
        start_index = 0
        if since_id:
            for idx, update in enumerate(updates):
                if update.update_id == since_id:
                    start_index = idx + 1
                    break
        filtered = []
        for update in updates[start_index:]:
            if task_id is not None and update.task_id != task_id:
                continue
            filtered.append(update)
        return filtered[-limit:]

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]:
        async with self._lock:
            events = list(self._steering.get(session_id, []))
        start_index = 0
        if since_id:
            for idx, event in enumerate(events):
                if event.event_id == since_id:
                    start_index = idx + 1
                    break
        filtered = []
        for event in events[start_index:]:
            if task_id is not None and event.task_id != task_id:
                continue
            filtered.append(event)
        return filtered[-limit:]


class StateStoreSessionAdapter(SessionStateStore):
    """Adapter that persists session updates into a core StateStore audit log."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def _trace_id(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def save_task(self, state: TaskState) -> None:
        payload = TaskStateModel.from_state(state).model_dump(mode="json")
        await self._store.save_event(
            StoredEvent(
                trace_id=self._trace_id(state.session_id),
                ts=time.time(),
                kind="session.task",
                node_name=None,
                node_id=state.task_id,
                payload=payload,
            )
        )

    async def save_update(self, update: StateUpdate) -> None:
        payload = update.model_dump(mode="json")
        await self._store.save_event(
            StoredEvent(
                trace_id=self._trace_id(update.session_id),
                ts=time.time(),
                kind="session.update",
                node_name=None,
                node_id=update.task_id,
                payload=payload,
            )
        )

    async def save_steering(self, event: SteeringEvent) -> None:
        payload = event.model_dump(mode="json")
        await self._store.save_event(
            StoredEvent(
                trace_id=self._trace_id(event.session_id),
                ts=time.time(),
                kind="session.steering",
                node_name=None,
                node_id=event.task_id,
                payload=payload,
            )
        )

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
        history = await self._store.load_history(self._trace_id(session_id))
        latest: dict[str, TaskState] = {}
        for event in history:
            if event.kind != "session.task":
                continue
            payload = event.payload
            if not isinstance(payload, dict):
                continue
            model = TaskStateModel.model_validate(payload)
            latest[model.task_id] = TaskState(
                task_id=model.task_id,
                session_id=model.session_id,
                status=model.status,
                task_type=model.task_type,
                priority=model.priority,
                context_snapshot=model.context_snapshot,
                trace_id=model.trace_id,
                result=model.result,
                error=model.error,
                description=model.description,
                progress=model.progress,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        return list(latest.values())

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]:
        history = await self._store.load_history(self._trace_id(session_id))
        updates: list[StateUpdate] = []
        for event in history:
            if event.kind != "session.update":
                continue
            payload = event.payload
            if not isinstance(payload, dict):
                continue
            update = StateUpdate.model_validate(payload)
            if task_id is not None and update.task_id != task_id:
                continue
            updates.append(update)
        if since_id:
            for idx, update in enumerate(updates):
                if update.update_id == since_id:
                    updates = updates[idx + 1 :]
                    break
        return updates[-limit:]

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]:
        history = await self._store.load_history(self._trace_id(session_id))
        events: list[SteeringEvent] = []
        for event in history:
            if event.kind != "session.steering":
                continue
            payload = event.payload
            if not isinstance(payload, dict):
                continue
            steering = SteeringEvent.model_validate(payload)
            if task_id is not None and steering.task_id != task_id:
                continue
            events.append(steering)
        if since_id:
            for idx, steering_event in enumerate(events):
                if steering_event.event_id == since_id:
                    events = events[idx + 1 :]
                    break
        return events[-limit:]


__all__ = [
    "InMemorySessionStateStore",
    "SessionStateStore",
    "StateStoreSessionAdapter",
]
