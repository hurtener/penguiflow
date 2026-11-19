"""Compatibility helpers for legacy store interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from penguiflow.artifacts import ArtifactStore, discover_artifact_store

if TYPE_CHECKING:
    from penguiflow.planner import PlannerEvent
    from penguiflow.state.models import (
        RemoteBinding,
        StateUpdate,
        SteeringEvent,
        TaskState,
    )

    from .protocol import StateStore


def get_artifact_store(store: object) -> ArtifactStore | None:
    """Return the artifact store exposed by ``store``, if any."""
    return discover_artifact_store(store)


async def save_update_compat(store: object, update: StateUpdate) -> None:
    """Save a task update with whichever update method ``store`` provides."""
    method = getattr(store, "save_update", None) or getattr(
        store, "save_task_update", None
    )
    if method is None:
        msg = "StateStore missing save_update/save_task_update"
        raise TypeError(msg)
    await method(update)


async def list_updates_compat(
    store: object,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> list[StateUpdate]:
    """List a session's updates with the list method ``store`` provides."""
    method = getattr(store, "list_updates", None) or getattr(
        store, "list_task_updates", None
    )
    if method is None:
        msg = "StateStore missing list_updates/list_task_updates"
        raise TypeError(msg)
    return list(
        await method(
            session_id, task_id=task_id, since_id=since_id, limit=limit
        )
    )


async def save_task_compat(store: object, state: TaskState) -> None:
    """Save a task state; ``store`` must provide ``save_task``."""
    method = getattr(store, "save_task", None)
    if method is None:
        msg = "StateStore missing save_task"
        raise TypeError(msg)
    await method(state)


async def list_tasks_compat(store: object, session_id: str) -> list[TaskState]:
    """List a session's tasks; ``store`` must provide ``list_tasks``."""
    method = getattr(store, "list_tasks", None)
    if method is None:
        msg = "StateStore missing list_tasks"
        raise TypeError(msg)
    return list(await method(session_id))


async def save_steering_compat(store: object, event: SteeringEvent) -> None:
    """Save a steering event; ``store`` must provide ``save_steering``."""
    method = getattr(store, "save_steering", None)
    if method is None:
        msg = "StateStore missing save_steering"
        raise TypeError(msg)
    await method(event)


async def list_steering_compat(
    store: object,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> list[SteeringEvent]:
    """List a session's steering events via ``store.list_steering``."""
    method = getattr(store, "list_steering", None)
    if method is None:
        msg = "StateStore missing list_steering"
        raise TypeError(msg)
    return list(
        await method(
            session_id, task_id=task_id, since_id=since_id, limit=limit
        )
    )


async def save_planner_event_compat(
    store: object, trace_id: str, event: PlannerEvent
) -> None:
    """Save a planner event if ``store`` supports it; otherwise do nothing."""
    method = getattr(store, "save_planner_event", None)
    if method is not None:
        await method(trace_id, event)
        return
    msg = "StateStore missing save_planner_event"
    raise TypeError(msg)


async def list_planner_events_compat(
    store: object, trace_id: str
) -> list[PlannerEvent]:
    """List a trace's planner events; ``store`` must provide the method."""
    method = getattr(store, "list_planner_events", None)
    if method is not None:
        return list(await method(trace_id))
    msg = "StateStore missing list_planner_events"
    raise TypeError(msg)


async def maybe_save_remote_binding(
    store: StateStore | None, binding: RemoteBinding
) -> None:
    """Save ``binding`` when a store is configured."""
    if store is None:
        return
    await store.save_remote_binding(binding)


__all__ = [
    "get_artifact_store",
    "list_planner_events_compat",
    "list_steering_compat",
    "list_tasks_compat",
    "list_updates_compat",
    "maybe_save_remote_binding",
    "save_planner_event_compat",
    "save_steering_compat",
    "save_task_compat",
    "save_update_compat",
]
