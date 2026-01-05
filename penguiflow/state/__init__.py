"""Unified state store protocols and reference implementations.

Note: this module is intentionally lightweight to avoid import cycles.
The reference implementation lives in `penguiflow.state.in_memory`.
"""

from __future__ import annotations

from typing import Any

from .models import (
    RemoteBinding,
    StateUpdate,
    SteeringEvent,
    SteeringEventType,
    StoredEvent,
    TaskContextSnapshot,
    TaskState,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
)
from .protocol import (
    StateStore,
    SupportsArtifacts,
    SupportsMemoryState,
    SupportsPlannerEvents,
    SupportsPlannerState,
    SupportsSteering,
    SupportsTasks,
    SupportsTrajectories,
    missing_capabilities,
    require_capabilities,
)

__all__ = [
    "RemoteBinding",
    "StateUpdate",
    "StateStore",
    "SteeringEvent",
    "SteeringEventType",
    "StoredEvent",
    "TaskContextSnapshot",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskType",
    "SupportsArtifacts",
    "SupportsMemoryState",
    "SupportsPlannerEvents",
    "SupportsPlannerState",
    "SupportsSteering",
    "SupportsTasks",
    "SupportsTrajectories",
    "UpdateType",
    "missing_capabilities",
    "require_capabilities",
]


def __getattr__(name: str) -> Any:  # pragma: no cover - exercised indirectly
    if name in {"InMemoryStateStore", "PlaygroundArtifactStore"}:
        from .in_memory import InMemoryStateStore, PlaygroundArtifactStore

        return {"InMemoryStateStore": InMemoryStateStore, "PlaygroundArtifactStore": PlaygroundArtifactStore}[name]
    raise AttributeError(name)
