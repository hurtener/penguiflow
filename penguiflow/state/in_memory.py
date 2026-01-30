"""Reference in-memory StateStore implementation.

This implementation is intended for development, testing, and the Playground.
It implements all optional capabilities used by PenguiFlow subsystems.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
)
from penguiflow.planner import PlannerEvent, Trajectory
from penguiflow.steering import sanitize_steering_event

from .models import RemoteBinding, StateUpdate, SteeringEvent, StoredEvent, TaskState


def _fingerprint(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clone_task(state: TaskState) -> TaskState:
    # TaskState is a mutable dataclass; clone defensively to prevent accidental mutation of stored state.
    return replace(
        state,
        context_snapshot=state.context_snapshot.model_copy(deep=True),
        progress=dict(state.progress) if state.progress else None,
    )


def _clone_update(update: StateUpdate) -> StateUpdate:
    return update.model_copy(deep=True)


def _clone_trajectory(trajectory: Trajectory) -> Trajectory:
    return Trajectory.from_serialised(trajectory.serialise())


class PlaygroundArtifactStore:
    """Session-aware artifact store for the Playground.

    Wraps :class:`~penguiflow.artifacts.InMemoryArtifactStore` with session-scoped
    isolation and exposes access-control helpers for the HTTP layer.
    """

    def __init__(
        self,
        retention: ArtifactRetentionConfig | None = None,
    ) -> None:
        self._retention = retention or ArtifactRetentionConfig()
        self._stores: dict[str, InMemoryArtifactStore] = {}
        self._artifact_index: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_store(self, session_id: str) -> InMemoryArtifactStore:
        if session_id not in self._stores:
            self._stores[session_id] = InMemoryArtifactStore(
                retention=self._retention,
                scope_filter=ArtifactScope(session_id=session_id),
            )
        return self._stores[session_id]

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        session_id = scope.session_id if scope is not None and scope.session_id is not None else "default"
        async with self._lock:
            store = self._get_or_create_store(session_id)
        ref = await store.put_bytes(
            data,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=scope,
            meta=meta,
        )
        async with self._lock:
            self._artifact_index[ref.id] = session_id
        return ref

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        session_id = scope.session_id if scope is not None and scope.session_id is not None else "default"
        async with self._lock:
            store = self._get_or_create_store(session_id)
        ref = await store.put_text(
            text,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=scope,
            meta=meta,
        )
        async with self._lock:
            self._artifact_index[ref.id] = session_id
        return ref

    async def get(self, artifact_id: str) -> bytes | None:
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get(artifact_id)

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get_ref(artifact_id)

    async def get_with_session_check(self, artifact_id: str, session_id: str) -> bytes | None:
        async with self._lock:
            stored_session = self._artifact_index.get(artifact_id)
            if stored_session is None or stored_session != session_id:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get(artifact_id)

    async def delete(self, artifact_id: str) -> bool:
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return False
            store = self._stores.get(session_id)
            if store is None:
                return False
        result = await store.delete(artifact_id)
        if result:
            async with self._lock:
                self._artifact_index.pop(artifact_id, None)
        return result

    async def exists(self, artifact_id: str) -> bool:
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return False
            store = self._stores.get(session_id)
            if store is None:
                return False
        return await store.exists(artifact_id)

    def clear_session(self, session_id: str) -> None:
        store = self._stores.pop(session_id, None)
        if store is not None:
            to_remove = [aid for aid, sid in self._artifact_index.items() if sid == session_id]
            for aid in to_remove:
                self._artifact_index.pop(aid, None)
            store.clear()

    def clear(self) -> None:
        """Clear all artifacts across all sessions (testing utility)."""
        self._stores.clear()
        self._artifact_index.clear()


class InMemoryStateStore:
    """Complete in-memory StateStore for development and testing."""

    def __init__(
        self,
        *,
        max_events_per_trace: int = 50_000,
        max_updates_per_session: int = 10_000,
        max_steering_events_per_session: int = 10_000,
        max_planner_events_per_trace: int = 50_000,
        artifact_retention: ArtifactRetentionConfig | None = None,
    ) -> None:
        self._max_events_per_trace = max_events_per_trace
        self._max_updates_per_session = max_updates_per_session
        self._max_steering_per_session = max_steering_events_per_session
        self._max_planner_events_per_trace = max_planner_events_per_trace

        self._events: dict[str, list[tuple[float, int, str, StoredEvent]]] = defaultdict(list)
        self._event_fingerprints: dict[str, set[str]] = defaultdict(set)
        self._event_seq = 0

        self._bindings: dict[tuple[str, str | None, str], RemoteBinding] = {}
        self._planner_state: dict[str, dict[str, Any]] = {}
        self._memory_state: dict[str, dict[str, Any]] = {}

        self._tasks: dict[str, dict[str, TaskState]] = {}
        self._updates: dict[str, list[StateUpdate]] = defaultdict(list)
        self._steering: dict[str, list[SteeringEvent]] = defaultdict(list)

        self._trajectories: dict[str, tuple[str, Trajectory]] = {}
        self._session_traces: dict[str, list[str]] = defaultdict(list)

        self._planner_events: dict[str, list[PlannerEvent]] = defaultdict(list)

        self._artifact_store = PlaygroundArtifactStore(retention=artifact_retention)

        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------------
    # Core audit log (required)
    # ---------------------------------------------------------------------

    async def save_event(self, event: StoredEvent) -> None:
        trace_id = event.trace_id or "__global__"
        fp = _fingerprint(
            {
                "trace_id": event.trace_id,
                "ts": event.ts,
                "kind": event.kind,
                "node_name": event.node_name,
                "node_id": event.node_id,
                "payload": dict(event.payload),
            }
        )
        async with self._lock:
            if fp in self._event_fingerprints[trace_id]:
                return
            self._event_fingerprints[trace_id].add(fp)
            self._event_seq += 1
            self._events[trace_id].append((event.ts, self._event_seq, fp, event))
            if self._max_events_per_trace > 0 and len(self._events[trace_id]) > self._max_events_per_trace:
                _, _, old_fp, _ = self._events[trace_id].pop(0)
                self._event_fingerprints[trace_id].discard(old_fp)

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        async with self._lock:
            entries = list(self._events.get(trace_id, []))
        entries.sort(key=lambda item: (item[0], item[1]))
        return [event for _, _, _, event in entries]

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        async with self._lock:
            self._bindings[(binding.trace_id, binding.context_id, binding.task_id)] = binding

    # ---------------------------------------------------------------------
    # Optional - Planner pause/resume
    # ---------------------------------------------------------------------

    async def save_planner_state(self, token: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._planner_state[token] = dict(payload)

    async def load_planner_state(self, token: str) -> dict[str, Any] | None:
        async with self._lock:
            payload = self._planner_state.pop(token, None)
        return dict(payload) if payload is not None else None

    # ---------------------------------------------------------------------
    # Optional - Short-term memory persistence
    # ---------------------------------------------------------------------

    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
        async with self._lock:
            self._memory_state[key] = dict(state)

    async def load_memory_state(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            stored = self._memory_state.get(key)
        return dict(stored) if stored is not None else None

    # ---------------------------------------------------------------------
    # Optional - Tasks + updates
    # ---------------------------------------------------------------------

    async def save_task(self, state: TaskState) -> None:
        async with self._lock:
            self._tasks.setdefault(state.session_id, {})[state.task_id] = _clone_task(state)

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
        async with self._lock:
            tasks = list(self._tasks.get(session_id, {}).values())
        return [_clone_task(task) for task in tasks]

    async def save_update(self, update: StateUpdate) -> None:
        async with self._lock:
            updates = self._updates[update.session_id]
            updates.append(_clone_update(update))
            if self._max_updates_per_session > 0 and len(updates) > self._max_updates_per_session:
                self._updates[update.session_id] = updates[-self._max_updates_per_session :]

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

        filtered: list[StateUpdate] = []
        for update in updates[start_index:]:
            if task_id is not None and update.task_id != task_id:
                continue
            filtered.append(_clone_update(update))
        return filtered[-limit:]

    # Legacy alias (RFC_UNIFIED_STATESTORE.md compatibility during migration)
    async def save_task_update(self, update: StateUpdate) -> None:
        await self.save_update(update)

    # Legacy alias (RFC_UNIFIED_STATESTORE.md compatibility during migration)
    async def list_task_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]:
        return await self.list_updates(session_id, task_id=task_id, since_id=since_id, limit=limit)

    # ---------------------------------------------------------------------
    # Optional - Steering
    # ---------------------------------------------------------------------

    async def save_steering(self, event: SteeringEvent) -> None:
        sanitized = sanitize_steering_event(event)
        async with self._lock:
            events = self._steering[event.session_id]
            events.append(sanitized)
            if self._max_steering_per_session > 0 and len(events) > self._max_steering_per_session:
                self._steering[event.session_id] = events[-self._max_steering_per_session :]

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

        filtered: list[SteeringEvent] = []
        for event in events[start_index:]:
            if task_id is not None and event.task_id != task_id:
                continue
            filtered.append(event.model_copy(deep=True))
        return filtered[-limit:]

    # ---------------------------------------------------------------------
    # Optional - Trajectories
    # ---------------------------------------------------------------------

    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None:
        async with self._lock:
            self._trajectories[trace_id] = (session_id, _clone_trajectory(trajectory))
            traces = self._session_traces[session_id]
            if trace_id in traces:
                traces.remove(trace_id)
            traces.append(trace_id)

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None:
        async with self._lock:
            entry = self._trajectories.get(trace_id)
        if entry is None:
            return None
        stored_session, trajectory = entry
        if stored_session != session_id:
            return None
        return _clone_trajectory(trajectory)

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]:
        async with self._lock:
            traces = list(self._session_traces.get(session_id, []))
        if not traces:
            return []
        return list(reversed(traces))[:limit]

    # ---------------------------------------------------------------------
    # Optional - Planner events
    # ---------------------------------------------------------------------

    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None:
        async with self._lock:
            events = self._planner_events[trace_id]
            events.append(event)
            if self._max_planner_events_per_trace > 0 and len(events) > self._max_planner_events_per_trace:
                self._planner_events[trace_id] = events[-self._max_planner_events_per_trace :]

    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]:
        async with self._lock:
            return list(self._planner_events.get(trace_id, []))

    # Legacy alias for older Playground internals/tests.
    async def get_events(self, trace_id: str) -> list[PlannerEvent]:
        return await self.list_planner_events(trace_id)

    # ---------------------------------------------------------------------
    # Optional - Artifacts
    # ---------------------------------------------------------------------

    @property
    def artifact_store(self) -> ArtifactStore:
        return self._artifact_store

    # ---------------------------------------------------------------------
    # Utilities (testing)
    # ---------------------------------------------------------------------

    def clear(self) -> None:
        self._events.clear()
        self._event_fingerprints.clear()
        self._bindings.clear()
        self._planner_state.clear()
        self._memory_state.clear()
        self._tasks.clear()
        self._updates.clear()
        self._steering.clear()
        self._trajectories.clear()
        self._session_traces.clear()
        self._planner_events.clear()
        self._artifact_store.clear()

    def clear_session(self, session_id: str) -> None:
        self._tasks.pop(session_id, None)
        self._updates.pop(session_id, None)
        self._steering.pop(session_id, None)
        self._artifact_store.clear_session(session_id)

        traces = self._session_traces.pop(session_id, [])
        for trace_id in traces:
            self._trajectories.pop(trace_id, None)
            self._planner_events.pop(trace_id, None)

            # Also remove audit log entries keyed by trace_id if present.
            self._events.pop(trace_id, None)
            self._event_fingerprints.pop(trace_id, None)


__all__ = ["InMemoryStateStore", "PlaygroundArtifactStore"]
