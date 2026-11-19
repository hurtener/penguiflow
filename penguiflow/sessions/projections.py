"""Projection helpers to map planner events to session updates."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .models import StateUpdate, UpdateType

if TYPE_CHECKING:
    from penguiflow.planner import PlannerEvent


class PlannerEventProjector:
    """Convert PlannerEvent streams into task-addressable StateUpdates."""

    def __init__(
        self,
        *,
        session_id: str,
        task_id: str,
        trace_id: str | None = None,
    ) -> None:
        """Address the projected updates to this session, task and trace."""
        self._session_id = session_id
        self._task_id = task_id
        self._trace_id = trace_id

    def _update(
        self,
        update_type: UpdateType,
        content: object,
        *,
        step_index: int | None = None,
    ) -> StateUpdate:
        return StateUpdate(
            session_id=self._session_id,
            task_id=self._task_id,
            trace_id=self._trace_id,
            update_type=update_type,
            content=content,
            step_index=step_index,
        )

    def project(self, event: PlannerEvent) -> list[StateUpdate]:
        """Map one planner event to the session updates it produces."""
        build = _PROJECTIONS.get(event.event_type)
        if build is None:
            return []
        update_type, content = build(event, dict(event.extra or {}))
        return [
            self._update(
                update_type, content, step_index=event.trajectory_step
            )
        ]


_Projection = tuple[UpdateType, object]
_Builder = Callable[["PlannerEvent", dict[str, Any]], _Projection]


def _step_start(event: PlannerEvent, extra: dict[str, Any]) -> _Projection:
    return UpdateType.PROGRESS, {
        "label": "step_start",
        "current": event.trajectory_step + 1,
        "details": {"action_seq": extra.get("action_seq")},
    }


def _step_complete(event: PlannerEvent) -> _Projection:
    return UpdateType.PROGRESS, {
        "label": "step_complete",
        "current": event.trajectory_step + 1,
        "details": {"latency_ms": event.latency_ms},
    }


def _stream_chunk(extra: dict[str, Any]) -> _Projection:
    channel = extra.get("channel") or "thinking"
    update_type = (
        UpdateType.RESULT
        if channel in {"answer", "revision"}
        else UpdateType.THINKING
    )
    return update_type, {
        "text": extra.get("text", ""),
        "done": bool(extra.get("done", False)),
        "channel": channel,
        "phase": extra.get("phase") or None,
    }


def _artifact_chunk(extra: dict[str, Any]) -> _Projection:
    return UpdateType.RESULT, {
        "artifact_type": extra.get("artifact_type"),
        "stream_id": extra.get("stream_id"),
        "seq": extra.get("seq"),
        "chunk": extra.get("chunk"),
        "done": bool(extra.get("done", False)),
    }


def _artifact_stored(extra: dict[str, Any]) -> _Projection:
    return UpdateType.RESULT, {
        "artifact_id": extra.get("artifact_id"),
        "mime_type": extra.get("mime_type"),
        "filename": extra.get("artifact_filename") or extra.get("filename"),
        "size_bytes": extra.get("size_bytes"),
        "source": extra.get("source"),
    }


def _tool_call(phase: str, extra: dict[str, Any]) -> _Projection:
    content: dict[str, Any] = {
        "phase": phase,
        "tool_name": extra.get("tool_name"),
        "tool_call_id": extra.get("tool_call_id"),
    }
    if phase == "result":
        content["result_json"] = extra.get("result_json")
    else:
        content["args_json"] = extra.get("args_json")
    return UpdateType.TOOL_CALL, content


def _finish(event: PlannerEvent, extra: dict[str, Any]) -> _Projection:
    return UpdateType.RESULT, {
        "reason": extra.get("reason"),
        "thought": event.thought,
        "cost": extra.get("cost"),
        "answer_action_seq": extra.get("answer_action_seq"),
    }


def _steering_received(extra: dict[str, Any]) -> _Projection:
    return UpdateType.STATUS_CHANGE, {
        "status": "STEERING_RECEIVED",
        "event_id": extra.get("event_id"),
        "event_type": extra.get("event_type"),
    }


def _kv_change(extra: dict[str, Any]) -> _Projection:
    # Durable session KV facade emits these as checkpoint-like updates.
    return UpdateType.CHECKPOINT, dict(extra)


def _error(event: PlannerEvent, extra: dict[str, Any]) -> _Projection:
    return UpdateType.ERROR, {"error": event.error or extra.get("error")}


def _extra_only(build: Callable[[dict[str, Any]], _Projection]) -> _Builder:
    """Adapt a builder that only reads the event's ``extra`` payload."""
    return lambda _event, extra: build(extra)


def _event_only(build: Callable[[PlannerEvent], _Projection]) -> _Builder:
    """Adapt a builder that only reads the event itself."""
    return lambda event, _extra: build(event)


_PROJECTIONS: dict[str, _Builder] = {
    "step_start": _step_start,
    "step_complete": _event_only(_step_complete),
    "stream_chunk": _extra_only(_stream_chunk),
    "llm_stream_chunk": _extra_only(_stream_chunk),
    "artifact_chunk": _extra_only(_artifact_chunk),
    "artifact_stored": _extra_only(_artifact_stored),
    "tool_call_start": _extra_only(lambda extra: _tool_call("start", extra)),
    "tool_call_end": _extra_only(lambda extra: _tool_call("end", extra)),
    "tool_call_result": _extra_only(lambda extra: _tool_call("result", extra)),
    "finish": _finish,
    "steering_received": _extra_only(_steering_received),
    "kv_init": _extra_only(_kv_change),
    "kv_set": _extra_only(_kv_change),
    "kv_patch": _extra_only(_kv_change),
    "kv_delete": _extra_only(_kv_change),
    "error": _error,
}


__all__ = ["PlannerEventProjector"]
