"""State storage protocol for the playground backend."""

from __future__ import annotations

import asyncio
from typing import Protocol

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


class InMemoryStateStore(PlaygroundStateStore):
    """Simple in-memory playground store with session isolation."""

    def __init__(self) -> None:
        self._trajectories: dict[str, tuple[str, Trajectory]] = {}
        self._session_index: dict[str, list[str]] = {}
        self._events: dict[str, list[PlannerEvent]] = {}
        self._lock = asyncio.Lock()

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


__all__ = ["InMemoryStateStore", "PlaygroundStateStore"]
