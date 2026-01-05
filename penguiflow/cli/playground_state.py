"""Deprecated Playground state store shim.

The Playground backend uses the unified `StateStore` optional capabilities for:
- trajectories (`save_trajectory`, `get_trajectory`, `list_traces`)
- planner events (`save_planner_event`, `list_planner_events`)
- artifacts (`artifact_store`)

This module remains for backward compatibility with earlier Playground internals.
"""

from __future__ import annotations

from typing import Protocol

from penguiflow.artifacts import ArtifactStore
from penguiflow.planner import PlannerEvent, Trajectory
from penguiflow.state.in_memory import InMemoryStateStore, PlaygroundArtifactStore


class PlaygroundStateStore(Protocol):
    """Protocol for the subset of StateStore used by the Playground."""

    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None: ...

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None: ...

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]: ...

    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None: ...

    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]: ...

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the artifact store for binary content storage."""
        ...


__all__ = ["InMemoryStateStore", "PlaygroundArtifactStore", "PlaygroundStateStore"]

