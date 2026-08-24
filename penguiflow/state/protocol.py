"""Protocols describing the unified StateStore surface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactStore
    from penguiflow.planner import PlannerEvent, Trajectory
    from penguiflow.state.models import StateUpdate, SteeringEvent, TaskState

from .models import RemoteBinding, StoredEvent


class TraceRef(TypedDict):
    """Typed dictionary-like trace reference.

    Why: dataset export needs to resolve trajectories across sessions. Carrying
    ``trace_id`` and ``session_id`` together avoids accidental mismatches when
    filtering by tags globally.
    """

    trace_id: str
    session_id: str


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
    """Optional StateStore capability for persisting planner checkpoint state.

    Detected via duck-typing (``hasattr``); a store need not implement this unless the
    planner checkpoint/resume feature is used.
    """

    async def save_planner_state(self, token: str, payload: dict[str, Any]) -> None:
        """Persist planner state under an opaque resumption token.

        Args:
            token: Opaque identifier used to look up the state later.
            payload: Serializable planner state to store.
        """
        ...

    async def load_planner_state(self, token: str) -> dict[str, Any] | None:
        """Load previously saved planner state for a resumption token.

        Args:
            token: Opaque identifier previously passed to `save_planner_state`.

        Returns:
            The stored payload, or None if no state exists for `token`.
        """
        ...


@runtime_checkable
class SupportsConversationBindings(Protocol):
    """Optional StateStore capability for tracking remote-agent conversation bindings.

    A binding associates a router session with a remote agent/skill invocation so that
    follow-up turns can be routed back to the same remote task/context.
    """

    async def find_binding(
        self,
        *,
        router_session_id: str,
        agent_url: str,
        remote_skill: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> RemoteBinding | None:
        """Find an existing, non-terminal binding matching the given coordinates.

        Args:
            router_session_id: Local session id issuing the remote call.
            agent_url: URL of the remote agent.
            remote_skill: Name of the remote skill/capability being invoked.
            tenant_id: Optional tenant scope to match.
            user_id: Optional user scope to match.

        Returns:
            The matching binding, or None if no such binding exists.
        """
        ...

    async def list_bindings(self, *, router_session_id: str) -> Sequence[RemoteBinding]:
        """List all bindings recorded for a router session.

        Args:
            router_session_id: Local session id to look up bindings for.

        Returns:
            The bindings associated with the session, in storage order.
        """
        ...

    async def mark_binding_terminal(self, *, trace_id: str, context_id: str | None, task_id: str) -> None:
        """Mark a binding as terminal so it is no longer reused for follow-up turns.

        Args:
            trace_id: Trace id of the binding to mark.
            context_id: Optional remote context id associated with the binding.
            task_id: Remote task id associated with the binding.
        """
        ...


@runtime_checkable
class SupportsMemoryState(Protocol):
    """Optional StateStore capability for persisting arbitrary keyed memory state."""

    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
        """Persist memory state under a key.

        Args:
            key: Identifier for the memory slot.
            state: Serializable state to store.
        """
        ...

    async def load_memory_state(self, key: str) -> dict[str, Any] | None:
        """Load previously saved memory state for a key.

        Args:
            key: Identifier previously passed to `save_memory_state`.

        Returns:
            The stored state, or None if no state exists for `key`.
        """
        ...


@runtime_checkable
class SupportsTasks(Protocol):
    """Optional StateStore capability for persisting background/foreground task state."""

    async def save_task(self, state: TaskState) -> None:
        """Persist (create or update) a task's state.

        Args:
            state: The task state to persist.
        """
        ...

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
        """List all tasks recorded for a session.

        Args:
            session_id: Session id to look up tasks for.

        Returns:
            The tasks associated with the session.
        """
        ...

    async def save_update(self, update: StateUpdate) -> None:
        """Persist a task progress/status update.

        Args:
            update: The update to persist.
        """
        ...

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]:
        """List updates recorded for a session, optionally filtered and paginated.

        Args:
            session_id: Session id to look up updates for.
            task_id: Optional task id to restrict results to.
            since_id: Optional update id; only updates after this one are returned.
            limit: Maximum number of updates to return.

        Returns:
            Matching updates in chronological order, bounded by `limit`.
        """
        ...


@runtime_checkable
class SupportsSteering(Protocol):
    """Optional StateStore capability for persisting mid-run steering events.

    Steering events represent externally injected control actions (pause, resume,
    redirect, cancel, user messages, etc.) applied to a running task.
    """

    async def save_steering(self, event: SteeringEvent) -> None:
        """Persist a steering event.

        Args:
            event: The steering event to persist.
        """
        ...

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]:
        """List steering events recorded for a session, optionally filtered and paginated.

        Args:
            session_id: Session id to look up steering events for.
            task_id: Optional task id to restrict results to.
            since_id: Optional event id; only events after this one are returned.
            limit: Maximum number of events to return.

        Returns:
            Matching steering events in chronological order, bounded by `limit`.
        """
        ...


@runtime_checkable
class SupportsTrajectories(Protocol):
    """Optional StateStore capability for persisting ReactPlanner trajectories."""

    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None:
        """Persist a planner trajectory for a trace.

        Args:
            trace_id: Trace id the trajectory belongs to.
            session_id: Session id the trace belongs to.
            trajectory: The trajectory to persist.
        """
        ...

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None:
        """Load a previously saved trajectory.

        Args:
            trace_id: Trace id of the trajectory to load.
            session_id: Session id the trace belongs to.

        Returns:
            The stored trajectory, or None if none exists.
        """
        ...

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]:
        """List trace ids with a stored trajectory for a session.

        Args:
            session_id: Session id to look up trace ids for.
            limit: Maximum number of trace ids to return.

        Returns:
            Trace ids, most recent first, bounded by `limit`.
        """
        ...


@runtime_checkable
class SupportsTraceQuery(Protocol):
    async def list_trace_refs(self, limit: int = 0) -> list[TraceRef]: ...


@runtime_checkable
class SupportsPlannerEvents(Protocol):
    """Optional StateStore capability for persisting ReactPlanner events for a trace."""

    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None:
        """Persist a planner event.

        Args:
            trace_id: Trace id the event belongs to.
            event: The planner event to persist.
        """
        ...

    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]:
        """List planner events recorded for a trace.

        Args:
            trace_id: Trace id to look up events for.

        Returns:
            The events for the trace, in the order they were saved.
        """
        ...


@runtime_checkable
class SupportsArtifacts(Protocol):
    """Optional StateStore capability for exposing an associated artifact store."""

    @property
    def artifact_store(self) -> ArtifactStore | None:
        """The artifact store associated with this state store, if any.

        Returns:
            An `ArtifactStore` instance, or None if artifacts are not configured.
        """
        ...


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
    "SupportsConversationBindings",
    "SupportsMemoryState",
    "SupportsPlannerEvents",
    "SupportsPlannerState",
    "SupportsSteering",
    "SupportsTasks",
    "SupportsTraceQuery",
    "SupportsTrajectories",
    "TraceRef",
    "missing_capabilities",
    "require_capabilities",
]
