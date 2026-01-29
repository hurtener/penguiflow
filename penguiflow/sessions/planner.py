"""Planner task pipeline for streaming sessions."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from penguiflow.artifacts import ArtifactRef
from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause, ReactPlanner
from penguiflow.steering import SteeringCancelled, SteeringEventType

from .models import (
    ContextPatch,
    NotificationAction,
    NotificationPayload,
    TaskStatus,
    TaskType,
    UpdateType,
)
from .projections import PlannerEventProjector
from .session import TaskResult, TaskRuntime

PlannerFactory = Callable[[], ReactPlanner]
PlannerEventSink = Callable[[PlannerEvent, str | None], None]


def _is_artifact_ref_dict(value: Any) -> bool:
    """Check if a dict looks like a serialized ArtifactRef."""
    if not isinstance(value, dict):
        return False
    # ArtifactRef has required 'id' field and typically 'mime_type'
    return "id" in value and isinstance(value.get("id"), str)


def _serialize_artifact_value(value: Any) -> dict[str, Any] | None:
    """Serialize an artifact value to a dict, preserving ArtifactRef semantics.

    Returns None for non-artifact values that should be skipped.
    """
    if isinstance(value, ArtifactRef):
        return value.model_dump(mode="json")
    if isinstance(value, BaseModel):
        # Other Pydantic models - serialize them
        return value.model_dump(mode="json")
    if _is_artifact_ref_dict(value):
        # Already serialized ArtifactRef
        return value
    if isinstance(value, dict):
        # Generic dict - include it as-is (may be tool-specific artifact data)
        return value
    # Skip non-dict, non-model values (strings, ints, etc.)
    return None


def _normalize_artifacts_for_patch(
    artifacts: dict[str, Any] | list[Any] | None,
) -> list[dict[str, Any]]:
    """Convert trajectory artifacts dict to ContextPatch list format.

    The ReactPlanner stores artifacts as a dict: {node_name: {field_name: value}}.
    ContextPatch.artifacts expects a list[dict[str, Any]].

    This function normalizes the structure while preserving ArtifactRef objects
    (either as Pydantic objects or already-serialized dicts). Raw binary data
    should never appear here - only references to ArtifactStore.

    Args:
        artifacts: Either a dict from trajectory.artifacts or an already-normalized list.

    Returns:
        List of artifact dicts suitable for ContextPatch.artifacts.
    """
    if not artifacts:
        return []

    # Already a list - validate and return
    if isinstance(artifacts, list):
        return [item for item in artifacts if isinstance(item, dict)]

    # Not a dict - can't normalize
    if not isinstance(artifacts, dict):
        return []

    result: list[dict[str, Any]] = []
    for node_name, fields in artifacts.items():
        if not isinstance(fields, dict):
            # Single artifact value for node (uncommon but possible)
            serialized = _serialize_artifact_value(fields)
            if serialized is not None:
                result.append({"node": node_name, "artifact": serialized})
            continue

        # Multiple fields for this node
        for field_name, value in fields.items():
            serialized = _serialize_artifact_value(value)
            if serialized is not None:
                result.append({
                    "node": node_name,
                    "field": field_name,
                    "artifact": serialized,
                })

    return result


def _extract_answer(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        for key in ("raw_answer", "answer", "text", "content", "message"):
            if key in payload and payload[key] is not None:
                return str(payload[key])
    for attr in ("raw_answer", "answer", "text", "content", "message"):
        if hasattr(payload, attr):
            value = getattr(payload, attr)
            if value is not None:
                return str(value)
    return None


def _build_context_patch(
    *,
    task_id: str,
    payload: Any,
    metadata: Mapping[str, Any] | None,
    context_version: int | None,
    context_hash: str | None,
    spawned_from_event_id: str | None,
) -> ContextPatch:
    digest: list[str] = []
    answer = _extract_answer(payload)
    if answer:
        digest = [answer[:5000]]
    artifacts = metadata.get("artifacts") if isinstance(metadata, Mapping) else None
    sources = metadata.get("sources") if isinstance(metadata, Mapping) else None
    return ContextPatch(
        task_id=task_id,
        spawned_from_event_id=spawned_from_event_id,
        source_context_version=context_version,
        source_context_hash=context_hash,
        digest=digest,
        artifacts=_normalize_artifacts_for_patch(artifacts),
        sources=list(sources or []),
    )


class PlannerTaskPipeline:
    """Run a ReactPlanner inside a StreamingSession task."""

    def __init__(
        self,
        *,
        planner_factory: PlannerFactory,
        event_sink: PlannerEventSink | None = None,
    ) -> None:
        self._planner_factory = planner_factory
        self._event_sink = event_sink

    async def __call__(self, runtime: TaskRuntime) -> TaskResult:
        planner = self._planner_factory()
        trace_id = runtime.context_snapshot.trace_id or secrets.token_hex(8)
        runtime.state.trace_id = trace_id
        projector = PlannerEventProjector(
            session_id=runtime.state.session_id,
            task_id=runtime.state.task_id,
            trace_id=trace_id,
        )

        def _event_callback(event: PlannerEvent) -> None:
            updates = projector.project(event)
            for update in updates:
                runtime.session._publish(update)
            if self._event_sink is not None:
                self._event_sink(event, trace_id)

        planner._event_callback = _event_callback

        llm_context = dict(runtime.context_snapshot.llm_context or {})
        tool_context = dict(runtime.context_snapshot.tool_context or {})
        if runtime.state.task_type == TaskType.BACKGROUND:
            tool_context["session_id"] = f"{runtime.state.session_id}:{runtime.state.task_id}"
            tool_context["parent_session_id"] = runtime.state.session_id
            tool_context["is_subagent"] = True
        else:
            tool_context["session_id"] = runtime.state.session_id
            tool_context["is_subagent"] = False
        tool_context["trace_id"] = trace_id
        tool_context["task_id"] = runtime.state.task_id

        query = runtime.context_snapshot.query or runtime.context_snapshot.spawn_reason or ""
        result: PlannerFinish | PlannerPause = await planner.run(
            query=query,
            llm_context=llm_context,
            tool_context=tool_context,
            steering=runtime.steering,
        )

        while isinstance(result, PlannerPause):
            await runtime.session.registry.update_status(runtime.state.task_id, TaskStatus.PAUSED)
            runtime.emit_update(
                UpdateType.STATUS_CHANGE,
                {"status": "PAUSED", "reason": "checkpoint"},
            )
            runtime.emit_update(
                UpdateType.CHECKPOINT,
                {
                    "kind": "approval_required",
                    "resume_token": result.resume_token,
                    "prompt": "Approval required to continue.",
                    "options": ["approve", "reject"],
                },
            )
            # Wait for approval or cancellation
            while True:
                if runtime.steering.cancelled:
                    raise SteeringCancelled(runtime.steering.cancel_reason)
                event = await runtime.steering.next()
                if event.event_type == SteeringEventType.CANCEL:
                    raise SteeringCancelled(str(event.payload.get("reason") or "cancelled"))
                if event.event_type in {SteeringEventType.APPROVE, SteeringEventType.REJECT}:
                    token = event.payload.get("resume_token")
                    if token == result.resume_token:
                        if event.event_type == SteeringEventType.REJECT:
                            raise SteeringCancelled("pause_rejected")
                        user_input = event.payload.get("decision") or event.payload.get("user_input")
                        await runtime.session.registry.update_status(runtime.state.task_id, TaskStatus.RUNNING)
                        runtime.emit_update(
                            UpdateType.STATUS_CHANGE,
                            {"status": "RUNNING", "reason": "resume"},
                        )
                        result = await planner.resume(
                            result.resume_token,
                            user_input=user_input,
                            tool_context=tool_context,
                            steering=runtime.steering,
                        )
                        break

        metadata = result.metadata if isinstance(result, PlannerFinish) else {}
        patch = _build_context_patch(
            task_id=runtime.state.task_id,
            payload=result.payload,
            metadata=metadata,
            context_version=runtime.context_snapshot.context_version,
            context_hash=runtime.context_snapshot.context_hash,
            spawned_from_event_id=runtime.context_snapshot.spawned_from_event_id,
        )
        digest = patch.digest
        notification = None
        if runtime.state.task_type == TaskType.BACKGROUND:
            notification = NotificationPayload(
                severity="info",
                title="Background task ready",
                body="A background task completed and can be applied to the conversation.",
                actions=[
                    NotificationAction(
                        id="apply_to_chat",
                        label="Apply to conversation",
                        payload={"task_id": runtime.state.task_id},
                    )
                ],
            )
        return TaskResult(
            payload=result.payload,
            context_patch=patch,
            digest=digest,
            artifacts=_normalize_artifacts_for_patch(
                metadata.get("artifacts") if isinstance(metadata, Mapping) else None
            ),
            sources=list(metadata.get("sources", [])) if isinstance(metadata, Mapping) else [],
            notification=notification,
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


__all__ = ["PlannerFactory", "PlannerTaskPipeline"]
