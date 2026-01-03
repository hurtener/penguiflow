"""Pause/resume helpers for the React planner."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from .constraints import _ConstraintTracker
from .context import PlannerPauseReason
from .models import PlannerPause
from .pause import _PauseRecord, _PlannerPauseSignal
from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")


async def pause(planner: Any, reason: PlannerPauseReason, payload: Mapping[str, Any] | None = None) -> PlannerPause:
    if planner._active_trajectory is None:
        raise RuntimeError("pause() requires an active planner run")
    try:
        await _pause_from_context(planner, reason, dict(payload or {}), planner._active_trajectory)
    except _PlannerPauseSignal as signal:
        return signal.pause
    raise RuntimeError("pause request did not trigger")


async def _pause_from_context(
    planner: Any,
    reason: PlannerPauseReason,
    payload: dict[str, Any],
    trajectory: Trajectory,
) -> PlannerPause:
    if not planner._pause_enabled:
        raise RuntimeError("Pause/resume is disabled for this planner")
    pause_payload = PlannerPause(
        reason=reason,
        payload=dict(payload),
        resume_token=uuid4().hex,
    )
    await _record_pause(planner, pause_payload, trajectory, planner._active_tracker)
    raise _PlannerPauseSignal(pause_payload)


async def _record_pause(
    planner: Any,
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


async def _store_pause_record(planner: Any, token: str, record: _PauseRecord) -> None:
    planner._pause_records[token] = record
    if planner._state_store is None:
        return
    saver = getattr(planner._state_store, "save_planner_state", None)
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
        # Log error but don't fail the pause operation
        # In-memory fallback already succeeded
        logger.error(
            "state_store_save_failed",
            extra={
                "token": token[:8] + "...",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )


async def _load_pause_record(planner: Any, token: str) -> _PauseRecord:
    record = planner._pause_records.pop(token, None)
    if record is not None:
        logger.debug("pause_record_loaded", extra={"source": "memory"})
        return record

    if planner._state_store is not None:
        loader = getattr(planner._state_store, "load_planner_state", None)
        if loader is not None:
            try:
                result = loader(token)
                if inspect.isawaitable(result):
                    result = await result
                if result is None:
                    raise KeyError(token)
                trajectory = Trajectory.from_serialised(result["trajectory"])
                payload = dict(result.get("payload", {}))
                reason = result.get("reason", "await_input")
                constraints = result.get("constraints")
                tool_context_payload = result.get("tool_context")
                tool_context = dict(tool_context_payload) if isinstance(tool_context_payload, Mapping) else None
                logger.debug("pause_record_loaded", extra={"source": "state_store"})
                return _PauseRecord(
                    trajectory=trajectory,
                    reason=reason,
                    payload=payload,
                    constraints=constraints,
                    tool_context=tool_context,
                )
            except KeyError:
                raise
            except Exception as exc:
                # Log error and re-raise as KeyError with context
                logger.error(
                    "state_store_load_failed",
                    extra={
                        "token": token[:8] + "...",
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                    },
                )
                raise KeyError(f"Failed to load pause record: {exc}") from exc

    raise KeyError(token)


def _serialise_pause_record(record: _PauseRecord) -> dict[str, Any]:
    tool_context: dict[str, Any] | None = None
    if record.tool_context is not None:
        try:
            tool_context = json.loads(json.dumps(record.tool_context, ensure_ascii=False))
        except (TypeError, ValueError):
            tool_context = None
    return {
        "trajectory": record.trajectory.serialise(),
        "reason": record.reason,
        "payload": dict(record.payload),
        "constraints": dict(record.constraints) if record.constraints is not None else None,
        "tool_context": tool_context,
    }
