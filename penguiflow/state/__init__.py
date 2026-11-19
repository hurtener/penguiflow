"""Unified state store protocols and reference implementations.

Note: this module is intentionally lightweight to avoid import cycles. The
reference implementation lives in `penguiflow.state.in_memory`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    SupportsConversationBindings,
    SupportsMemoryState,
    SupportsPlannerEvents,
    SupportsPlannerState,
    SupportsSteering,
    SupportsTasks,
    SupportsTrajectories,
    missing_capabilities,
    require_capabilities,
)

if TYPE_CHECKING:
    from .in_memory import InMemoryStateStore, SessionArtifactStore

__all__ = [
    "InMemoryStateStore",
    "RemoteBinding",
    "SessionArtifactStore",
    "StateStore",
    "StateUpdate",
    "SteeringEvent",
    "SteeringEventType",
    "StoredEvent",
    "SupportsArtifacts",
    "SupportsConversationBindings",
    "SupportsMemoryState",
    "SupportsPlannerEvents",
    "SupportsPlannerState",
    "SupportsSteering",
    "SupportsTasks",
    "SupportsTrajectories",
    "TaskContextSnapshot",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskType",
    "UpdateType",
    "missing_capabilities",
    "require_capabilities",
]


def __getattr__(name: str) -> type[object]:  # pragma: no cover - lazy
    if name in {"InMemoryStateStore", "SessionArtifactStore"}:
        from .in_memory import InMemoryStateStore, SessionArtifactStore

        return {
            "InMemoryStateStore": InMemoryStateStore,
            "SessionArtifactStore": SessionArtifactStore,
        }[name]
    raise AttributeError(name)
