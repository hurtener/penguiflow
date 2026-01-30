"""Protocols describing the unified StateStore surface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactStore
    from penguiflow.planner import PlannerEvent, Trajectory
    from penguiflow.state.models import StateUpdate, SteeringEvent, TaskState

from .models import RemoteBinding, StoredEvent


@runtime_checkable
class StateStore(Protocol):
    """Protocol for durable state adapters used by PenguiFlow.

    Only the core audit-log methods are required. Additional subsystems detect
    optional capabilities via duck-typing (``hasattr`` / ``getattr``).
    """

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event.

        Implementations may choose any storage backend (Postgres, Redis, etc.).
        The method must be idempotent since retries can emit duplicate events.
        """

        raise NotImplementedError()
        return None

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Return the ordered history for a trace id."""

        raise NotImplementedError()
        return []

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist the mapping between a trace and an external worker."""

        raise NotImplementedError()
        return None


@runtime_checkable
class SupportsPlannerState(Protocol):
    async def save_planner_state(self, token: str, payload: dict[str, Any]) -> None: ...

    async def load_planner_state(self, token: str) -> dict[str, Any] | None: ...


@runtime_checkable
class SupportsMemoryState(Protocol):
    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None: ...

    async def load_memory_state(self, key: str) -> dict[str, Any] | None: ...


@runtime_checkable
class SupportsTasks(Protocol):
    async def save_task(self, state: TaskState) -> None: ...

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]: ...

    async def save_update(self, update: StateUpdate) -> None: ...

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]: ...


@runtime_checkable
class SupportsSteering(Protocol):
    async def save_steering(self, event: SteeringEvent) -> None: ...

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]: ...


@runtime_checkable
class SupportsTrajectories(Protocol):
    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None: ...

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None: ...

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]: ...


@runtime_checkable
class SupportsPlannerEvents(Protocol):
    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None: ...

    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]: ...


@runtime_checkable
class SupportsArtifacts(Protocol):
    @property
    def artifact_store(self) -> ArtifactStore | None: ...


def missing_capabilities(store: object, methods: Sequence[str]) -> list[str]:
    """Return missing attribute names from ``methods``."""

    return [method for method in methods if not hasattr(store, method)]


def require_capabilities(store: object, *, feature: str, methods: Sequence[str]) -> None:
    """Fail fast when a StateStore is missing required optional capabilities."""

    missing = missing_capabilities(store, methods)
    if missing:
        raise TypeError(f"StateStore missing {missing} required for feature={feature}")


__all__ = [
    "StateStore",
    "SupportsArtifacts",
    "SupportsMemoryState",
    "SupportsPlannerEvents",
    "SupportsPlannerState",
    "SupportsSteering",
    "SupportsTasks",
    "SupportsTrajectories",
    "missing_capabilities",
    "require_capabilities",
]
