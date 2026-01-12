"""Parallel execution helpers for the planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from . import prompts
from .models import ParallelCall, ParallelJoin, PlannerAction, PlannerPause
from .tool_calls import execute_tool_call
from .trajectory import Trajectory, TrajectoryStep

logger = logging.getLogger("penguiflow.planner")


@dataclass(slots=True)
class _BranchExecutionResult:
    """Compatibility container for parallel branch results (used in tests)."""

    observation: BaseModel | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None


async def execute_parallel_plan(
    planner: Any,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: Any,
    *,
    action_seq: int,
    artifact_collector: Any | None = None,
    source_collector: Any | None = None,
) -> tuple[Any | None, PlannerPause | None]:
    if action.next_node != "parallel":
        error = prompts.render_parallel_with_next_node(action.next_node)
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    steps_payload = action.args.get("steps")
    if not isinstance(steps_payload, list) or not steps_payload:
        error = prompts.render_empty_parallel_plan()
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    join_spec_payload = action.args.get("join")
    join_model: ParallelJoin | None = None
    if isinstance(join_spec_payload, Mapping):
        try:
            join_model = ParallelJoin.model_validate(join_spec_payload)
        except ValidationError:
            join_model = None

    validation_errors: list[str] = []
    entries: list[tuple[Any, Any, BaseModel]] = []
    plan: list[ParallelCall] = []
    for item in steps_payload:
        try:
            plan.append(ParallelCall.model_validate(item))
        except ValidationError:
            continue
    if not plan:
        error = prompts.render_empty_parallel_plan()
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    for plan_item in plan:
        spec = planner._spec_by_name.get(plan_item.node)
        if spec is None:
            validation_errors.append(prompts.render_invalid_node(plan_item.node, list(planner._spec_by_name.keys())))
            continue
        try:
            parsed_args = spec.args_model.model_validate(plan_item.args or {})
        except ValidationError as exc:
            validation_errors.append(
                prompts.render_validation_error(
                    spec.name,
                    json.dumps(exc.errors(), ensure_ascii=False),
                )
            )
            continue
        entries.append((plan_item, spec, parsed_args))

    if validation_errors:
        error = prompts.render_parallel_setup_error(validation_errors)
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    # Execute branches concurrently, emitting tool_call_* events from the shared tool executor.
    step_index = len(trajectory.steps)
    steering = getattr(planner, "_steering", None)
    import asyncio

    results = await asyncio.gather(
        *(
            execute_tool_call(
                planner,
                trajectory=trajectory,
                spec=spec,
                parsed_args=parsed_args,
                tool_call_id=f"call_{action_seq}_parallel_{branch_index}",
                action_seq=action_seq,
                step_index=step_index,
                artifact_collector=artifact_collector,
                source_collector=source_collector,
                artifact_metadata_extra={"parallel_branch": branch_index},
                steering=steering,
            )
            for branch_index, (_, spec, parsed_args) in enumerate(entries)
        )
    )

    branch_payloads: list[dict[str, Any]] = []
    llm_branch_payloads: list[dict[str, Any]] = []
    success_payloads: list[Any] = []
    failure_entries: list[dict[str, Any]] = []
    pause_result: PlannerPause | None = None

    for _branch_index, ((_, spec, parsed_args), outcome) in enumerate(zip(entries, results, strict=False)):
        tracker.record_hop()
        payload: dict[str, Any] = {
            "node": spec.name,
            "args": parsed_args.model_dump(mode="json"),
        }
        llm_payload: dict[str, Any] = dict(payload)
        if outcome.pause is not None and pause_result is None:
            pause_result = outcome.pause
            payload["pause"] = {"reason": outcome.pause.reason, "payload": dict(outcome.pause.payload)}
            llm_payload["pause"] = payload["pause"]
        elif outcome.observation is not None:
            obs_json = dict(outcome.observation)
            payload["observation"] = obs_json
            success_payloads.append(obs_json)
            llm_payload["observation"] = dict(outcome.llm_observation or obs_json)
        else:
            error_text = outcome.error or prompts.render_parallel_unknown_failure(spec.name)
            payload["error"] = error_text
            llm_payload["error"] = error_text
            if outcome.failure is not None:
                payload["failure"] = dict(outcome.failure)
                failure_entries.append(
                    {
                        "node": spec.name,
                        "error": error_text,
                        "failure": dict(outcome.failure),
                    }
                )
            else:
                failure_entries.append({"node": spec.name, "error": error_text})
        branch_payloads.append(payload)
        llm_branch_payloads.append(llm_payload)

    stats = {"success": len(success_payloads), "failed": len(failure_entries)}
    observation: dict[str, Any] = {
        "branches": branch_payloads,
        "stats": stats,
    }
    llm_observation: dict[str, Any] = {
        "branches": llm_branch_payloads,
        "stats": stats,
    }

    if pause_result is not None:
        observation["join"] = {
            "status": "skipped",
            "reason": "pause",
        }
        llm_observation["join"] = observation["join"]
        trajectory.steps.append(TrajectoryStep(action=action, observation=observation, llm_observation=llm_observation))
        trajectory.summary = None
        await planner._record_pause(pause_result, trajectory, tracker)
        return observation, pause_result

    join_payload: dict[str, Any] | None = None
    join_llm_payload: dict[str, Any] | None = None
    join_error: str | None = None
    join_failure: Mapping[str, Any] | None = None
    join_spec: Any | None = None
    join_args_template: dict[str, Any] | None = None
    implicit_join_injection = False

    if join_model is not None:
        join_spec = planner._spec_by_name.get(join_model.node)
        if join_spec is None:
            join_error = prompts.render_invalid_node(join_model.node, list(planner._spec_by_name.keys()))
        elif failure_entries:
            join_payload = {
                "node": join_spec.name,
                "status": "skipped",
                "reason": "branch_failures",
                "failures": list(failure_entries),
            }
        else:
            join_args_template = dict(join_model.args or {})
            injection_mapping = join_model.inject.mapping if join_model.inject else {}
            explicit_injection = bool(injection_mapping)
            if explicit_injection:
                injection_sources = {
                    "$results": list(success_payloads),
                    "$expect": len(entries),
                    "$branches": list(branch_payloads),
                    "$failures": list(failure_entries),
                    "$success_count": len(success_payloads),
                    "$failure_count": len(failure_entries),
                }
                try:
                    for target, source in injection_mapping.items():
                        if source not in injection_sources:
                            raise KeyError(source)
                        join_args_template[target] = injection_sources[source]
                except KeyError as exc:
                    join_error = prompts.render_invalid_join_injection_source(
                        exc.args[0] if exc.args else str(exc),
                        sorted(injection_sources),
                    )
            elif join_model.inject is None:
                implicit_join_injection = True
                logger.warning(
                    "Implicit join injection is deprecated. Use explicit 'inject' mapping for join tool '%s'.",
                    join_spec.name,
                )
                join_fields = join_spec.args_model.model_fields
                if "expect" in join_fields and "expect" not in join_args_template:
                    join_args_template["expect"] = len(entries)
                if "results" in join_fields and "results" not in join_args_template:
                    join_args_template["results"] = list(success_payloads)
                if "branches" in join_fields and "branches" not in join_args_template:
                    join_args_template["branches"] = list(branch_payloads)
                if "failures" in join_fields and "failures" not in join_args_template:
                    join_args_template["failures"] = []
                if "success_count" in join_fields and "success_count" not in join_args_template:
                    join_args_template["success_count"] = len(success_payloads)
                if "failure_count" in join_fields and "failure_count" not in join_args_template:
                    join_args_template["failure_count"] = len(failure_entries)

            if join_error is None:
                try:
                    join_args = join_spec.args_model.model_validate(join_args_template)
                except ValidationError as exc:
                    join_error = prompts.render_join_validation_error(
                        join_spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                        suggest_inject=implicit_join_injection,
                    )
                else:
                    join_outcome = await execute_tool_call(
                        planner,
                        trajectory=trajectory,
                        spec=join_spec,
                        parsed_args=join_args,
                        tool_call_id=f"call_{action_seq}_parallel_join",
                        action_seq=action_seq,
                        step_index=step_index,
                        artifact_collector=artifact_collector,
                        source_collector=source_collector,
                        tool_context_update={
                            "parallel_results": branch_payloads,
                            "parallel_success_count": len(success_payloads),
                            "parallel_failure_count": len(failure_entries),
                            "parallel_failures": list(failure_entries) if failure_entries else None,
                            "parallel_input": dict(join_args_template),
                        },
                        artifact_metadata_extra={"parallel_join": True},
                        steering=steering,
                    )
                    tracker.record_hop()

                    if join_outcome.pause is not None:
                        join_payload = {
                            "node": join_spec.name,
                            "pause": {
                                "reason": join_outcome.pause.reason,
                                "payload": dict(join_outcome.pause.payload),
                            },
                        }
                        observation["join"] = join_payload
                        trajectory.steps.append(TrajectoryStep(action=action, observation=observation))
                        trajectory.summary = None
                        await planner._record_pause(join_outcome.pause, trajectory, tracker)
                        return observation, join_outcome.pause

                    if join_outcome.error is not None:
                        join_error = join_outcome.error
                        join_failure = join_outcome.failure
                    elif join_outcome.observation is not None:
                        join_json = dict(join_outcome.observation)
                        join_payload = {"node": join_spec.name, "observation": join_json}
                        join_llm_payload = {
                            "node": join_spec.name,
                            "observation": dict(join_outcome.llm_observation or join_json),
                        }

    if join_model is not None and "join" not in observation:
        if join_payload is not None:
            observation["join"] = join_payload
            llm_observation["join"] = join_llm_payload or join_payload
        else:
            join_name = (
                join_spec.name if join_spec is not None else join_model.node if join_model is not None else "join"
            )
            join_entry: dict[str, Any] = {"node": join_name}
            if join_error is not None:
                join_entry["error"] = join_error
            if join_failure is not None:
                join_entry["failure"] = dict(join_failure)
            if "error" in join_entry or "failure" in join_entry:
                observation["join"] = join_entry
                llm_observation["join"] = join_entry
            elif join_model is not None and join_spec is None:
                observation["join"] = join_entry
                llm_observation["join"] = join_entry

    trajectory.steps.append(TrajectoryStep(action=action, observation=observation, llm_observation=llm_observation))
    trajectory.summary = None
    return observation, None


__all__ = ["_BranchExecutionResult", "execute_parallel_plan"]
