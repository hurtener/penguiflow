"""Shared tool-call execution helper (emits tool_call_* events).

This module is used by both the main runtime loop (single tool calls) and
parallel plan execution, so frontends receive consistent live tool telemetry.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ..rich_output.tools import (
    RICH_OUTPUT_COMPONENT_TOOL_NAMES,
    RICH_OUTPUT_RENDER_TOOL_NAMES,
    get_render_tool_component_name,
)
from ..steering import SteeringCancelled, SteeringInbox
from . import prompts
from .llm import _redact_artifacts
from .models import PlannerEvent, PlannerPause
from .pause import _PlannerPauseError
from .planner_context import _PlannerContext
from .react_utils import _safe_json_dumps, _serialize_validation_errors

if TYPE_CHECKING:
    from ..catalog import NodeSpec
    from .artifact_handling import _ArtifactCollector, _SourceCollector
    from .react import ReactPlanner
    from .trajectory import Trajectory

_TASK_SERVICE_KEY = "task_service"
_PROACTIVE_HOPS_KEY = "proactive_hops_remaining"


def _dedupe_key(value: object) -> str:
    try:
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except Exception:
        canonical = str(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _component_tool_dedupe_key(
    tool_name: str, args_payload: Mapping[str, Any]
) -> str:
    return _dedupe_key({"tool": tool_name, "payload": args_payload})


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


def _emit_tool_result(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    tool_call_id: str,
    tool_name: str,
    action_seq: int,
    result_json: str,
) -> None:
    """Emit the ``tool_call_result`` event of one tool call."""
    planner.emit_event(
        PlannerEvent(
            event_type="tool_call_result",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result_json": result_json,
                "action_seq": action_seq,
            },
        )
    )


@dataclass(frozen=True, slots=True)
class _CheckedResult:
    """A tool result as the ``tool_call_result`` guardrail left it."""

    result_json: str
    observation: dict[str, Any]
    llm_observation: dict[str, Any]


async def _guardrail_after_tool(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    spec: NodeSpec,
    ctx: _PlannerContext,
    args_payload: dict[str, Any],
    tool_call_id: str,
    action_seq: int,
    checked: _CheckedResult,
) -> ToolCallOutcome | _CheckedResult:
    """Evaluate the tool_call_result guardrail: stop, pause or redact."""
    if not planner.guardrails_enabled():
        return checked
    decision = await planner.evaluate_guardrail(
        planner.build_guardrail_event(
            event_type="tool_call_result",
            text_content=checked.result_json,
            tool_name=spec.name,
            tool_args=args_payload,
            payload={
                "tool_call_id": tool_call_id,
                "action_seq": action_seq,
            },
            trajectory=trajectory,
        )
    )
    if not decision or not planner.guardrails_enforced():
        return checked
    if decision.action.value == "STOP":
        failure_payload = {
            "guardrail": planner.guardrail_decision_payload(decision)
        }
        error = f"guardrail_stop:{decision.rule_id}"
        _emit_tool_result(
            planner,
            trajectory,
            tool_call_id=tool_call_id,
            tool_name=spec.name,
            action_seq=action_seq,
            result_json=_safe_json_dumps(
                {"error": error, "failure": failure_payload}
            ),
        )
        return ToolCallOutcome(
            error=error,
            failure=failure_payload,
            streams=ctx.collect_chunks() or None,
        )
    if decision.action.value == "PAUSE":
        pause_payload = {
            "guardrail": planner.guardrail_decision_payload(decision)
        }
        pause = await planner.pause_from_context(
            "approval_required",
            pause_payload,
            trajectory,
        )
        return ToolCallOutcome(
            pause=pause,
            streams=ctx.collect_chunks() or None,
        )
    if decision.action.value == "REDACT" and decision.redactions:
        from .guardrails.utils import apply_redactions_to_text

        redacted_json = apply_redactions_to_text(
            checked.result_json, decision.redactions
        )
        try:
            redacted_payload = json.loads(redacted_json)
        except json.JSONDecodeError:
            return _CheckedResult(
                redacted_json, checked.observation, checked.llm_observation
            )
        return _CheckedResult(
            redacted_json, redacted_payload, redacted_payload
        )
    return checked


async def execute_tool_call(
    planner: ReactPlanner,
    *,
    trajectory: Trajectory,
    spec: NodeSpec,
    parsed_args: BaseModel,
    tool_call_id: str,
    action_seq: int,
    step_index: int,
    artifact_collector: _ArtifactCollector | None,
    source_collector: _SourceCollector | None,
    tool_context_update: Mapping[str, Any] | None = None,
    artifact_metadata_extra: Mapping[str, Any] | None = None,
    steering: SteeringInbox | None = None,
) -> ToolCallOutcome:
    """Execute a tool call and emit tool_call_* events.

    This intentionally does not mutate trajectory.steps; callers decide how the
    outcome is recorded (single-step runtime vs. parallel plan aggregation).
    """
    args_json, args_payload = _start_tool_call(
        planner,
        trajectory,
        action_seq=action_seq,
        parsed_args=parsed_args,
        spec=spec,
        tool_call_id=tool_call_id,
    )

    early = await _guardrail_before_tool(
        planner,
        trajectory,
        action_seq=action_seq,
        args_payload=args_payload,
        spec=spec,
        tool_call_id=tool_call_id,
    )
    if early is not None:
        return early

    ctx = _tool_call_context(
        planner,
        trajectory,
        spec=spec,
        tool_call_id=tool_call_id,
        tool_context_update=tool_context_update,
    )

    # Prevent "render_component" loops where the model re-renders the exact
    # same UI payload in
    # the next step because it can't infer success from a minimal {"ok": true}
    # observation.
    early = _skip_duplicate_render(
        planner,
        trajectory,
        action_seq=action_seq,
        args_payload=args_payload,
        ctx=ctx,
        spec=spec,
        step_index=step_index,
        tool_call_id=tool_call_id,
    )
    if early is not None:
        return early

    try:
        early = await _spawn_background_call(
            planner,
            trajectory,
            action_seq=action_seq,
            args_json=args_json,
            args_payload=args_payload,
            ctx=ctx,
            spec=spec,
            tool_call_id=tool_call_id,
        )
        if early is not None:
            return early

        result = await _invoke_tool(
            ctx=ctx, parsed_args=parsed_args, spec=spec, steering=steering
        )
    except _PlannerPauseError as signal:
        return _tool_pause_outcome(
            planner,
            trajectory,
            action_seq=action_seq,
            ctx=ctx,
            signal=signal,
            spec=spec,
            tool_call_id=tool_call_id,
        )
    except Exception as exc:
        return _tool_failure_outcome(
            planner,
            trajectory,
            action_seq=action_seq,
            ctx=ctx,
            exc=exc,
            parsed_args=parsed_args,
            spec=spec,
            tool_call_id=tool_call_id,
        )

    try:
        observation_model = spec.out_model.model_validate(result)
    except ValidationError as exc:
        return _invalid_output_outcome(
            planner,
            trajectory,
            action_seq=action_seq,
            ctx=ctx,
            exc=exc,
            spec=spec,
            tool_call_id=tool_call_id,
        )

    observation_json = observation_model.model_dump(mode="json")

    _record_tool_artifacts(
        planner,
        trajectory,
        args_payload=args_payload,
        artifact_metadata_extra=artifact_metadata_extra,
        observation_json=observation_json,
        spec=spec,
        step_index=step_index,
    )

    observation_json, was_clamped = await planner.clamp_observation(
        observation_json,
        spec.name,
        step_index,
    )

    if artifact_collector is not None:
        artifact_collector.collect(spec.name, spec.out_model, observation_json)
    if source_collector is not None:
        source_collector.collect(spec.out_model, observation_json)

    llm_obs = (
        observation_json
        if was_clamped
        else _redact_artifacts(spec.out_model, observation_json)
    )
    result_json = _safe_json_dumps(llm_obs)
    checked = await _guardrail_after_tool(
        planner,
        trajectory,
        spec=spec,
        ctx=ctx,
        args_payload=args_payload,
        tool_call_id=tool_call_id,
        action_seq=action_seq,
        checked=_CheckedResult(result_json, observation_json, llm_obs),
    )
    if isinstance(checked, ToolCallOutcome):
        return checked
    _emit_tool_result(
        planner,
        trajectory,
        tool_call_id=tool_call_id,
        tool_name=spec.name,
        action_seq=action_seq,
        result_json=checked.result_json,
    )
    planner.record_hint_progress(spec.name, trajectory)

    return ToolCallOutcome(
        observation=checked.observation,
        llm_observation=checked.llm_observation,
        streams=ctx.collect_chunks() or None,
    )


def _start_tool_call(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    parsed_args: BaseModel,
    spec: NodeSpec,
    tool_call_id: str,
) -> tuple[str, dict[str, Any]]:
    """Serialize the arguments and emit the tool_call_start/end events."""
    try:
        args_payload: dict[str, Any] = parsed_args.model_dump(mode="json")
    except Exception:  # pragma: no cover - defensive
        args_payload = parsed_args.model_dump()
    args_json = _safe_json_dumps(args_payload)

    planner.emit_event(
        PlannerEvent(
            event_type="tool_call_start",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": spec.name,
                "args_json": args_json,
                "action_seq": action_seq,
            },
        )
    )
    planner.emit_event(
        PlannerEvent(
            event_type="tool_call_end",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "tool_call_id": tool_call_id,
                "tool_name": spec.name,
                "action_seq": action_seq,
            },
        )
    )
    return args_json, args_payload


def _tool_call_context(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    spec: NodeSpec,
    tool_call_id: str,
    tool_context_update: Mapping[str, Any] | None,
) -> _PlannerContext:
    """Build the tool's context, tagged with the current call."""
    ctx = _PlannerContext(planner, trajectory)
    if tool_context_update and isinstance(ctx.tool_context, MutableMapping):
        ctx.tool_context.update(dict(tool_context_update))
    if isinstance(ctx.tool_context, MutableMapping):
        ctx.tool_context["_current_tool_name"] = spec.name
        ctx.tool_context["_current_tool_call_id"] = tool_call_id
    return ctx


async def _guardrail_before_tool(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    args_payload: dict[str, Any],
    spec: NodeSpec,
    tool_call_id: str,
) -> ToolCallOutcome | None:
    """Evaluate the tool_call_start guardrail; stop or pause if enforced."""
    if planner.guardrails_enabled():
        decision = await planner.evaluate_guardrail(
            planner.build_guardrail_event(
                event_type="tool_call_start",
                tool_name=spec.name,
                tool_args=args_payload,
                payload={
                    "tool_call_id": tool_call_id,
                    "action_seq": action_seq,
                },
                trajectory=trajectory,
            )
        )
        if decision and planner.guardrails_enforced():
            if decision.action.value == "STOP":
                failure_payload = {
                    "guardrail": planner.guardrail_decision_payload(decision)
                }
                result_json = _safe_json_dumps(
                    {"error": decision.reason, "failure": failure_payload}
                )
                _emit_tool_result(
                    planner,
                    trajectory,
                    tool_call_id=tool_call_id,
                    tool_name=spec.name,
                    action_seq=action_seq,
                    result_json=result_json,
                )
                return ToolCallOutcome(
                    error=f"guardrail_stop:{decision.rule_id}",
                    failure=failure_payload,
                    streams=None,
                )
            if decision.action.value == "PAUSE":
                pause_payload = {
                    "guardrail": planner.guardrail_decision_payload(decision)
                }
                pause = await planner.pause_from_context(
                    "approval_required",
                    pause_payload,
                    trajectory,
                )
                return ToolCallOutcome(
                    pause=pause,
                    streams=None,
                )
    return None


def _skip_duplicate_render(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    args_payload: dict[str, Any],
    ctx: _PlannerContext,
    spec: NodeSpec,
    step_index: int,
    tool_call_id: str,
) -> ToolCallOutcome | None:
    """Skip re-rendering the exact component rendered by the last step."""
    if spec.name in RICH_OUTPUT_COMPONENT_TOOL_NAMES and isinstance(
        trajectory.metadata, MutableMapping
    ):
        dedupe = _component_tool_dedupe_key(spec.name, args_payload)
        last_key = trajectory.metadata.get("_render_component_last_dedupe_key")
        last_step = trajectory.metadata.get(
            "_render_component_last_step_index"
        )
        if (
            isinstance(last_key, str)
            and last_key == dedupe
            and isinstance(last_step, int)
            and last_step == step_index - 1
        ):
            component = args_payload.get("component")
            if not isinstance(component, str):
                component = get_render_tool_component_name(spec.name)
            last_ref = trajectory.metadata.get(
                "_render_component_last_artifact_ref"
            )
            skipped_kind = (
                "duplicate_render"
                if spec.name in RICH_OUTPUT_RENDER_TOOL_NAMES
                else "duplicate_build"
            )
            summary = (
                "Duplicate render skipped"
                if spec.name in RICH_OUTPUT_RENDER_TOOL_NAMES
                else "Duplicate build skipped"
            )
            result_payload = {
                "ok": True,
                "component": component if isinstance(component, str) else None,
                "artifact_ref": last_ref
                if isinstance(last_ref, str)
                else None,
                "dedupe_key": dedupe,
                "summary": summary,
                "skipped": skipped_kind,
            }
            result_json = _safe_json_dumps(result_payload)
            _emit_tool_result(
                planner,
                trajectory,
                tool_call_id=tool_call_id,
                tool_name=spec.name,
                action_seq=action_seq,
                result_json=result_json,
            )
            planner.record_hint_progress(spec.name, trajectory)
            return ToolCallOutcome(
                observation=result_payload,
                llm_observation=result_payload,
                streams=ctx.collect_chunks() or None,
            )
    return None


async def _spawn_background_call(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    args_json: str,
    args_payload: dict[str, Any],
    ctx: _PlannerContext,
    spec: NodeSpec,
    tool_call_id: str,
) -> ToolCallOutcome | None:
    """Spawn the call as a background task when the tool allows it."""
    extra = spec.extra if isinstance(spec.extra, Mapping) else {}
    background_cfg = (
        extra.get("background") if isinstance(extra, Mapping) else None
    )
    background_allowed = bool(
        getattr(
            getattr(planner, "background_tasks", None),
            "allow_tool_background",
            False,
        )
    )
    proactive_hops = None
    if isinstance(ctx.tool_context, Mapping):
        value = ctx.tool_context.get(_PROACTIVE_HOPS_KEY)
        if value is not None:
            try:
                proactive_hops = int(value)
            except (TypeError, ValueError):
                proactive_hops = None
    if proactive_hops is not None and proactive_hops <= 0:
        background_allowed = False
    if (
        background_allowed
        and isinstance(background_cfg, Mapping)
        and background_cfg.get("enabled") is True
    ):
        service = ctx.tool_context.get(_TASK_SERVICE_KEY)
        if service is not None:
            session_id = ctx.tool_context.get("session_id")
            parent_task_id = ctx.tool_context.get("task_id")
            if isinstance(session_id, str):
                from penguiflow.sessions.models import MergeStrategy

                mode = (
                    background_cfg.get("mode")
                    if isinstance(background_cfg, Mapping)
                    else None
                )
                mode_value = (
                    str(mode).lower().strip() if mode is not None else "job"
                )
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
                notify_on_complete = (
                    background_cfg.get("notify_on_complete", True) is not False
                )

                context_tool_context = (
                    {_PROACTIVE_HOPS_KEY: proactive_hops}
                    if proactive_hops is not None
                    else None
                )
                if mode_value == "subagent":
                    tool_query = (
                        f"Run tool {spec.name} with args {args_json}. "
                        "Return the tool output and a brief digest."
                    )
                    spawned = await service.spawn(
                        session_id=session_id,
                        query=tool_query,
                        parent_task_id=parent_task_id
                        if isinstance(parent_task_id, str)
                        else None,
                        priority=0,
                        merge_strategy=merge_strategy,
                        propagate_on_cancel="cascade",
                        notify_on_complete=notify_on_complete,
                        context_depth="full",
                        context_tool_context=context_tool_context,
                    )
                else:
                    spawned = await service.spawn_tool_job(
                        session_id=session_id,
                        tool_name=spec.name,
                        tool_args=args_payload,
                        parent_task_id=parent_task_id
                        if isinstance(parent_task_id, str)
                        else None,
                        priority=0,
                        merge_strategy=merge_strategy,
                        propagate_on_cancel="cascade",
                        notify_on_complete=notify_on_complete,
                        context_tool_context=context_tool_context,
                    )
                from .models import BackgroundTaskHandle

                status_obj = getattr(spawned, "status", None)
                status_value = getattr(status_obj, "value", None)
                status = (
                    status_value if status_value is not None else status_obj
                )
                handle = BackgroundTaskHandle(
                    task_id=str(getattr(spawned, "task_id", "")),
                    status=str(status or "PENDING"),
                    message=f"spawned:{mode_value}",
                )
                observation_json = handle.model_dump(mode="json")
                result_json = _safe_json_dumps(observation_json)
                _emit_tool_result(
                    planner,
                    trajectory,
                    tool_call_id=tool_call_id,
                    tool_name=spec.name,
                    action_seq=action_seq,
                    result_json=result_json,
                )
                return ToolCallOutcome(
                    observation=observation_json,
                    llm_observation=observation_json,
                    streams=ctx.collect_chunks() or None,
                    background_spawned=True,
                )
    return None


async def _invoke_tool(
    *,
    ctx: _PlannerContext,
    parsed_args: BaseModel,
    spec: NodeSpec,
    steering: SteeringInbox | None,
) -> object:
    """Await the tool, cancelling it when steering cancels the run."""
    if steering is not None:
        # Node functions are typed as returning any awaitable.
        tool_task: asyncio.Future[object] = asyncio.ensure_future(
            spec.node.func(parsed_args, ctx)
        )
        cancel_task: asyncio.Future[object] = asyncio.ensure_future(
            steering.cancel_event.wait()
        )
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
    return result


def _tool_pause_outcome(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    ctx: _PlannerContext,
    signal: _PlannerPauseError,
    spec: NodeSpec,
    tool_call_id: str,
) -> ToolCallOutcome:
    """Report a tool that paused the run."""
    result_json = _safe_json_dumps(
        {
            "pause": signal.pause.reason,
            "payload": dict(signal.pause.payload),
        }
    )
    _emit_tool_result(
        planner,
        trajectory,
        tool_call_id=tool_call_id,
        tool_name=spec.name,
        action_seq=action_seq,
        result_json=result_json,
    )
    return ToolCallOutcome(
        pause=signal.pause,
        streams=ctx.collect_chunks() or None,
    )


def _tool_failure_outcome(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    ctx: _PlannerContext,
    exc: Exception,
    parsed_args: BaseModel,
    spec: NodeSpec,
    tool_call_id: str,
) -> ToolCallOutcome:
    """Report a tool that raised, with its failure payload."""
    if spec.name in RICH_OUTPUT_COMPONENT_TOOL_NAMES:
        try:
            count = int(
                getattr(planner, "render_component_failure_history_count", 0)
            )
        except Exception:
            count = 0
        with contextlib.suppress(Exception):
            planner.render_component_failure_history_count = count + 1
    failure_payload = planner.build_failure_payload(spec, parsed_args, exc)
    error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
    _emit_tool_result(
        planner,
        trajectory,
        tool_call_id=tool_call_id,
        tool_name=spec.name,
        action_seq=action_seq,
        result_json=_safe_json_dumps(
            {"error": error, "failure": failure_payload}
        ),
    )
    return ToolCallOutcome(
        error=error,
        failure=failure_payload,
        streams=ctx.collect_chunks() or None,
    )


def _invalid_output_outcome(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    action_seq: int,
    ctx: _PlannerContext,
    exc: ValidationError,
    spec: NodeSpec,
    tool_call_id: str,
) -> ToolCallOutcome:
    """Report a tool output that failed its output model's validation."""
    error = prompts.render_output_validation_error(
        spec.name,
        _serialize_validation_errors(exc),
    )
    _emit_tool_result(
        planner,
        trajectory,
        tool_call_id=tool_call_id,
        tool_name=spec.name,
        action_seq=action_seq,
        result_json=_safe_json_dumps({"error": error}),
    )
    return ToolCallOutcome(
        error=error,
        streams=ctx.collect_chunks() or None,
    )


def _record_tool_artifacts(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    args_payload: dict[str, Any],
    artifact_metadata_extra: Mapping[str, Any] | None,
    observation_json: dict[str, Any],
    spec: NodeSpec,
    step_index: int,
) -> None:
    """Register the output's artifacts and the last component render."""
    if spec.name in RICH_OUTPUT_COMPONENT_TOOL_NAMES and isinstance(
        trajectory.metadata, MutableMapping
    ):
        key = observation_json.get("dedupe_key")
        if not isinstance(key, str):
            key = _component_tool_dedupe_key(spec.name, args_payload)
        ref = observation_json.get("artifact_ref")
        if not isinstance(ref, str):
            ref = None
        trajectory.metadata["_render_component_last_dedupe_key"] = key
        trajectory.metadata["_render_component_last_step_index"] = step_index
        trajectory.metadata["_render_component_last_artifact_ref"] = ref

    registry = getattr(planner, "artifact_registry", None)
    if registry is not None:
        registry.register_tool_artifacts(
            spec.name,
            spec.out_model,
            observation_json,
            step_index=step_index,
            metadata_extra=dict(artifact_metadata_extra)
            if artifact_metadata_extra
            else None,
        )
        if isinstance(trajectory.metadata, MutableMapping):
            registry.write_snapshot(trajectory.metadata)
