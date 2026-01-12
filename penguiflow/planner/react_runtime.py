"""Runtime helpers for the React planner loop."""

from __future__ import annotations

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
from .error_recovery import ErrorRecoveryConfig, step_with_recovery
from .models import PlannerAction, PlannerEvent, PlannerFinish, PlannerPause, ReflectionCritique
from .streaming import _StreamingArgsExtractor
from .tool_aliasing import rewrite_action_node
from .tool_calls import execute_tool_call
from .trajectory import Trajectory, TrajectoryStep
from .validation_repair import _autofill_missing_args, _coerce_tool_context, _validate_llm_context

logger = logging.getLogger("penguiflow.planner")

_TASK_SERVICE_KEY = "task_service"
_MULTI_ACTION_BLOCKED_NODES = frozenset({"final_response", "render_component", "tasks.spawn"})
_MULTI_ACTION_READONLY_SIDE_EFFECTS = frozenset({"pure", "read"})


def _alternate_candidates(action: PlannerAction) -> list[PlannerAction]:
    """Convert action.alternate_actions into PlannerAction instances."""

    raw = action.alternate_actions
    if not isinstance(raw, list) or not raw:
        return []
    candidates: list[PlannerAction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        next_node = item.get("next_node")
        args = item.get("args")
        if not isinstance(next_node, str) or not next_node.strip():
            continue
        if not isinstance(args, dict):
            args = {}
        candidates.append(PlannerAction(next_node=next_node, args=dict(args)))
    return candidates


def _candidate_is_executable_tool(planner: Any, candidate: PlannerAction) -> bool:
    if candidate.next_node in _MULTI_ACTION_BLOCKED_NODES:
        return False
    if not candidate.is_tool_call():
        return False
    spec = getattr(planner, "_spec_by_name", {}).get(candidate.next_node)
    if spec is None:
        return False
    return True


def _maybe_select_alternate_action(
    planner: Any,
    action: PlannerAction,
    *,
    prefer_same_tool: bool,
    require_read_only: bool,
) -> tuple[PlannerAction | None, str | None]:
    """Pick an alternate candidate action that can be executed."""

    candidates = _alternate_candidates(action)
    if not candidates:
        return None, None

    def _passes_readonly(spec: Any) -> bool:
        if not require_read_only:
            return True
        side_effects = getattr(spec, "side_effects", None)
        return side_effects in _MULTI_ACTION_READONLY_SIDE_EFFECTS

    # Prefer candidates that target the same tool (likely args correction).
    ordered: list[PlannerAction] = []
    if prefer_same_tool and action.next_node:
        ordered.extend([c for c in candidates if c.next_node == action.next_node])
    ordered.extend([c for c in candidates if c.next_node != action.next_node])

    for candidate in ordered:
        if candidate.next_node in _MULTI_ACTION_BLOCKED_NODES:
            continue
        if not candidate.is_tool_call():
            continue
        spec = planner._spec_by_name.get(candidate.next_node)
        if spec is None:
            continue
        if not _passes_readonly(spec):
            continue
        try:
            spec.args_model.model_validate(candidate.args)
        except ValidationError:
            continue
        return candidate, spec.name
    return None, None


def _apply_steering(planner: Any, trajectory: Trajectory) -> None:
    steering: SteeringInbox | None = getattr(planner, "_steering", None)
    if steering is None:
        logger.info("_apply_steering: no steering inbox connected to planner")
        return

    if steering.cancelled:
        raise SteeringCancelled(steering.cancel_reason)

    has_pending = steering.has_event()
    logger.info(
        "steering_check",
        extra={"has_pending": has_pending, "step": len(trajectory.steps)},
    )
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
        elif event.event_type == SteeringEventType.USER_MESSAGE:
            # Rich injection with task context and interpretation guidance
            user_text = event.payload.get("text", "")
            active_tasks = event.payload.get("active_tasks", [])
            injection = {
                "steering": {
                    "event_id": event.event_id,
                    "event_type": "USER_MESSAGE",
                    "user_text": user_text,
                    "active_background_tasks": active_tasks,
                    "instructions": (
                        "The user sent this message while you are working. "
                        "Interpret their intent: Are they providing clarification, "
                        "changing direction, asking about status, or controlling "
                        "a background task? If referencing a background task ambiguously, "
                        "use select_option to let them choose which one. "
                        "Acknowledge naturally and act accordingly."
                    ),
                }
            }
            trajectory.steering_inputs.append(json.dumps(injection, ensure_ascii=False))


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
    error_recovery_cfg = getattr(planner, "_error_recovery_config", None)
    result = await run_loop(planner, trajectory, tracker=None, error_recovery_config=error_recovery_cfg)
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

    error_recovery_cfg = getattr(planner, "_error_recovery_config", None)
    result = await run_loop(planner, trajectory, tracker=tracker, error_recovery_config=error_recovery_cfg)
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
        "has_plan": action.next_node == "parallel",
    }
    # For finish actions, log the args to help debug answer extraction issues
    if action.next_node == "final_response":
        args_preview = str(action.args)
        if len(args_preview) > 500:
            args_preview = args_preview[:500] + "..."
        action_extra["args_preview"] = args_preview
        action_extra["args_keys"] = list(action.args.keys())
        action_extra["has_raw_answer"] = "raw_answer" in action.args
        action_extra["has_answer"] = "answer" in action.args
    logger.info("planner_action", extra=action_extra)


async def _handle_parallel_plan(
    planner: Any,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    artifact_collector: _ArtifactCollector,
    source_collector: _SourceCollector,
    *,
    action_seq: int,
) -> tuple[Any | None, PlannerPause | None]:
    parallel_observation, pause = await planner._execute_parallel_plan(
        action,
        trajectory,
        tracker,
        artifact_collector,
        source_collector,
        action_seq=action_seq,
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
    action_seq: int,
) -> PlannerFinish:
    # Check if raw_answer is missing and attempt finish repair
    answer = action.answer_text()
    has_answer = answer is not None and answer not in {"", "<auto>"} and answer.strip() != ""

    if not has_answer and not trajectory.metadata.get("finish_repair_attempted"):
        # Model tried to finish without an answer - attempt repair
        # Log full args for debugging what the model actually returned
        args_dump = str(action.args)
        if len(args_dump) > 1000:
            args_dump = args_dump[:1000] + "..."
        # Include raw LLM response for exact model output debugging
        raw_response = action.raw_llm_response
        if raw_response and len(raw_response) > 2000:
            raw_response = raw_response[:2000] + "..."
        logger.info(
            "finish_repair_attempt",
            extra={
                "args_keys": list(action.args.keys()),
                "args_dump": args_dump,
                "thought": action.thought,
                "answer_text_result": repr(answer),  # Show what answer_text() returned
                "raw_llm_response": raw_response,  # Exact model output
            },
        )

        filled_answer = await planner._attempt_finish_repair(
            trajectory,
            action,
            action_seq=action_seq,
        )

        if filled_answer is not None:
            # Success! Update action args with both canonical + legacy keys.
            action.args["answer"] = filled_answer
            action.args["raw_answer"] = filled_answer
            logger.info(
                "finish_repair_success",
                extra={
                    "answer_len": len(filled_answer),
                    "args_keys": list(action.args.keys()),
                },
            )
        else:
            logger.warning(
                "finish_repair_failed",
                extra={"thought": action.thought},
            )

    candidate_answer = action.args if action.args else last_observation
    # Trace: Log candidate_answer state (helps debug answer loss issues)
    _ca_raw = candidate_answer.get("raw_answer") if isinstance(candidate_answer, dict) else None
    logger.info(
        "finish_candidate_answer",
        extra={
            "has_raw_answer": _ca_raw is not None,
            "raw_answer_len": len(_ca_raw) if isinstance(_ca_raw, str) else None,
        },
    )
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

    # Trace: Verify answer state before final payload
    _pre_raw = candidate_answer.get("raw_answer") if isinstance(candidate_answer, dict) else None
    logger.info(
        "finish_pre_payload",
        extra={
            "has_raw_answer": _pre_raw is not None,
            "raw_answer_len": len(_pre_raw) if isinstance(_pre_raw, str) else None,
        },
    )

    final_payload = planner._build_final_payload(
        candidate_answer,
        last_observation,
        trajectory.artifacts,
        trajectory.sources,
    )
    logger.info(
        "finish_payload_built",
        extra={"raw_answer_len": len(final_payload.raw_answer) if final_payload.raw_answer else 0},
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
    error_recovery_config: ErrorRecoveryConfig | None = None,
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

            # Support executing additional tool actions that were emitted in the same
            # LLM response as the previous step (multi-JSON outputs).
            queued_action: PlannerAction | None = None
            if isinstance(trajectory.metadata, MutableMapping):
                pending = trajectory.metadata.get("pending_actions")
                if isinstance(pending, list) and pending:
                    item = pending.pop(0)
                    if isinstance(item, dict):
                        next_node = item.get("next_node")
                        args = item.get("args")
                        if isinstance(next_node, str):
                            queued_action = PlannerAction(
                                next_node=next_node,
                                args=dict(args) if isinstance(args, dict) else {},
                            )

            if queued_action is not None:
                action = queued_action
            else:
                action = await step_with_recovery(planner, trajectory, config=error_recovery_config)
                # If this action came from a mixed-output response, enqueue eligible
                # follow-up tool calls for sequential execution without another LLM call.
                if getattr(planner, "_multi_action_sequential", False) and action.alternate_actions:
                    if isinstance(trajectory.metadata, MutableMapping):
                        pending = trajectory.metadata.get("pending_actions")
                        if not isinstance(pending, list):
                            pending = []
                            trajectory.metadata["pending_actions"] = pending
                        max_tools = int(getattr(planner, "_multi_action_max_tools", 0) or 0)
                        read_only_only = bool(getattr(planner, "_multi_action_read_only_only", True))
                        added = 0
                        for candidate in _alternate_candidates(action):
                            if candidate.next_node in _MULTI_ACTION_BLOCKED_NODES:
                                continue
                            if not candidate.is_tool_call():
                                continue
                            spec = planner._spec_by_name.get(candidate.next_node)
                            if spec is None:
                                continue
                            if read_only_only and (
                                getattr(spec, "side_effects", None) not in _MULTI_ACTION_READONLY_SIDE_EFFECTS
                            ):
                                continue
                            pending.append({"next_node": candidate.next_node, "args": dict(candidate.args or {})})
                            added += 1
                            if max_tools > 0 and added >= max_tools:
                                break
                        if added:
                            planner._emit_event(
                                PlannerEvent(
                                    event_type="multi_action_enqueued",
                                    ts=planner._time_source(),
                                    trajectory_step=len(trajectory.steps),
                                    extra={"count": added},
                                )
                            )

            # Normalize aliased tool names to their real names (RFC alignment)
            tool_aliases = getattr(planner, "_tool_aliases", {})
            if tool_aliases:
                action.next_node = rewrite_action_node(action.next_node, alias_to_real=tool_aliases)

            # Log the action received from LLM
            _log_action_received(planner, action, trajectory)

            # Check constraints BEFORE executing parallel plan or any action
            constraint_error = planner._check_action_constraints(action, trajectory, tracker)
            if constraint_error is not None:
                trajectory.steps.append(TrajectoryStep(action=action, error=constraint_error))
                trajectory.summary = None
                continue

            if action.next_node == "parallel":
                last_observation, pause = await _handle_parallel_plan(
                    planner,
                    action,
                    trajectory,
                    tracker,
                    artifact_collector,
                    source_collector,
                    action_seq=current_action_seq,
                )
                if pause is not None:
                    return pause
                continue

            if action.next_node == "final_response":
                # Before finishing, check if there are pending steering events
                # that arrived while the LLM was generating this finish action.
                # If so, process them and continue instead of finishing.
                steering_inbox: SteeringInbox | None = getattr(planner, "_steering", None)
                if steering_inbox is not None and steering_inbox.has_event():
                    _apply_steering(planner, trajectory)
                    if trajectory.steering_inputs:
                        # User sent steering while LLM was responding - defer finish
                        logger.info(
                            "finish_deferred_for_steering",
                            extra={
                                "pending_inputs": len(trajectory.steering_inputs),
                                "step": len(trajectory.steps),
                            },
                        )
                        # Record the attempted finish as a step so LLM knows it tried
                        trajectory.steps.append(
                            TrajectoryStep(
                                action=action,
                                observation="Finish deferred: new steering input received from user.",
                            )
                        )
                        trajectory.summary = None
                        continue  # Process steering in next iteration

                return await _handle_finish_action(
                    planner,
                    action,
                    trajectory,
                    tracker,
                    last_observation,
                    artifact_collector,
                    source_collector,
                    action_seq=current_action_seq,
                )

            if not action.is_tool_call():
                error = prompts.render_invalid_node(
                    action.next_node,
                    list(planner._spec_by_name.keys()),
                )
                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                trajectory.summary = None
                continue

            spec = planner._spec_by_name.get(action.next_node)
            if spec is None:
                # Try alternate candidates (multi-action outputs) before requiring another LLM step.
                alt, _ = _maybe_select_alternate_action(
                    planner,
                    action,
                    prefer_same_tool=False,
                    require_read_only=False,
                )
                if alt is not None:
                    previous = action.next_node
                    action.next_node = alt.next_node
                    action.args = dict(alt.args or {})
                    # Remove used candidate from alternates to avoid reuse.
                    if isinstance(action.alternate_actions, list):
                        action.alternate_actions = [
                            item
                            for item in action.alternate_actions
                            if not (
                                isinstance(item, dict)
                                and item.get("next_node") == alt.next_node
                                and isinstance(item.get("args"), dict)
                                and dict(item.get("args") or {}) == dict(alt.args or {})
                            )
                        ]
                    planner._emit_event(
                        PlannerEvent(
                            event_type="multi_action_fallback_used",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={"from": previous, "to": action.next_node, "reason": "unknown_tool"},
                        )
                    )
                    spec = planner._spec_by_name.get(action.next_node)
                error = prompts.render_invalid_node(
                    action.next_node,
                    list(planner._spec_by_name.keys()),
                )
                if spec is None:
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

            autofilled_fields: tuple[str, ...] = ()
            try:
                parsed_args = spec.args_model.model_validate(action.args)
            except ValidationError as exc:
                # If the model emitted multiple candidates, try a same-tool alternative args payload first.
                alt, _ = _maybe_select_alternate_action(
                    planner,
                    action,
                    prefer_same_tool=True,
                    require_read_only=False,
                )
                if alt is not None and alt.next_node == action.next_node:
                    action.args = dict(alt.args or {})
                    if isinstance(action.alternate_actions, list):
                        action.alternate_actions = [
                            item
                            for item in action.alternate_actions
                            if not (
                                isinstance(item, dict)
                                and item.get("next_node") == alt.next_node
                                and isinstance(item.get("args"), dict)
                                and dict(item.get("args") or {}) == dict(alt.args or {})
                            )
                        ]
                    planner._emit_event(
                        PlannerEvent(
                            event_type="multi_action_fallback_used",
                            ts=planner._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={"tool": spec.name, "reason": "args_validation"},
                        )
                    )
                    parsed_args = spec.args_model.model_validate(action.args)
                else:
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
                # Use per-tool counters to avoid one tool's failures affecting another tool's chances
                autofill_rejection_key = f"autofill_rejection_count_{spec.name}"
                autofill_rejection_count = int(trajectory.metadata.get(autofill_rejection_key, 0))
                consecutive_failures_key = f"consecutive_arg_failures_{spec.name}"
                consecutive_failures = int(trajectory.metadata.get(consecutive_failures_key, 0))

                # Force finish conditions:
                # 1. Second autofill rejection for THIS tool (gave model one chance with explicit field names)
                # 2. Consecutive failures threshold reached for THIS tool
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

                    # Attempt graceful failure: ask model to generate user-friendly response
                    # instead of returning a technical error
                    graceful_answer = await planner._attempt_graceful_failure(
                        trajectory,
                        action_seq=planner._action_seq,
                    )

                    if graceful_answer is not None:
                        # Success - return with a proper answer instead of no_path
                        return planner._finish(
                            trajectory,
                            reason="answer_complete",
                            payload={
                                "raw_answer": graceful_answer,
                                "graceful_failure": True,
                                "failure_reason": failure_reason,
                                "tool": spec.name,
                            },
                            thought=f"Gracefully handled {failure_reason} for tool '{spec.name}'",
                            constraints=tracker,
                        )

                    # Graceful failure didn't work - fall back to no_path
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
                                # Success! Reset ALL failure counters for this tool
                                # This is critical: autofill_rejection_count must reset so the tool
                                # gets fresh chances if it needs to retry later (e.g., tool fails
                                # for other reasons and model retries with autofill again)
                                trajectory.metadata["consecutive_arg_failures"] = 0
                                trajectory.metadata[f"consecutive_arg_failures_{spec.name}"] = 0
                                trajectory.metadata[f"autofill_rejection_count_{spec.name}"] = 0
                                trajectory.metadata[f"arg_fill_attempted_{spec.name}"] = False

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
            step_index = len(trajectory.steps)
            outcome = await execute_tool_call(
                planner,
                trajectory=trajectory,
                spec=spec,
                parsed_args=parsed_args,
                tool_call_id=tool_call_id,
                action_seq=current_action_seq,
                step_index=step_index,
                artifact_collector=artifact_collector,
                source_collector=source_collector,
                steering=steering,
            )

            if outcome.pause is not None:
                tracker.record_hop()
                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        observation={
                            "pause": outcome.pause.reason,
                            "payload": outcome.pause.payload,
                        },
                        streams=outcome.streams or None,
                    )
                )
                trajectory.summary = None
                await planner._record_pause(outcome.pause, trajectory, tracker)
                return outcome.pause

            if outcome.error is not None:
                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        error=outcome.error,
                        failure=outcome.failure,
                        streams=outcome.streams or None,
                    )
                )
                tracker.record_hop()
                trajectory.summary = None
                last_observation = None
                continue

            observation_json = outcome.observation or {}
            llm_obs = outcome.llm_observation or observation_json
            trajectory.steps.append(
                TrajectoryStep(
                    action=action,
                    observation=observation_json,
                    llm_observation=llm_obs,
                    streams=outcome.streams or None,
                )
            )
            tracker.record_hop()
            trajectory.summary = None
            last_observation = observation_json
            trajectory.resume_user_input = None

            # Preserve legacy behavior: background-spawned tools did not emit step_complete.
            if not outcome.background_spawned:
                trajectory.artifacts = artifact_collector.snapshot()
                trajectory.sources = source_collector.snapshot()

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

            # Reset consecutive arg failure counters on successful tool execution
            if trajectory.metadata.get("consecutive_arg_failures"):
                trajectory.metadata["consecutive_arg_failures"] = 0
            per_tool_key = f"consecutive_arg_failures_{spec.name}"
            if trajectory.metadata.get(per_tool_key):
                trajectory.metadata[per_tool_key] = 0

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
