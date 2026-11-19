"""Parallel execution helpers for the planner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from . import prompts
from .models import ParallelCall, ParallelJoin, PlannerAction, PlannerPause
from .react_utils import _serialize_validation_errors
from .tool_calls import ToolCallOutcome, execute_tool_call
from .trajectory import Trajectory, TrajectoryStep

if TYPE_CHECKING:
    from ..catalog import NodeSpec
    from ..steering import SteeringInbox
    from .artifact_handling import _ArtifactCollector, _SourceCollector
    from .constraints import _ConstraintTracker
    from .react import ReactPlanner

logger = logging.getLogger("penguiflow.planner")


@dataclass(slots=True)
class _BranchExecutionResult:
    """Compatibility container for parallel branch results (used in tests)."""

    observation: BaseModel | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None


_Branch = tuple[ParallelCall, "NodeSpec", BaseModel]


@dataclass(slots=True)
class _BranchResults:
    """Outcomes of a parallel plan's branches, shaped for the observation."""

    branch_payloads: list[dict[str, Any]] = field(default_factory=list)
    llm_branch_payloads: list[dict[str, Any]] = field(default_factory=list)
    success_payloads: list[dict[str, Any]] = field(default_factory=list)
    failure_entries: list[dict[str, Any]] = field(default_factory=list)
    pause: PlannerPause | None = None

    def add(
        self, spec: NodeSpec, parsed_args: BaseModel, outcome: ToolCallOutcome
    ) -> None:
        """Record one branch's pause, observation or error."""
        payload: dict[str, Any] = {
            "node": spec.name,
            "args": parsed_args.model_dump(mode="json"),
        }
        llm_payload: dict[str, Any] = dict(payload)
        if outcome.pause is not None and self.pause is None:
            self.pause = outcome.pause
            payload["pause"] = {
                "reason": outcome.pause.reason,
                "payload": dict(outcome.pause.payload),
            }
            llm_payload["pause"] = payload["pause"]
        elif outcome.observation is not None:
            obs_json = dict(outcome.observation)
            payload["observation"] = obs_json
            self.success_payloads.append(obs_json)
            llm_payload["observation"] = dict(
                outcome.llm_observation or obs_json
            )
        else:
            error_text = outcome.error or (
                prompts.render_parallel_unknown_failure(spec.name)
            )
            payload["error"] = error_text
            llm_payload["error"] = error_text
            failure_entry: dict[str, Any] = {
                "node": spec.name,
                "error": error_text,
            }
            if outcome.failure is not None:
                payload["failure"] = dict(outcome.failure)
                failure_entry["failure"] = dict(outcome.failure)
            self.failure_entries.append(failure_entry)
        self.branch_payloads.append(payload)
        self.llm_branch_payloads.append(llm_payload)

    def observations(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the step's observation and its LLM-facing variant."""
        stats = {
            "success": len(self.success_payloads),
            "failed": len(self.failure_entries),
        }
        return (
            {"branches": self.branch_payloads, "stats": stats},
            {"branches": self.llm_branch_payloads, "stats": stats},
        )


@dataclass(frozen=True, slots=True)
class _JoinResult:
    """What a parallel plan's join step contributes to the observation."""

    node: str
    spec_found: bool = True
    payload: dict[str, Any] | None = None
    llm_payload: dict[str, Any] | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None


def _record_plan_error(
    trajectory: Trajectory, action: PlannerAction, error: str
) -> None:
    """Record a parallel action that could not run."""
    trajectory.steps.append(TrajectoryStep(action=action, error=error))
    trajectory.summary = None


def _parse_plan(action: PlannerAction) -> list[ParallelCall]:
    """Return the action's valid parallel calls."""
    steps_payload = action.args.get("steps")
    if not isinstance(steps_payload, list):
        return []
    plan: list[ParallelCall] = []
    for item in steps_payload:
        try:
            plan.append(ParallelCall.model_validate(item))
        except ValidationError:
            continue
    return plan


def _parse_join(join_spec_payload: object) -> ParallelJoin | None:
    """Return the action's join spec, if it has a valid one."""
    if not isinstance(join_spec_payload, Mapping):
        return None
    try:
        return ParallelJoin.model_validate(join_spec_payload)
    except ValidationError:
        return None


def _resolve_branch_spec(
    planner: ReactPlanner, trajectory: Trajectory, node: str
) -> NodeSpec | None:
    """Find a branch's tool, activating deferred tools on first use.

    Deferred tools can be activated on first use in the single-action path;
    parallel branches get the same activation before setup validation fails.
    """
    spec = planner.spec_by_name.get(node)
    if spec is None:
        from .react_runtime import _maybe_activate_tool

        if _maybe_activate_tool(planner, node, trajectory):
            spec = planner.spec_by_name.get(node)
    return spec


def _prepare_branches(
    planner: ReactPlanner, trajectory: Trajectory, plan: list[ParallelCall]
) -> tuple[list[_Branch], list[str]]:
    """Validate each branch; return the runnable ones and the errors."""
    validation_errors: list[str] = []
    entries: list[_Branch] = []
    for plan_item in plan:
        spec = _resolve_branch_spec(planner, trajectory, plan_item.node)
        if spec is None:
            validation_errors.append(
                prompts.render_invalid_node(
                    plan_item.node, list(planner.spec_by_name.keys())
                )
            )
            continue
        try:
            parsed_args = spec.args_model.model_validate(plan_item.args or {})
        except ValidationError as exc:
            validation_errors.append(
                prompts.render_validation_error(
                    spec.name,
                    _serialize_validation_errors(exc),
                )
            )
            continue
        entries.append((plan_item, spec, parsed_args))
    return entries, validation_errors


def _inject_implicit_join_args(
    template: dict[str, Any],
    join_spec: NodeSpec,
    branches: _BranchResults,
    *,
    expect: int,
) -> None:
    """Fill the join args its model declares from the branch results."""
    defaults: dict[str, Any] = {
        "expect": expect,
        "results": list(branches.success_payloads),
        "branches": list(branches.branch_payloads),
        "failures": [],
        "success_count": len(branches.success_payloads),
        "failure_count": len(branches.failure_entries),
    }
    join_fields = join_spec.args_model.model_fields
    for name, value in defaults.items():
        if name in join_fields and name not in template:
            template[name] = value


def _join_args_template(
    join_model: ParallelJoin,
    join_spec: NodeSpec,
    branches: _BranchResults,
    *,
    expect: int,
) -> tuple[dict[str, Any], str | None, bool]:
    """Build the join args; return them, an error and the implicit flag."""
    template = dict(join_model.args or {})
    injection_mapping = join_model.inject.mapping if join_model.inject else {}
    if injection_mapping:
        injection_sources = {
            "$results": list(branches.success_payloads),
            "$expect": expect,
            "$branches": list(branches.branch_payloads),
            "$failures": list(branches.failure_entries),
            "$success_count": len(branches.success_payloads),
            "$failure_count": len(branches.failure_entries),
        }
        for target, source in injection_mapping.items():
            if source not in injection_sources:
                error = prompts.render_invalid_join_injection_source(
                    source, sorted(injection_sources)
                )
                return template, error, False
            template[target] = injection_sources[source]
        return template, None, False
    if join_model.inject is not None:
        return template, None, False
    logger.warning(
        "Implicit join injection is deprecated. Use "
        "explicit 'inject' mapping for join tool '%s'.",
        join_spec.name,
    )
    _inject_implicit_join_args(template, join_spec, branches, expect=expect)
    return template, None, True


def _join_result(name: str, outcome: ToolCallOutcome) -> _JoinResult:
    """Shape the join tool call's outcome for the observation."""
    if outcome.pause is not None:
        return _JoinResult(
            node=name,
            payload={
                "node": name,
                "pause": {
                    "reason": outcome.pause.reason,
                    "payload": dict(outcome.pause.payload),
                },
            },
            pause=outcome.pause,
        )
    if outcome.error is not None:
        return _JoinResult(
            node=name, error=outcome.error, failure=outcome.failure
        )
    if outcome.observation is None:
        return _JoinResult(node=name)
    join_json = dict(outcome.observation)
    return _JoinResult(
        node=name,
        payload={"node": name, "observation": join_json},
        llm_payload={
            "node": name,
            "observation": dict(outcome.llm_observation or join_json),
        },
    )


async def _run_join(
    planner: ReactPlanner,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    *,
    join_model: ParallelJoin,
    branches: _BranchResults,
    expect: int,
    action_seq: int,
    step_index: int,
    artifact_collector: _ArtifactCollector | None,
    source_collector: _SourceCollector | None,
    steering: SteeringInbox | None,
) -> _JoinResult:
    """Run the plan's join tool over the branch results."""
    join_spec = planner.spec_by_name.get(join_model.node)
    if join_spec is None:
        return _JoinResult(
            node=join_model.node,
            spec_found=False,
            error=prompts.render_invalid_node(
                join_model.node, list(planner.spec_by_name.keys())
            ),
        )
    if branches.failure_entries:
        return _JoinResult(
            node=join_spec.name,
            payload={
                "node": join_spec.name,
                "status": "skipped",
                "reason": "branch_failures",
                "failures": list(branches.failure_entries),
            },
        )
    template, error, implicit = _join_args_template(
        join_model, join_spec, branches, expect=expect
    )
    if error is not None:
        return _JoinResult(node=join_spec.name, error=error)
    try:
        join_args = join_spec.args_model.model_validate(template)
    except ValidationError as exc:
        return _JoinResult(
            node=join_spec.name,
            error=prompts.render_join_validation_error(
                join_spec.name,
                _serialize_validation_errors(exc),
                suggest_inject=implicit,
            ),
        )
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
            "parallel_results": branches.branch_payloads,
            "parallel_success_count": len(branches.success_payloads),
            "parallel_failure_count": len(branches.failure_entries),
            # Branch failures skip the join, so there are none to pass.
            "parallel_failures": None,
            "parallel_input": dict(template),
        },
        artifact_metadata_extra={"parallel_join": True},
        steering=steering,
    )
    tracker.record_hop()
    return _join_result(join_spec.name, join_outcome)


def _attach_join(
    observation: dict[str, Any],
    llm_observation: dict[str, Any],
    join: _JoinResult,
) -> None:
    """Add the join's payload, or its error, to the observations."""
    if join.payload is not None:
        observation["join"] = join.payload
        llm_observation["join"] = join.llm_payload or join.payload
        return
    join_entry: dict[str, Any] = {"node": join.node}
    if join.error is not None:
        join_entry["error"] = join.error
    if join.failure is not None:
        join_entry["failure"] = dict(join.failure)
    if "error" in join_entry or "failure" in join_entry or not join.spec_found:
        observation["join"] = join_entry
        llm_observation["join"] = join_entry


async def execute_parallel_plan(
    planner: ReactPlanner,
    action: PlannerAction,
    trajectory: Trajectory,
    tracker: _ConstraintTracker,
    *,
    action_seq: int,
    artifact_collector: _ArtifactCollector | None = None,
    source_collector: _SourceCollector | None = None,
) -> tuple[dict[str, Any] | None, PlannerPause | None]:
    """Run a parallel plan's branches concurrently, then its join step.

    Returns:
        The step's observation (``None`` when the plan could not run) and
        the pause, if a branch or the join paused.
    """
    if action.next_node != "parallel":
        _record_plan_error(
            trajectory,
            action,
            prompts.render_parallel_with_next_node(action.next_node),
        )
        return None, None
    plan = _parse_plan(action)
    if not plan:
        _record_plan_error(
            trajectory, action, prompts.render_empty_parallel_plan()
        )
        return None, None
    entries, validation_errors = _prepare_branches(planner, trajectory, plan)
    if validation_errors:
        _record_plan_error(
            trajectory,
            action,
            prompts.render_parallel_setup_error(validation_errors),
        )
        return None, None
    join_model = _parse_join(action.args.get("join"))

    # Execute branches concurrently, emitting tool_call_* events from the
    # shared tool executor.
    step_index = len(trajectory.steps)
    steering: SteeringInbox | None = getattr(planner, "steering", None)
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
    branches = _BranchResults()
    for (_, spec, parsed_args), outcome in zip(entries, results, strict=False):
        tracker.record_hop()
        branches.add(spec, parsed_args, outcome)
    observation, llm_observation = branches.observations()

    if branches.pause is not None:
        observation["join"] = {"status": "skipped", "reason": "pause"}
        llm_observation["join"] = observation["join"]
        trajectory.steps.append(
            TrajectoryStep(
                action=action,
                observation=observation,
                llm_observation=llm_observation,
            )
        )
        trajectory.summary = None
        await planner.record_pause(branches.pause, trajectory, tracker)
        return observation, branches.pause

    if join_model is not None:
        join = await _run_join(
            planner,
            trajectory,
            tracker,
            join_model=join_model,
            branches=branches,
            expect=len(entries),
            action_seq=action_seq,
            step_index=step_index,
            artifact_collector=artifact_collector,
            source_collector=source_collector,
            steering=steering,
        )
        if join.pause is not None:
            observation["join"] = join.payload
            trajectory.steps.append(
                TrajectoryStep(action=action, observation=observation)
            )
            trajectory.summary = None
            await planner.record_pause(join.pause, trajectory, tracker)
            return observation, join.pause
        _attach_join(observation, llm_observation, join)

    trajectory.steps.append(
        TrajectoryStep(
            action=action,
            observation=observation,
            llm_observation=llm_observation,
        )
    )
    trajectory.summary = None
    return observation, None


__all__ = ["_BranchExecutionResult", "execute_parallel_plan"]
