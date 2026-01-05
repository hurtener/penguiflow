"""Runtime helpers for the React planner loop."""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from collections.abc import Mapping, MutableMapping
from typing import Any

from pydantic import ValidationError

from ..steering import SteeringCancelled, SteeringEventType, SteeringInbox
from . import prompts
from .artifact_handling import _ArtifactCollector, _SourceCollector
from .artifact_registry import ArtifactRegistry
from .constraints import _ConstraintTracker, _CostTracker
from .llm import _redact_artifacts
from .models import PlannerAction, PlannerEvent, PlannerFinish, PlannerPause, ReflectionCritique
from .pause import _PlannerPauseSignal
from .planner_context import _PlannerContext
from .react_utils import _safe_json_dumps
from .streaming import _StreamingArgsExtractor
from .trajectory import Trajectory, TrajectoryStep
from .validation_repair import _autofill_missing_args, _coerce_tool_context, _validate_llm_context

logger = logging.getLogger("penguiflow.planner")

_TASK_SERVICE_KEY = "task_service"


def _apply_steering(planner: Any, trajectory: Trajectory) -> None:
    steering: SteeringInbox | None = getattr(planner, "_steering", None)
    if steering is None:
        return

    if steering.cancelled:
        raise SteeringCancelled(steering.cancel_reason)

    events = steering.drain()
    if not events:
        return

    for event in events:
        planner._emit_event(
            PlannerEvent(
                event_type="steering_received",
                ts=planner._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "source": event.source,
                },
            )
        )
        if event.event_type == SteeringEventType.CANCEL:
            raise SteeringCancelled(str(event.payload.get("reason") or "cancelled"))
        if event.event_type in {SteeringEventType.INJECT_CONTEXT, SteeringEventType.REDIRECT}:
            if event.event_type == SteeringEventType.REDIRECT:
                new_goal = (
                    event.payload.get("instruction")
                    or event.payload.get("goal")
                    or event.payload.get("query")
                )
                if isinstance(new_goal, str) and new_goal.strip():
                    trajectory.query = new_goal.strip()
            trajectory.steering_inputs.append(event.to_injection())


async def run(
    planner: Any,
    query: str,
    *,
    llm_context: Mapping[str, Any] | None = None,
    context_meta: Mapping[str, Any] | None = None,
    tool_context: Mapping[str, Any] | None = None,
    memory_key: Any | None = None,
) -> PlannerFinish | PlannerPause:
    # Handle backward compatibility
    if context_meta is not None:
        warnings.warn(
            "context_meta parameter is deprecated. Use llm_context instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if llm_context is None:
            llm_context = context_meta

    logger.info("planner_run_start", extra={"query": query})
    normalised_tool_context = _coerce_tool_context(tool_context)
    normalised_llm_context = _validate_llm_context(llm_context)
    resolved_key = planner._resolve_memory_key(memory_key, normalised_tool_context)
    normalised_llm_context = await planner._apply_memory_context(normalised_llm_context, resolved_key)
    planner._cost_tracker = _CostTracker()
    trajectory = Trajectory(
        query=query,
        llm_context=normalised_llm_context,
        tool_context=normalised_tool_context,
    )
    planner._artifact_registry = ArtifactRegistry.from_snapshot(trajectory.metadata.get("artifact_registry"))
    planner._artifact_registry.write_snapshot(trajectory.metadata)
    result = await run_loop(planner, trajectory, tracker=None)
    await planner._maybe_record_memory_turn(query, result, trajectory, resolved_key)
    return result


async def resume(
    planner: Any,
    token: str,
    user_input: str | None = None,
    *,
    tool_context: Mapping[str, Any] | None = None,
    memory_key: Any | None = None,
) -> PlannerFinish | PlannerPause:
    logger.info("planner_resume", extra={"token": token[:8] + "..."})
    provided_tool_context = _coerce_tool_context(tool_context) if tool_context is not None else None
    record = await planner._load_pause_record(token)
    trajectory = record.trajectory
    trajectory.llm_context = _validate_llm_context(trajectory.llm_context) or {}
    if provided_tool_context is not None:
        trajectory.tool_context = provided_tool_context
    elif record.tool_context is not None:
        trajectory.tool_context = dict(record.tool_context)
    else:
        trajectory.tool_context = trajectory.tool_context or {}
    if user_input is not None:
        trajectory.resume_user_input = user_input

    planner._artifact_registry = ArtifactRegistry.from_snapshot(trajectory.metadata.get("artifact_registry"))
    planner._artifact_registry.write_snapshot(trajectory.metadata)

    resolved_key = planner._resolve_memory_key(memory_key, trajectory.tool_context or {})
    merged_llm_context = await planner._apply_memory_context(
        dict(trajectory.llm_context or {}),
        resolved_key,
    )
    trajectory.llm_context = merged_llm_context
    tracker: _ConstraintTracker | None = None
    if record.constraints is not None:
        tracker = _ConstraintTracker.from_snapshot(
            record.constraints,
            time_source=planner._time_source,
        )

    # Emit resume event
    planner._emit_event(
        PlannerEvent(
            event_type="resume",
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra={"user_input": user_input} if user_input else {},
        )
    )

    result = await run_loop(planner, trajectory, tracker=tracker)
    await planner._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)
    return result


def _check_deadline(
    planner: Any,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
    last_observation: Any | None,
) -> PlannerFinish | None:
    deadline_message = tracker.check_deadline()
    if deadline_message is None:
        return None

    logger.warning(
        "deadline_exhausted",
        extra={"step": len(trajectory.steps)},
    )
    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()
    return planner._finish(
        trajectory,
        reason="budget_exhausted",
        payload=last_observation,
        thought=deadline_message,
        constraints=tracker,
    )


def _emit_step_start(planner: Any, trajectory: Trajectory) -> tuple[float, int]:
    step_start_ts = planner._time_source()
    planner._action_seq += 1
    current_action_seq = planner._action_seq
    planner._emit_event(
        PlannerEvent(
            event_type="step_start",
            ts=step_start_ts,
            trajectory_step=len(trajectory.steps),
            extra={"action_seq": current_action_seq},
        )
    )
    return step_start_ts, current_action_seq


def _log_action_received(planner: Any, action: PlannerAction, trajectory: Trajectory) -> None:
    action_extra: dict[str, Any] = {
        "step": len(trajectory.steps),
        "thought": action.thought,
        "next_node": action.next_node,
        "has_plan": action.plan is not None,
    }
    # For finish actions, log the args to help debug answer extraction issues
    if action.next_node is None:
        if action.args:
            args_preview = str(action.args)
            if len(args_preview) > 500:
                args_preview = args_preview[:500] + "..."
            action_extra["args_preview"] = args_preview
            if isinstance(action.args, dict):
                action_extra["args_keys"] = list(action.args.keys())
                action_extra["has_raw_answer"] = "raw_answer" in action.args
            else:
                action_extra["args_keys"] = None
                action_extra["has_raw_answer"] = False
        else:
            action_extra["args_preview"] = "None"
            action_extra["args_keys"] = None
            action_extra["has_raw_answer"] = False
    logger.info("planner_action", extra=action_extra)


async def _handle_parallel_plan(
    planner: Any,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
) -> tuple[Any | None, PlannerPause | None]:
    parallel_observation, pause = await planner._execute_parallel_plan(
        action,
        trajectory,
        tracker,
        artifact_collector,
        source_collector,
    )
    if pause is not None:
        return None, pause
    trajectory.summary = None
    last_observation = parallel_observation
    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()
    trajectory.resume_user_input = None
    return last_observation, None


async def _handle_finish_action(
    planner: Any,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    last_observation: Any | None,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
) -> PlannerFinish:
    # Check if raw_answer is missing and attempt finish repair
    has_raw_answer = (
        isinstance(action.args, dict)
        and action.args.get("raw_answer")
        and action.args["raw_answer"] not in {"", None, "<auto>"}
    )

    if not has_raw_answer and not trajectory.metadata.get("finish_repair_attempted"):
        # Model tried to finish without raw_answer - attempt repair
        logger.info(
            "finish_repair_attempt",
            extra={
                "has_args": action.args is not None,
                "args_keys": list(action.args.keys()) if isinstance(action.args, dict) else None,
                "thought": action.thought,
            },
        )

        filled_answer = await planner._attempt_finish_repair(
            trajectory,
            action,
        )

        if filled_answer is not None:
            # Success! Update action.args with the raw_answer
            if action.args is None:
                action.args = {}
            if isinstance(action.args, dict):
                action.args["raw_answer"] = filled_answer
            logger.info(
                "finish_repair_success",
                extra={"answer_len": len(filled_answer)},
            )
        else:
            logger.warning(
                "finish_repair_failed",
                extra={"thought": action.thought},
            )

    candidate_answer = action.args or last_observation
    metadata_reflection: dict[str, Any] | None = None

    if candidate_answer is not None and planner._reflection_config and planner._reflection_config.enabled:
        critique: ReflectionCritique | None = None
        metadata_reflection = {}
        for revision_idx in range(planner._reflection_config.max_revisions + 1):
            critique = await planner._critique_answer(trajectory, candidate_answer)

            planner._emit_event(
                PlannerEvent(
                    event_type="reflection_critique",
                    ts=planner._time_source(),
                    trajectory_step=len(trajectory.steps),
                    thought=action.thought,
                    extra={
                        "score": critique.score,
                        "passed": critique.passed,
                        "revision": revision_idx,
                        "feedback": critique.feedback[:200],
                    },
                )
            )

            if critique.passed or critique.score >= planner._reflection_config.quality_threshold:
                logger.info(
                    "reflection_passed",
                    extra={
                        "score": critique.score,
                        "revisions": revision_idx,
                    },
                )
                break

            if revision_idx >= planner._reflection_config.max_revisions:
                threshold = planner._reflection_config.quality_threshold

                # Check if quality is still below threshold
                if critique.score < threshold:
                    # Quality remains poor - transform into honest clarification
                    logger.warning(
                        "reflection_honest_failure",
                        extra={
                            "score": critique.score,
                            "threshold": threshold,
                            "revisions": revision_idx,
                        },
                    )

                    # Generate clarification instead of returning low-quality answer
                    clarification_text = await planner._generate_clarification(
                        trajectory=trajectory,
                        failed_answer=candidate_answer,
                        critique=critique,
                        revision_attempts=revision_idx,
                    )

                    # Replace candidate answer with clarification
                    # Ensure proper structure for downstream consumers (like FinalAnswer model)
                    if isinstance(candidate_answer, dict):
                        # Update existing dict with clarification
                        candidate_answer["raw_answer"] = clarification_text
                        candidate_answer["text"] = clarification_text

                        # Ensure required fields are present
                        if "route" not in candidate_answer:
                            # Extract route from first step observation if available
                            route = "unknown"
                            if trajectory.steps and trajectory.steps[0].observation:
                                obs = trajectory.steps[0].observation
                                # Handle both dict and Pydantic model observations
                                if isinstance(obs, dict):
                                    route = obs.get("route", "unknown")
                                else:
                                    route = getattr(obs, "route", "unknown")
                            candidate_answer["route"] = route
                        if "artifacts" not in candidate_answer:
                            candidate_answer["artifacts"] = {}
                        if "metadata" not in candidate_answer:
                            candidate_answer["metadata"] = {}

                        # Mark as unsatisfied in metadata
                        candidate_answer["metadata"]["confidence"] = "unsatisfied"
                        candidate_answer["metadata"]["reflection_score"] = critique.score
                        candidate_answer["metadata"]["revision_attempts"] = revision_idx
                    else:
                        # Create structured answer from scratch
                        route = "unknown"
                        if trajectory.steps and trajectory.steps[0].observation:
                            obs = trajectory.steps[0].observation
                            # Handle both dict and Pydantic model observations
                            if isinstance(obs, dict):
                                route = obs.get("route", "unknown")
                            else:
                                route = getattr(obs, "route", "unknown")

                        candidate_answer = {
                            "raw_answer": clarification_text,
                            "text": clarification_text,
                            "route": route,
                            "artifacts": {},
                            "metadata": {
                                "confidence": "unsatisfied",
                                "reflection_score": critique.score,
                                "revision_attempts": revision_idx,
                            },
                        }

                    # Emit telemetry event
                    planner._emit_event(
                        PlannerEvent(
                            event_type="reflection_clarification_generated",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            thought="Generated clarification for unsatisfiable query",
                            extra={
                                "original_score": critique.score,
                                "threshold": threshold,
                                "revisions": revision_idx,
                            },
                        )
                    )
                else:
                    # Quality improved enough, just log warning
                    logger.warning(
                        "reflection_max_revisions",
                        extra={
                            "score": critique.score,
                            "threshold": threshold,
                        },
                    )

                break

            if not tracker.has_budget_for_next_tool():
                snapshot = tracker.snapshot()
                logger.warning(
                    "reflection_budget_exhausted",
                    extra={
                        "score": critique.score,
                        "hops_used": snapshot.get("hops_used"),
                    },
                )
                break

            logger.debug(
                "reflection_requesting_revision",
                extra={
                    "revision": revision_idx + 1,
                    "score": critique.score,
                },
            )

            # Build streaming callback for revision (reuse extractor pattern)
            revision_extractor = _StreamingArgsExtractor()

            def _emit_revision_chunk(
                text: str,
                done: bool,
                *,
                _extractor: _StreamingArgsExtractor = revision_extractor,
                _revision_idx: int = revision_idx,
            ) -> None:
                if planner._event_callback is None:
                    return

                args_chars = _extractor.feed(text)

                if args_chars:
                    args_text = "".join(args_chars)
                    planner._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "text": args_text,
                                "done": False,
                                "phase": "revision",
                                "revision_idx": _revision_idx + 1,
                            },
                        )
                    )

                if done and _extractor.is_finish_action:
                    planner._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "text": "",
                                "done": True,
                                "phase": "revision",
                                "revision_idx": _revision_idx + 1,
                            },
                        )
                    )

            revision_action = await planner._request_revision(
                trajectory,
                critique,
                on_stream_chunk=_emit_revision_chunk if planner._stream_final_response else None,
            )
            candidate_answer = revision_action.args or revision_action.model_dump()
            trajectory.steps.append(
                TrajectoryStep(
                    action=revision_action,
                    observation={"status": "revision_requested"},
                )
            )
            trajectory.summary = None

        if critique is not None:
            metadata_reflection = {
                "score": critique.score,
                "revisions": min(
                    revision_idx,
                    planner._reflection_config.max_revisions,
                ),
                "passed": critique.passed,
            }
            if critique.feedback:
                metadata_reflection["feedback"] = critique.feedback

    metadata_extra: dict[str, Any] | None = None
    if metadata_reflection is not None:
        metadata_extra = {"reflection": metadata_reflection}

    trajectory.artifacts = artifact_collector.snapshot()
    trajectory.sources = source_collector.snapshot()
    final_payload = planner._build_final_payload(
        candidate_answer,
        last_observation,
        trajectory.artifacts,
        trajectory.sources,
    )
    # Note: Real-time streaming of args content happens during LLM call
    # via _StreamingArgsExtractor in step(). No post-hoc chunking needed.

    return planner._finish(
        trajectory,
        reason="answer_complete",
        payload=final_payload.model_dump(mode="json"),
        thought=action.thought,
        constraints=tracker,
        metadata_extra=metadata_extra,
    )


async def run_loop(
    planner: Any,
    trajectory: Trajectory,
    *,
    tracker: _ConstraintTracker | None,
) -> PlannerFinish | PlannerPause:
    last_observation: Any | None = None
    artifact_collector = _ArtifactCollector(trajectory.artifacts)
    source_collector = _SourceCollector(trajectory.sources)
    planner._active_trajectory = trajectory
    if tracker is None:
        tracker = _ConstraintTracker(
            deadline_s=planner._deadline_s,
            hop_budget=planner._hop_budget,
            time_source=planner._time_source,
        )
    planner._active_tracker = tracker
    try:
        while len(trajectory.steps) < planner._max_iters:
            steering: SteeringInbox | None = getattr(planner, "_steering", None)
            if steering is not None:
                await steering.wait_if_paused()
            _apply_steering(planner, trajectory)
            finish = _check_deadline(
                planner,
                trajectory,
                tracker,
                artifact_collector,
                source_collector,
                last_observation,
            )
            if finish is not None:
                return finish

            # Emit step start event and bump action sequence
            step_start_ts, current_action_seq = _emit_step_start(planner, trajectory)

            action = await planner.step(trajectory)

            # Log the action received from LLM
            _log_action_received(planner, action, trajectory)

            # Check constraints BEFORE executing parallel plan or any action
            constraint_error = planner._check_action_constraints(action, trajectory, tracker)
            if constraint_error is not None:
                trajectory.steps.append(TrajectoryStep(action=action, error=constraint_error))
                trajectory.summary = None
                continue

            if action.plan:
                last_observation, pause = await _handle_parallel_plan(
                    planner,
                    action,
                    trajectory,
                    tracker,
                    artifact_collector,
                    source_collector,
                )
                if pause is not None:
                    return pause
                continue

            if action.next_node is None:
                return await _handle_finish_action(
                    planner,
                    action,
                    trajectory,
                    tracker,
                    last_observation,
                    artifact_collector,
                    source_collector,
                )

            spec = planner._spec_by_name.get(action.next_node)
            if spec is None:
                error = prompts.render_invalid_node(
                    action.next_node,
                    list(planner._spec_by_name.keys()),
                )
                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                trajectory.summary = None
                continue

            autofilled_fields: tuple[str, ...] = ()
            try:
                parsed_args = spec.args_model.model_validate(action.args or {})
            except ValidationError as exc:
                autofilled = _autofill_missing_args(spec, action.args)
                if autofilled is not None:
                    autofilled_args, filled_fields = autofilled
                    try:
                        parsed_args = spec.args_model.model_validate(autofilled_args)
                        action.args = autofilled_args
                        autofilled_fields = filled_fields
                        logger.info(
                            "planner_autofill_args",
                            extra={
                                "tool": spec.name,
                                "filled": list(filled_fields),
                            },
                        )
                    except ValidationError as autofill_exc:
                        error = prompts.render_validation_error(
                            spec.name,
                            json.dumps(autofill_exc.errors(), ensure_ascii=False),
                        )
                        trajectory.steps.append(TrajectoryStep(action=action, error=error))
                        trajectory.summary = None
                        continue
                else:
                    error = prompts.render_validation_error(
                        spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                    )
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

            arg_validation_error = planner._apply_arg_validation(
                trajectory,
                spec=spec,
                action=action,
                parsed_args=parsed_args,
                autofilled_fields=autofilled_fields,
            )
            if arg_validation_error is not None:
                autofill_rejection_count = int(trajectory.metadata.get("autofill_rejection_count", 0))
                consecutive_failures = int(trajectory.metadata.get("consecutive_arg_failures", 0))

                # Force finish conditions:
                # 1. Second autofill rejection (gave model one chance with explicit field names)
                # 2. Consecutive failures threshold reached
                force_finish = (
                    (autofilled_fields and autofill_rejection_count >= 2)
                    or consecutive_failures >= planner._max_consecutive_arg_failures
                )

                if force_finish:
                    failure_reason = (
                        "autofill_rejection"
                        if autofilled_fields and autofill_rejection_count >= 2
                        else "consecutive_arg_failures"
                    )
                    logger.warning(
                        "planner_arg_failure_threshold",
                        extra={
                            "tool": spec.name,
                            "consecutive_failures": consecutive_failures,
                            "autofill_rejection_count": autofill_rejection_count,
                            "threshold": planner._max_consecutive_arg_failures,
                            "last_error": arg_validation_error,
                            "failure_reason": failure_reason,
                        },
                    )
                    trajectory.steps.append(TrajectoryStep(action=action, error=arg_validation_error))
                    trajectory.artifacts = artifact_collector.snapshot()
                    trajectory.sources = source_collector.snapshot()
                    return planner._finish(
                        trajectory,
                        reason="no_path",
                        payload={
                            "requires_followup": True,
                            "failure_reason": failure_reason,
                            "tool": spec.name,
                            "last_error": arg_validation_error,
                            "missing_fields": list(autofilled_fields) if autofilled_fields else None,
                        },
                        thought=(
                            f"Cannot proceed: {failure_reason} for tool '{spec.name}'. "
                            f"Last error: {arg_validation_error}"
                        ),
                        constraints=tracker,
                        metadata_extra={"requires_followup": True},
                    )

                # Try arg-fill if eligible (only for autofilled fields, i.e. missing required args)
                if autofilled_fields and planner._is_arg_fill_eligible(
                    spec, autofilled_fields, trajectory
                ):
                    filled_args = await planner._attempt_arg_fill(
                        trajectory,
                        spec,
                        action,
                        list(autofilled_fields),
                    )

                    if filled_args is not None:
                        # Merge filled args into action
                        merged_args = dict(action.args or {})
                        merged_args.update(filled_args)

                        # Re-validate with merged args
                        try:
                            parsed_args = spec.args_model.model_validate(merged_args)
                            action.args = merged_args

                            # Re-run arg validation (placeholders, custom validators)
                            revalidation_error = planner._apply_arg_validation(
                                trajectory,
                                spec=spec,
                                action=action,
                                parsed_args=parsed_args,
                                autofilled_fields=(),  # No longer autofilled
                            )

                            if revalidation_error is None:
                                # Success! Reset failure counters and proceed to tool execution
                                trajectory.metadata["consecutive_arg_failures"] = 0
                                trajectory.metadata["arg_fill_attempted"] = False

                                logger.info(
                                    "arg_fill_merged_success",
                                    extra={
                                        "tool": spec.name,
                                        "filled_fields": list(filled_args.keys()),
                                    },
                                )

                                # Jump to tool execution (parsed_args is now valid)
                                # We need to NOT continue the loop, but proceed with execution below
                                # This is done by not entering the repair flow
                                pass  # Fall through to tool execution
                            else:
                                # Arg-fill succeeded but validation still failed
                                logger.warning(
                                    "arg_fill_revalidation_failed",
                                    extra={
                                        "tool": spec.name,
                                        "filled_fields": list(filled_args.keys()),
                                        "error": revalidation_error,
                                    },
                                )
                                # Fall through to repair message
                                repair_msg = prompts.render_arg_repair_message(
                                    spec.name,
                                    revalidation_error,
                                )
                                if isinstance(trajectory.metadata, MutableMapping):
                                    trajectory.metadata["arg_repair_message"] = repair_msg
                                error = prompts.render_validation_error(spec.name, revalidation_error)
                                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                                trajectory.summary = None
                                continue

                        except ValidationError as merge_exc:
                            # Merge failed validation
                            logger.warning(
                                "arg_fill_merge_validation_failed",
                                extra={
                                    "tool": spec.name,
                                    "filled_fields": list(filled_args.keys()),
                                    "error": str(merge_exc),
                                },
                            )
                            # Fall through to repair message
                            repair_msg = prompts.render_arg_repair_message(
                                spec.name,
                                json.dumps(merge_exc.errors(), ensure_ascii=False),
                            )
                            if isinstance(trajectory.metadata, MutableMapping):
                                trajectory.metadata["arg_repair_message"] = repair_msg
                            error = prompts.render_validation_error(
                                spec.name,
                                json.dumps(merge_exc.errors(), ensure_ascii=False),
                            )
                            trajectory.steps.append(TrajectoryStep(action=action, error=error))
                            trajectory.summary = None
                            continue
                    else:
                        # Arg-fill failed, generate user-friendly clarification
                        field_descriptions = planner._extract_field_descriptions(spec)
                        clarification = prompts.render_arg_fill_clarification(
                            spec.name,
                            list(autofilled_fields),
                            field_descriptions,
                        )

                        # Use clarification as the failure message instead of diagnostic dump
                        trajectory.steps.append(TrajectoryStep(action=action, error=arg_validation_error))
                        trajectory.artifacts = artifact_collector.snapshot()
                        trajectory.sources = source_collector.snapshot()
                        return planner._finish(
                            trajectory,
                            reason="no_path",
                            payload={
                                "requires_followup": True,
                                "failure_reason": "arg_fill_failed",
                                "tool": spec.name,
                                "clarification": clarification,
                                "missing_fields": list(autofilled_fields),
                            },
                            thought=clarification,
                            constraints=tracker,
                            metadata_extra={"requires_followup": True},
                        )
                else:
                    # Arg-fill not eligible or not enabled, use standard repair flow
                    # Choose repair message based on whether this was an autofill rejection
                    if autofilled_fields:
                        # First autofill rejection: tell model exactly which fields it forgot
                        repair_msg = prompts.render_missing_args_message(
                            spec.name,
                            list(autofilled_fields),
                            user_query=(trajectory.resume_user_input or trajectory.query),
                        )
                    else:
                        # Regular arg validation failure
                        repair_msg = prompts.render_arg_repair_message(
                            spec.name,
                            arg_validation_error,
                        )

                    if isinstance(trajectory.metadata, MutableMapping):
                        trajectory.metadata["arg_repair_message"] = repair_msg
                    error = prompts.render_validation_error(spec.name, arg_validation_error)
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

            tool_call_id = f"call_{current_action_seq}_{len(trajectory.steps)}"
            try:
                args_payload = parsed_args.model_dump(mode="json")
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
                        "action_seq": current_action_seq,
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
                        "action_seq": current_action_seq,
                    },
                )
            )

            ctx = _PlannerContext(planner, trajectory)
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
                                    parent_task_id=parent_task_id
                                    if isinstance(parent_task_id, str)
                                    else None,
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
                                    parent_task_id=parent_task_id
                                    if isinstance(parent_task_id, str)
                                    else None,
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
                                        "action_seq": current_action_seq,
                                    },
                                )
                            )
                            trajectory.steps.append(
                                TrajectoryStep(
                                    action=action,
                                    observation=observation_json,
                                    llm_observation=observation_json,
                                )
                            )
                            tracker.record_hop()
                            trajectory.summary = None
                            last_observation = observation_json
                            trajectory.resume_user_input = None
                            continue

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
                tracker.record_hop()
                pause_chunks = ctx._collect_chunks()
                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        observation={
                            "pause": signal.pause.reason,
                            "payload": signal.pause.payload,
                        },
                        streams=pause_chunks or None,
                    )
                )
                trajectory.summary = None
                await planner._record_pause(signal.pause, trajectory, tracker)
                planner._emit_event(
                    PlannerEvent(
                        event_type="tool_call_result",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "tool_call_id": tool_call_id,
                            "tool_name": spec.name,
                            "result_json": _safe_json_dumps(
                                {"pause": signal.pause.reason, "payload": dict(signal.pause.payload)}
                            ),
                            "action_seq": current_action_seq,
                        },
                    )
                )
                return signal.pause
            except Exception as exc:
                failure_payload = planner._build_failure_payload(spec, parsed_args, exc)
                error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
                failure_chunks = ctx._collect_chunks()
                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        error=error,
                        failure=failure_payload,
                        streams=failure_chunks or None,
                    )
                )
                tracker.record_hop()
                trajectory.summary = None
                last_observation = None
                planner._emit_event(
                    PlannerEvent(
                        event_type="tool_call_result",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "tool_call_id": tool_call_id,
                            "tool_name": spec.name,
                            "result_json": _safe_json_dumps({"error": error, "failure": failure_payload}),
                            "action_seq": current_action_seq,
                        },
                    )
                )
                continue

            step_chunks = ctx._collect_chunks()

            try:
                observation = spec.out_model.model_validate(result)
            except ValidationError as exc:
                error = prompts.render_output_validation_error(
                    spec.name,
                    json.dumps(exc.errors(), ensure_ascii=False),
                )
                tracker.record_hop()
                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        error=error,
                        streams=step_chunks or None,
                    )
                )
                trajectory.summary = None
                last_observation = None
                planner._emit_event(
                    PlannerEvent(
                        event_type="tool_call_result",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "tool_call_id": tool_call_id,
                            "tool_name": spec.name,
                            "result_json": _safe_json_dumps({"error": error}),
                            "action_seq": current_action_seq,
                        },
                    )
                )
                continue

            observation_json = observation.model_dump(mode="json")

            planner._artifact_registry.register_tool_artifacts(
                spec.name,
                spec.out_model,
                observation_json,
                step_index=len(trajectory.steps),
            )
            if isinstance(trajectory.metadata, MutableMapping):
                planner._artifact_registry.write_snapshot(trajectory.metadata)

            # Apply observation size guardrails
            observation_json, was_clamped = await planner._clamp_observation(
                observation_json,
                spec.name,
                len(trajectory.steps),
            )

            artifact_collector.collect(spec.name, spec.out_model, observation_json)
            source_collector.collect(spec.out_model, observation_json)

            # If observation was clamped, use it directly; otherwise apply artifact redaction
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
                        "action_seq": current_action_seq,
                    },
                )
            )
            trajectory.steps.append(
                TrajectoryStep(
                    action=action,
                    observation=observation_json,
                    llm_observation=llm_obs,
                    streams=step_chunks or None,
                )
            )
            tracker.record_hop()
            trajectory.summary = None
            last_observation = observation_json
            trajectory.artifacts = artifact_collector.snapshot()
            trajectory.sources = source_collector.snapshot()
            planner._record_hint_progress(spec.name, trajectory)
            trajectory.resume_user_input = None

            # Emit step complete event
            step_latency = (planner._time_source() - step_start_ts) * 1000  # ms
            planner._emit_event(
                PlannerEvent(
                    event_type="step_complete",
                    ts=planner._time_source(),
                    trajectory_step=len(trajectory.steps) - 1,
                    thought=action.thought,
                    node_name=spec.name,
                    latency_ms=step_latency,
                )
            )

            # Reset consecutive arg failure counter on successful tool execution
            if trajectory.metadata.get("consecutive_arg_failures"):
                trajectory.metadata["consecutive_arg_failures"] = 0

        if tracker.deadline_triggered or tracker.hop_exhausted:
            thought = (
                prompts.render_deadline_exhausted()
                if tracker.deadline_triggered
                else prompts.render_hop_budget_violation(planner._hop_budget or 0)
            )
            trajectory.artifacts = artifact_collector.snapshot()
            trajectory.sources = source_collector.snapshot()
            return planner._finish(
                trajectory,
                reason="budget_exhausted",
                payload=last_observation,
                thought=thought,
                constraints=tracker,
            )
        trajectory.artifacts = artifact_collector.snapshot()
        trajectory.sources = source_collector.snapshot()
        return planner._finish(
            trajectory,
            reason="no_path",
            payload=last_observation,
            thought="iteration limit reached",
            constraints=tracker,
        )
    finally:
        planner._active_trajectory = None
        planner._active_tracker = None
