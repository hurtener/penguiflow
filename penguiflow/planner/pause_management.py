"""Pause/resume helpers for the React planner."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .models import PlannerPause
from .pause import _PauseRecord, _PlannerPauseError
from .trajectory import Trajectory

if TYPE_CHECKING:
    from .constraints import _ConstraintTracker
    from .context import PlannerPauseReason
    from .react import ReactPlanner

logger = logging.getLogger("penguiflow.planner")


async def pause(
    planner: ReactPlanner,
    reason: PlannerPauseReason,
    payload: Mapping[str, Any] | None = None,
) -> PlannerPause:
    """Pause the active run; return the pause for the caller to resume."""
    if planner.active_trajectory is None:
        msg = "pause() requires an active planner run"
        raise RuntimeError(msg)
    try:
        await _pause_from_context(
            planner, reason, dict(payload or {}), planner.active_trajectory
        )
    except _PlannerPauseError as signal:
        return signal.pause
    msg = "pause request did not trigger"
    raise RuntimeError(msg)


async def _pause_from_context(
    planner: ReactPlanner,
    reason: PlannerPauseReason,
    payload: dict[str, Any],
    trajectory: Trajectory,
) -> PlannerPause:
    if not planner.pause_enabled:
        msg = "Pause/resume is disabled for this planner"
        raise RuntimeError(msg)
    pause_payload = PlannerPause(
        reason=reason,
        payload=dict(payload),
        resume_token=uuid4().hex,
    )
    await _record_pause(
        planner, pause_payload, trajectory, planner.active_tracker
    )
    raise _PlannerPauseError(pause_payload)


async def _record_pause(
    planner: ReactPlanner,
    pause_payload: PlannerPause,
    trajectory: Trajectory,
    tracker: _ConstraintTracker | None,
) -> None:
    snapshot = Trajectory.from_serialised(trajectory.serialise())
    snapshot.tool_context = dict(trajectory.tool_context or {})
    record = _PauseRecord(
        trajectory=snapshot,
        reason=pause_payload.reason,
        payload=dict(pause_payload.payload),
        constraints=tracker.snapshot() if tracker is not None else None,
        tool_context=dict(snapshot.tool_context or {}),
    )
    await _store_pause_record(planner, pause_payload.resume_token, record)


async def _store_pause_record(
    planner: ReactPlanner, token: str, record: _PauseRecord
) -> None:
    planner.pause_records[token] = record
    if planner.state_store is None:
        return
    saver = getattr(planner.state_store, "save_planner_state", None)
    if saver is None:
        logger.debug(
            "state_store_no_save_method",
            extra={"token": token[:8] + "..."},
        )
        return

    try:
        payload = _serialise_pause_record(record)
        result = saver(token, payload)
        if inspect.isawaitable(result):
            await result
        logger.debug("pause_record_saved", extra={"token": token[:8] + "..."})
    except Exception as exc:
        # Log error but don't fail the pause operation In-memory fallback
        # already succeeded
        logger.exception(
            "state_store_save_failed",
            extra={
                "token": token[:8] + "...",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )


def _pause_record_from_state(result: object, token: str) -> _PauseRecord:
    """Rebuild a pause record from what the state store returned.

    Raises:
        KeyError: If the store has no record for ``token``.
    """
    if result is None:
        raise KeyError(token)
    if not isinstance(result, Mapping):
        msg = f"pause record for {token!r} is not a mapping"
        raise TypeError(msg)
    trajectory = Trajectory.from_serialised(result["trajectory"])
    payload = dict(result.get("payload", {}))
    reason = result.get("reason", "await_input")
    constraints = result.get("constraints")
    tool_context_payload = result.get("tool_context")
    tool_context = (
        dict(tool_context_payload)
        if isinstance(tool_context_payload, Mapping)
        else None
    )
    return _PauseRecord(
        trajectory=trajectory,
        reason=reason,
        payload=payload,
        constraints=constraints,
        tool_context=tool_context,
    )


async def _load_stored_pause_record(
    loader: Callable[[str], object], token: str
) -> _PauseRecord:
    """Load a pause record from the state store.

    Raises:
        KeyError: If the record is missing or could not be loaded.
    """
    try:
        result = loader(token)
        if inspect.isawaitable(result):
            result = await result
        record = _pause_record_from_state(result, token)
    except KeyError:
        raise
    except Exception as exc:
        # Log error and re-raise as KeyError with context
        logger.exception(
            "state_store_load_failed",
            extra={
                "token": token[:8] + "...",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )
        msg = f"Failed to load pause record: {exc}"
        raise KeyError(msg) from exc
    logger.debug("pause_record_loaded", extra={"source": "state_store"})
    return record


async def _load_pause_record(
    planner: ReactPlanner, token: str
) -> _PauseRecord:
    record = planner.pause_records.pop(token, None)
    if record is not None:
        logger.debug("pause_record_loaded", extra={"source": "memory"})
        return record

    if planner.state_store is not None:
        loader = getattr(planner.state_store, "load_planner_state", None)
        if loader is not None:
            return await _load_stored_pause_record(loader, token)

    raise KeyError(token)


def _serialise_pause_record(record: _PauseRecord) -> dict[str, Any]:
    tool_context: dict[str, Any] | None = None
    if record.tool_context is not None:
        try:
            tool_context = json.loads(
                json.dumps(record.tool_context, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            tool_context = None
    return {
        "trajectory": record.trajectory.serialise(),
        "reason": record.reason,
        "payload": dict(record.payload),
        "constraints": dict(record.constraints)
        if record.constraints is not None
        else None,
        "tool_context": tool_context,
    }
