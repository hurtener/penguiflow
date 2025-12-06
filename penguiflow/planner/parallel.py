"""Parallel execution helpers for the planner."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from . import prompts
from .llm import _redact_artifacts
from .models import PlannerAction, PlannerPause
from .pause import _PlannerPauseSignal
from .trajectory import Trajectory, TrajectoryStep

logger = logging.getLogger("penguiflow.planner")


@dataclass(slots=True)
class _BranchExecutionResult:
    observation: BaseModel | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None


async def _run_parallel_branch(planner: Any, spec: Any, args: BaseModel, ctx: Any) -> _BranchExecutionResult:
    try:
        raw = await spec.node.func(args, ctx)
    except _PlannerPauseSignal as signal:
        return _BranchExecutionResult(pause=signal.pause)
    except Exception as exc:  # pragma: no cover - exercised in tests via planner path
        failure_payload = planner._build_failure_payload(spec, args, exc)
        error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
        return _BranchExecutionResult(error=error, failure=failure_payload)

    try:
        observation = spec.out_model.model_validate(raw)
    except ValidationError as exc:
        error = prompts.render_output_validation_error(
            spec.name,
            json.dumps(exc.errors(), ensure_ascii=False),
        )
        return _BranchExecutionResult(error=error)

    return _BranchExecutionResult(observation=observation)


async def execute_parallel_plan(
    planner: Any,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: Any,
    artifact_collector: Any | None = None,
    source_collector: Any | None = None,
) -> tuple[Any | None, PlannerPause | None]:
    if action.next_node is not None:
        error = prompts.render_parallel_with_next_node(action.next_node)
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    if not action.plan:
        error = prompts.render_empty_parallel_plan()
        trajectory.steps.append(TrajectoryStep(action=action, error=error))
        trajectory.summary = None
        return None, None

    validation_errors: list[str] = []
    entries: list[tuple[Any, Any, BaseModel]] = []
    for plan_item in action.plan:
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

    ctx = planner._make_context(trajectory)
    results = await asyncio.gather(
        *(_run_parallel_branch(planner, spec, parsed_args, ctx) for (_, spec, parsed_args) in entries)
    )

    branch_payloads: list[dict[str, Any]] = []
    llm_branch_payloads: list[dict[str, Any]] = []
    success_payloads: list[Any] = []
    failure_entries: list[dict[str, Any]] = []
    pause_result: PlannerPause | None = None

    for (_, spec, parsed_args), outcome in zip(entries, results, strict=False):
        tracker.record_hop()
        payload: dict[str, Any] = {
            "node": spec.name,
            "args": parsed_args.model_dump(mode="json"),
        }
        llm_payload: dict[str, Any] = dict(payload)
        if outcome.pause is not None and pause_result is None:
            pause_result = outcome.pause
            payload["pause"] = {
                "reason": outcome.pause.reason,
                "payload": dict(outcome.pause.payload),
            }
            llm_payload["pause"] = payload["pause"]
        elif outcome.observation is not None:
            obs_json = outcome.observation.model_dump(mode="json")
            payload["observation"] = obs_json
            success_payloads.append(obs_json)
            llm_payload["observation"] = _redact_artifacts(spec.out_model, obs_json)
            if artifact_collector is not None:
                artifact_collector.collect(spec.name, spec.out_model, obs_json)
            if source_collector is not None:
                source_collector.collect(spec.out_model, obs_json)
            planner._record_hint_progress(spec.name, trajectory)
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

    if action.join is not None:
        join_spec = planner._spec_by_name.get(action.join.node)
        if join_spec is None:
            join_error = prompts.render_invalid_node(action.join.node, list(planner._spec_by_name.keys()))
        elif failure_entries:
            join_payload = {
                "node": join_spec.name,
                "status": "skipped",
                "reason": "branch_failures",
                "failures": list(failure_entries),
            }
        else:
            join_args_template = dict(action.join.args or {})
            injection_mapping = action.join.inject.mapping if action.join.inject else {}
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
            elif action.join.inject is None:
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
                    join_ctx = planner._make_context(trajectory)
                    join_ctx.tool_context.update(
                        {
                            "parallel_results": branch_payloads,
                            "parallel_success_count": len(success_payloads),
                            "parallel_failure_count": len(failure_entries),
                        }
                    )
                    if failure_entries:
                        join_ctx.tool_context["parallel_failures"] = list(failure_entries)
                    join_ctx.tool_context["parallel_input"] = dict(join_args_template)

                    try:
                        join_raw = await join_spec.node.func(join_args, join_ctx)
                    except _PlannerPauseSignal as signal:
                        tracker.record_hop()
                        join_payload = {
                            "node": join_spec.name,
                            "pause": {
                                "reason": signal.pause.reason,
                                "payload": dict(signal.pause.payload),
                            },
                        }
                        observation["join"] = join_payload
                        trajectory.steps.append(TrajectoryStep(action=action, observation=observation))
                        trajectory.summary = None
                        await planner._record_pause(signal.pause, trajectory, tracker)
                        return observation, signal.pause
                    except Exception as exc:  # pragma: no cover - handled in planner tests
                        tracker.record_hop()
                        join_error = f"tool '{join_spec.name}' raised {exc.__class__.__name__}: {exc}"
                        join_failure = planner._build_failure_payload(join_spec, join_args, exc)
                    else:
                        try:
                            join_model = join_spec.out_model.model_validate(join_raw)
                        except ValidationError as exc:
                            tracker.record_hop()
                            join_error = prompts.render_output_validation_error(
                                join_spec.name,
                                json.dumps(exc.errors(), ensure_ascii=False),
                            )
                        else:
                            tracker.record_hop()
                            planner._record_hint_progress(join_spec.name, trajectory)
                            join_json = join_model.model_dump(mode="json")
                            if artifact_collector is not None:
                                artifact_collector.collect(
                                    join_spec.name,
                                    join_spec.out_model,
                                    join_json,
                                )
                            join_payload = {
                                "node": join_spec.name,
                                "observation": join_json,
                            }
                            join_llm_payload = {
                                "node": join_spec.name,
                                "observation": _redact_artifacts(join_spec.out_model, join_json),
                            }

    if action.join is not None and "join" not in observation:
        if join_payload is not None:
            observation["join"] = join_payload
            llm_observation["join"] = join_llm_payload or join_payload
        else:
            join_name = (
                join_spec.name if join_spec is not None else action.join.node if action.join is not None else "join"
            )
            join_entry: dict[str, Any] = {"node": join_name}
            if join_error is not None:
                join_entry["error"] = join_error
            if join_failure is not None:
                join_entry["failure"] = dict(join_failure)
            if "error" in join_entry or "failure" in join_entry:
                observation["join"] = join_entry
                llm_observation["join"] = join_entry
            elif action.join is not None and join_spec is None:
                observation["join"] = join_entry
                llm_observation["join"] = join_entry

    trajectory.steps.append(TrajectoryStep(action=action, observation=observation, llm_observation=llm_observation))
    trajectory.summary = None
    return observation, None


__all__ = ["_BranchExecutionResult", "execute_parallel_plan"]
