"""Compatibility helpers for legacy store interfaces."""

from __future__ import annotations

from typing import Any

from penguiflow.artifacts import ArtifactStore, discover_artifact_store
from penguiflow.planner import PlannerEvent
from penguiflow.state.models import StateUpdate, SteeringEvent, TaskState

from .protocol import StateStore


def get_artifact_store(store: object) -> ArtifactStore | None:
    return discover_artifact_store(store)


async def save_update_compat(store: object, update: StateUpdate) -> None:
    method = getattr(store, "save_update", None) or getattr(store, "save_task_update", None)
    if method is None:
        raise TypeError("StateStore missing save_update/save_task_update")
    await method(update)


async def list_updates_compat(
    store: object,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> list[StateUpdate]:
    method = getattr(store, "list_updates", None) or getattr(store, "list_task_updates", None)
    if method is None:
        raise TypeError("StateStore missing list_updates/list_task_updates")
    return list(await method(session_id, task_id=task_id, since_id=since_id, limit=limit))


async def save_task_compat(store: object, state: TaskState) -> None:
    method = getattr(store, "save_task", None)
    if method is None:
        raise TypeError("StateStore missing save_task")
    await method(state)


async def list_tasks_compat(store: object, session_id: str) -> list[TaskState]:
    method = getattr(store, "list_tasks", None)
    if method is None:
        raise TypeError("StateStore missing list_tasks")
    return list(await method(session_id))


async def save_steering_compat(store: object, event: SteeringEvent) -> None:
    method = getattr(store, "save_steering", None)
    if method is None:
        raise TypeError("StateStore missing save_steering")
    await method(event)


async def list_steering_compat(
    store: object,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> list[SteeringEvent]:
    method = getattr(store, "list_steering", None)
    if method is None:
        raise TypeError("StateStore missing list_steering")
    return list(await method(session_id, task_id=task_id, since_id=since_id, limit=limit))


async def save_planner_event_compat(store: object, trace_id: str, event: PlannerEvent) -> None:
    method = getattr(store, "save_planner_event", None)
    if method is not None:
        await method(trace_id, event)
        return

    # Legacy PlaygroundStateStore interface used save_event(trace_id, event)
    legacy = getattr(store, "save_event", None)
    if legacy is not None:
        try:
            await legacy(trace_id, event)
            return
        except TypeError as exc:
            raise TypeError("StateStore missing save_planner_event") from exc
    raise TypeError("StateStore missing save_planner_event")


async def list_planner_events_compat(store: object, trace_id: str) -> list[PlannerEvent]:
    method = getattr(store, "list_planner_events", None)
    if method is not None:
        return list(await method(trace_id))

    # Legacy PlaygroundStateStore interface used get_events(trace_id)
    legacy = getattr(store, "get_events", None)
    if legacy is not None:
        return list(await legacy(trace_id))

    raise TypeError("StateStore missing list_planner_events/get_events")


async def maybe_save_remote_binding(store: StateStore | None, binding: Any) -> None:
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
