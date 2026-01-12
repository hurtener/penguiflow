"""Shared tool-call execution helper (emits tool_call_* events).

This module is used by both the main runtime loop (single tool calls) and
parallel plan execution, so frontends receive consistent live tool telemetry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ..steering import SteeringCancelled, SteeringInbox
from . import prompts
from .llm import _redact_artifacts
from .models import PlannerEvent, PlannerPause
from .pause import _PlannerPauseSignal
from .planner_context import _PlannerContext
from .react_utils import _safe_json_dumps
from .trajectory import Trajectory

_TASK_SERVICE_KEY = "task_service"


def _dedupe_key(value: Any) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        canonical = str(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class ToolCallOutcome:
    """Result of executing a tool call with validation and telemetry."""

    observation: dict[str, Any] | None = None
    llm_observation: dict[str, Any] | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None
    streams: Mapping[str, list[dict[str, Any]]] | None = None
    background_spawned: bool = False


async def execute_tool_call(
    planner: Any,
    *,
    trajectory: Trajectory,
    spec: Any,
    parsed_args: BaseModel,
    tool_call_id: str,
    action_seq: int,
    step_index: int,
    artifact_collector: Any | None,
    source_collector: Any | None,
    tool_context_update: Mapping[str, Any] | None = None,
    artifact_metadata_extra: Mapping[str, Any] | None = None,
    steering: SteeringInbox | None = None,
) -> ToolCallOutcome:
    """Execute a tool call and emit tool_call_* events.

    This intentionally does not mutate trajectory.steps; callers decide how the
    outcome is recorded (single-step runtime vs. parallel plan aggregation).
    """

    try:
        args_payload: dict[str, Any] = parsed_args.model_dump(mode="json")
    except Exception:  # pragma: no cover - defensive
        args_payload = parsed_args.model_dump()
    args_json = _safe_json_dumps(args_payload)

    planner._emit_event(
        PlannerEvent(
            event_type="tool_call_start",
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": spec.name,
                "args_json": args_json,
                "action_seq": action_seq,
            },
        )
    )
    planner._emit_event(
        PlannerEvent(
            event_type="tool_call_end",
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": spec.name,
                "action_seq": action_seq,
            },
        )
    )

    ctx = _PlannerContext(planner, trajectory)
    if tool_context_update and isinstance(ctx.tool_context, MutableMapping):
        ctx.tool_context.update(dict(tool_context_update))

    # Prevent "render_component" loops where the model re-renders the exact same UI payload in
    # the next step because it can't infer success from a minimal {"ok": true} observation.
    if spec.name == "render_component" and isinstance(trajectory.metadata, MutableMapping):
        dedupe = _dedupe_key(args_payload)
        last_key = trajectory.metadata.get("_render_component_last_dedupe_key")
        last_step = trajectory.metadata.get("_render_component_last_step_index")
        if (
            isinstance(last_key, str)
            and last_key == dedupe
            and isinstance(last_step, int)
            and last_step == step_index - 1
        ):
            component = args_payload.get("component")
            last_ref = trajectory.metadata.get("_render_component_last_artifact_ref")
            result_payload = {
                "ok": True,
                "component": component if isinstance(component, str) else None,
                "artifact_ref": last_ref if isinstance(last_ref, str) else None,
                "dedupe_key": dedupe,
                "summary": "Duplicate render skipped",
                "skipped": "duplicate_render",
            }
            result_json = _safe_json_dumps(result_payload)
            planner._emit_event(
                PlannerEvent(
                    event_type="tool_call_result",
                    ts=planner._time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "tool_call_id": tool_call_id,
                        "tool_name": spec.name,
                        "result_json": result_json,
                        "action_seq": action_seq,
                    },
                )
            )
            planner._record_hint_progress(spec.name, trajectory)
            return ToolCallOutcome(
                observation=result_payload,
                llm_observation=result_payload,
                streams=ctx._collect_chunks() or None,
            )

    try:
        extra = spec.extra if isinstance(spec.extra, Mapping) else {}
        background_cfg = extra.get("background") if isinstance(extra, Mapping) else None
        background_allowed = bool(
            getattr(
                getattr(planner, "_background_tasks", None),
                "allow_tool_background",
                False,
            )
        )
        if background_allowed and isinstance(background_cfg, Mapping) and background_cfg.get("enabled") is True:
            service = ctx.tool_context.get(_TASK_SERVICE_KEY)
            if service is not None:
                session_id = ctx.tool_context.get("session_id")
                parent_task_id = ctx.tool_context.get("task_id")
                if isinstance(session_id, str):
                    from penguiflow.sessions.models import MergeStrategy

                    mode = background_cfg.get("mode") if isinstance(background_cfg, Mapping) else None
                    mode_value = str(mode).lower().strip() if mode is not None else "job"
                    merge_raw = background_cfg.get("default_merge_strategy")
                    merge_value = (
                        str(merge_raw).lower().strip()
                        if merge_raw is not None
                        else MergeStrategy.HUMAN_GATED.value
                    )
                    merge_strategy = {
                        "append": MergeStrategy.APPEND,
                        "replace": MergeStrategy.REPLACE,
                        "human_gated": MergeStrategy.HUMAN_GATED,
                        "human-gated": MergeStrategy.HUMAN_GATED,
                        "human": MergeStrategy.HUMAN_GATED,
                    }.get(merge_value, MergeStrategy.HUMAN_GATED)
                    notify_on_complete = background_cfg.get("notify_on_complete", True) is not False

                    if mode_value == "subagent":
                        tool_query = (
                            f"Run tool {spec.name} with args {args_json}. "
                            "Return the tool output and a brief digest."
                        )
                        spawned = await service.spawn(
                            session_id=session_id,
                            query=tool_query,
                            parent_task_id=parent_task_id if isinstance(parent_task_id, str) else None,
                            priority=0,
                            merge_strategy=merge_strategy,
                            propagate_on_cancel="cascade",
                            notify_on_complete=notify_on_complete,
                            context_depth="full",
                        )
                    else:
                        spawned = await service.spawn_tool_job(
                            session_id=session_id,
                            tool_name=spec.name,
                            tool_args=args_payload,
                            parent_task_id=parent_task_id if isinstance(parent_task_id, str) else None,
                            priority=0,
                            merge_strategy=merge_strategy,
                            propagate_on_cancel="cascade",
                            notify_on_complete=notify_on_complete,
                        )
                    from .models import BackgroundTaskHandle

                    status_obj = getattr(spawned, "status", None)
                    status_value = getattr(status_obj, "value", None)
                    status = status_value if status_value is not None else status_obj
                    handle = BackgroundTaskHandle(
                        task_id=str(getattr(spawned, "task_id", "")),
                        status=str(status or "PENDING"),
                        message=f"spawned:{mode_value}",
                    )
                    observation_json = handle.model_dump(mode="json")
                    result_json = _safe_json_dumps(observation_json)
                    planner._emit_event(
                        PlannerEvent(
                            event_type="tool_call_result",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "tool_call_id": tool_call_id,
                                "tool_name": spec.name,
                                "result_json": result_json,
                                "action_seq": action_seq,
                            },
                        )
                    )
                    return ToolCallOutcome(
                        observation=observation_json,
                        llm_observation=observation_json,
                        streams=ctx._collect_chunks() or None,
                        background_spawned=True,
                    )

        if steering is not None:
            tool_task = asyncio.create_task(spec.node.func(parsed_args, ctx))
            cancel_task = asyncio.create_task(steering.cancel_event.wait())
            done, _pending = await asyncio.wait(
                {tool_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and steering.cancelled:
                tool_task.cancel()
                await asyncio.gather(tool_task, return_exceptions=True)
                raise SteeringCancelled(steering.cancel_reason)
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            result = await tool_task
        else:
            result = await spec.node.func(parsed_args, ctx)
    except _PlannerPauseSignal as signal:
        result_json = _safe_json_dumps({"pause": signal.pause.reason, "payload": dict(signal.pause.payload)})
        planner._emit_event(
            PlannerEvent(
                event_type="tool_call_result",
                ts=planner._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool_call_id": tool_call_id,
                    "tool_name": spec.name,
                    "result_json": result_json,
                    "action_seq": action_seq,
                },
            )
        )
        return ToolCallOutcome(
            pause=signal.pause,
            streams=ctx._collect_chunks() or None,
        )
    except Exception as exc:
        if spec.name == "render_component":
            try:
                count = int(getattr(planner, "_render_component_failure_history_count", 0))
            except Exception:
                count = 0
            try:
                planner._render_component_failure_history_count = count + 1
            except Exception:
                pass
        failure_payload = planner._build_failure_payload(spec, parsed_args, exc)
        error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
        planner._emit_event(
            PlannerEvent(
                event_type="tool_call_result",
                ts=planner._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool_call_id": tool_call_id,
                    "tool_name": spec.name,
                    "result_json": _safe_json_dumps({"error": error, "failure": failure_payload}),
                    "action_seq": action_seq,
                },
            )
        )
        return ToolCallOutcome(
            error=error,
            failure=failure_payload,
            streams=ctx._collect_chunks() or None,
        )

    try:
        observation_model = spec.out_model.model_validate(result)
    except ValidationError as exc:
        error = prompts.render_output_validation_error(
            spec.name,
            json.dumps(exc.errors(), ensure_ascii=False),
        )
        planner._emit_event(
            PlannerEvent(
                event_type="tool_call_result",
                ts=planner._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool_call_id": tool_call_id,
                    "tool_name": spec.name,
                    "result_json": _safe_json_dumps({"error": error}),
                    "action_seq": action_seq,
                },
            )
        )
        return ToolCallOutcome(
            error=error,
            streams=ctx._collect_chunks() or None,
        )

    observation_json = observation_model.model_dump(mode="json")

    if spec.name == "render_component" and isinstance(trajectory.metadata, MutableMapping):
        key = observation_json.get("dedupe_key")
        if not isinstance(key, str):
            key = _dedupe_key(args_payload)
        ref = observation_json.get("artifact_ref")
        if not isinstance(ref, str):
            ref = None
        trajectory.metadata["_render_component_last_dedupe_key"] = key
        trajectory.metadata["_render_component_last_step_index"] = step_index
        trajectory.metadata["_render_component_last_artifact_ref"] = ref

    registry = getattr(planner, "_artifact_registry", None)
    if registry is not None:
        registry.register_tool_artifacts(
            spec.name,
            spec.out_model,
            observation_json,
            step_index=step_index,
            metadata_extra=dict(artifact_metadata_extra) if artifact_metadata_extra else None,
        )
        if isinstance(trajectory.metadata, MutableMapping):
            registry.write_snapshot(trajectory.metadata)

    observation_json, was_clamped = await planner._clamp_observation(
        observation_json,
        spec.name,
        step_index,
    )

    if artifact_collector is not None:
        artifact_collector.collect(spec.name, spec.out_model, observation_json)
    if source_collector is not None:
        source_collector.collect(spec.out_model, observation_json)

    llm_obs = observation_json if was_clamped else _redact_artifacts(spec.out_model, observation_json)
    result_json = _safe_json_dumps(llm_obs)
    planner._emit_event(
        PlannerEvent(
            event_type="tool_call_result",
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": spec.name,
                "result_json": result_json,
                "action_seq": action_seq,
            },
        )
    )
    planner._record_hint_progress(spec.name, trajectory)

    return ToolCallOutcome(
        observation=observation_json,
        llm_observation=llm_obs,
        streams=ctx._collect_chunks() or None,
    )
