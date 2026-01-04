"""Projection helpers to map planner events to session updates."""

from __future__ import annotations

from typing import Any

from penguiflow.planner import PlannerEvent

from .models import StateUpdate, UpdateType


class PlannerEventProjector:
    """Convert PlannerEvent streams into task-addressable StateUpdate records."""

    def __init__(
        self,
        *,
        session_id: str,
        task_id: str,
        trace_id: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._trace_id = trace_id

    def _update(
        self,
        update_type: UpdateType,
        content: Any,
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
        event_type = event.event_type
        extra = dict(event.extra or {})
        step_index = event.trajectory_step

        if event_type == "step_start":
            return [
                self._update(
                    UpdateType.PROGRESS,
                    {
                        "label": "step_start",
                        "current": step_index + 1,
                        "details": {"action_seq": extra.get("action_seq")},
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "step_complete":
            return [
                self._update(
                    UpdateType.PROGRESS,
                    {
                        "label": "step_complete",
                        "current": step_index + 1,
                        "details": {"latency_ms": event.latency_ms},
                    },
                    step_index=step_index,
                )
            ]

        if event_type in {"stream_chunk", "llm_stream_chunk"}:
            channel = extra.get("channel") or "thinking"
            phase = extra.get("phase") or None
            update_type = UpdateType.RESULT if channel in {"answer", "revision"} else UpdateType.THINKING
            return [
                self._update(
                    update_type,
                    {
                        "text": extra.get("text", ""),
                        "done": bool(extra.get("done", False)),
                        "channel": channel,
                        "phase": phase,
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "artifact_chunk":
            return [
                self._update(
                    UpdateType.RESULT,
                    {
                        "artifact_type": extra.get("artifact_type"),
                        "stream_id": extra.get("stream_id"),
                        "seq": extra.get("seq"),
                        "chunk": extra.get("chunk"),
                        "done": bool(extra.get("done", False)),
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "artifact_stored":
            return [
                self._update(
                    UpdateType.RESULT,
                    {
                        "artifact_id": extra.get("artifact_id"),
                        "mime_type": extra.get("mime_type"),
                        "filename": extra.get("artifact_filename") or extra.get("filename"),
                        "size_bytes": extra.get("size_bytes"),
                        "source": extra.get("source"),
                    },
                    step_index=step_index,
                )
            ]

        if event_type in {"tool_call_start", "tool_call_end"}:
            phase = "start" if event_type == "tool_call_start" else "end"
            return [
                self._update(
                    UpdateType.TOOL_CALL,
                    {
                        "phase": phase,
                        "tool_name": extra.get("tool_name"),
                        "tool_call_id": extra.get("tool_call_id"),
                        "args_json": extra.get("args_json"),
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "tool_call_result":
            return [
                self._update(
                    UpdateType.TOOL_CALL,
                    {
                        "phase": "result",
                        "tool_name": extra.get("tool_name"),
                        "tool_call_id": extra.get("tool_call_id"),
                        "result_json": extra.get("result_json"),
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "finish":
            return [
                self._update(
                    UpdateType.RESULT,
                    {
                        "reason": extra.get("reason"),
                        "thought": event.thought,
                        "cost": extra.get("cost"),
                        "answer_action_seq": extra.get("answer_action_seq"),
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "steering_received":
            return [
                self._update(
                    UpdateType.STATUS_CHANGE,
                    {
                        "status": "STEERING_RECEIVED",
                        "event_id": extra.get("event_id"),
                        "event_type": extra.get("event_type"),
                    },
                    step_index=step_index,
                )
            ]

        if event_type == "error":
            return [
                self._update(
                    UpdateType.ERROR,
                    {"error": event.error or extra.get("error")},
                    step_index=step_index,
                )
            ]

        return []


__all__ = ["PlannerEventProjector"]
