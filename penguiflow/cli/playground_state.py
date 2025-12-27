"""State storage protocol for the playground backend."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
)
from penguiflow.planner import PlannerEvent, Trajectory


class PlaygroundStateStore(Protocol):
    """Protocol for storing planner state and events in the playground."""

    async def save_trajectory(
        self,
        trace_id: str,
        session_id: str,
        trajectory: Trajectory,
    ) -> None: ...

    async def get_trajectory(
        self,
        trace_id: str,
        session_id: str,
    ) -> Trajectory | None: ...

    async def list_traces(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[str]: ...

    async def save_event(
        self,
        trace_id: str,
        event: PlannerEvent,
    ) -> None: ...

    async def get_events(
        self,
        trace_id: str,
    ) -> list[PlannerEvent]: ...

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the artifact store for binary content storage."""
        ...


class PlaygroundArtifactStore:
    """Session-aware artifact store for the Playground.

    Wraps InMemoryArtifactStore with session-scoped isolation and
    provides access control helpers for the HTTP layer.
    """

    def __init__(
        self,
        retention: ArtifactRetentionConfig | None = None,
    ) -> None:
        self._retention = retention or ArtifactRetentionConfig()
        # Session-scoped stores
        self._stores: dict[str, InMemoryArtifactStore] = {}
        # Global artifact index for cross-session lookups (artifact_id -> session_id)
        self._artifact_index: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_store(self, session_id: str) -> InMemoryArtifactStore:
        """Get or create the artifact store for a session."""
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
        """Store binary data with session scoping."""
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
        """Store large text with session scoping."""
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
        """Retrieve artifact bytes by ID."""
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get(artifact_id)

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Retrieve artifact metadata by ID."""
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get_ref(artifact_id)

    async def get_with_session_check(
        self,
        artifact_id: str,
        session_id: str,
    ) -> bytes | None:
        """Retrieve artifact bytes with session validation.

        Returns None if artifact doesn't exist or belongs to a different session.
        Use this for HTTP endpoints to enforce session isolation.
        """
        async with self._lock:
            stored_session = self._artifact_index.get(artifact_id)
            if stored_session is None or stored_session != session_id:
                return None
            store = self._stores.get(session_id)
            if store is None:
                return None
        return await store.get(artifact_id)

    async def delete(self, artifact_id: str) -> bool:
        """Delete an artifact."""
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
        """Check if an artifact exists."""
        async with self._lock:
            session_id = self._artifact_index.get(artifact_id)
            if session_id is None:
                return False
            store = self._stores.get(session_id)
            if store is None:
                return False
        return await store.exists(artifact_id)

    def clear_session(self, session_id: str) -> None:
        """Clear all artifacts for a session."""
        store = self._stores.pop(session_id, None)
        if store is not None:
            # Remove from global index
            to_remove = [
                aid for aid, sid in self._artifact_index.items() if sid == session_id
            ]
            for aid in to_remove:
                self._artifact_index.pop(aid, None)
            store.clear()


class InMemoryStateStore(PlaygroundStateStore):
    """Simple in-memory playground store with session isolation.

    Includes an integrated artifact store that can be discovered by ReactPlanner.
    """

    def __init__(
        self,
        artifact_retention: ArtifactRetentionConfig | None = None,
    ) -> None:
        self._trajectories: dict[str, tuple[str, Trajectory]] = {}
        self._session_index: dict[str, list[str]] = {}
        self._events: dict[str, list[PlannerEvent]] = {}
        self._lock = asyncio.Lock()
        self._artifact_store = PlaygroundArtifactStore(retention=artifact_retention)

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the artifact store for binary content storage.

        This property enables discovery by ReactPlanner via discover_artifact_store().
        """
        return self._artifact_store

    async def save_trajectory(
        self,
        trace_id: str,
        session_id: str,
        trajectory: Trajectory,
    ) -> None:
        async with self._lock:
            self._trajectories[trace_id] = (session_id, trajectory)
            traces = self._session_index.setdefault(session_id, [])
            traces.append(trace_id)

    async def get_trajectory(
        self,
        trace_id: str,
        session_id: str,
    ) -> Trajectory | None:
        async with self._lock:
            entry = self._trajectories.get(trace_id)
            if entry is None:
                return None
            stored_session, trajectory = entry
            if stored_session != session_id:
                return None
            return trajectory

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]:
        async with self._lock:
            traces = list(self._session_index.get(session_id, []))
            if not traces:
                return []
            return list(reversed(traces))[:limit]

    async def save_event(self, trace_id: str, event: PlannerEvent) -> None:
        async with self._lock:
            events = self._events.setdefault(trace_id, [])
            events.append(event)

    async def get_events(self, trace_id: str) -> list[PlannerEvent]:
        async with self._lock:
            return list(self._events.get(trace_id, []))


__all__ = ["InMemoryStateStore", "PlaygroundArtifactStore", "PlaygroundStateStore"]
