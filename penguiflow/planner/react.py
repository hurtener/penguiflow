"""JSON-only ReAct planner loop with pause/resume and summarisation."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from ..catalog import NodeSpec, build_catalog
from ..node import Node
from ..registry import ModelRegistry
from . import prompts

# Planner-specific logger
logger = logging.getLogger("penguiflow.planner")


@dataclass(frozen=True, slots=True)
class PlannerEvent:
    """Structured event emitted during planner execution for observability."""

    event_type: str  # step_start, step_complete, llm_call, pause, resume, finish
    ts: float
    trajectory_step: int
    thought: str | None = None
    node_name: str | None = None
    latency_ms: float | None = None
    token_estimate: int | None = None
    error: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Render a dictionary payload suitable for structured logging."""
        payload: dict[str, Any] = {
            "event": self.event_type,
            "ts": self.ts,
            "step": self.trajectory_step,
        }
        if self.thought is not None:
            payload["thought"] = self.thought
        if self.node_name is not None:
            payload["node_name"] = self.node_name
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        if self.token_estimate is not None:
            payload["token_estimate"] = self.token_estimate
        if self.error is not None:
            payload["error"] = self.error
        if self.extra:
            payload.update(self.extra)
        return payload


# Observability callback type
PlannerEventCallback = Callable[[PlannerEvent], None]


class JSONLLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        ...


class ParallelCall(BaseModel):
    node: str
    args: dict[str, Any] = Field(default_factory=dict)


class ParallelJoin(BaseModel):
    node: str
    args: dict[str, Any] = Field(default_factory=dict)


class PlannerAction(BaseModel):
    thought: str
    next_node: str | None = None
    args: dict[str, Any] | None = None
    plan: list[ParallelCall] | None = None
    join: ParallelJoin | None = None


PlannerPauseReason = Literal[
    "approval_required",
    "await_input",
    "external_event",
    "constraints_conflict",
]


class PlannerPause(BaseModel):
    reason: PlannerPauseReason
    payload: dict[str, Any] = Field(default_factory=dict)
    resume_token: str


class PlannerFinish(BaseModel):
    reason: Literal["answer_complete", "no_path", "budget_exhausted"]
    payload: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrajectorySummary(BaseModel):
    goals: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    pending: list[str] = Field(default_factory=list)
    last_output_digest: str | None = None
    note: str | None = None

    def compact(self) -> dict[str, Any]:
        payload = {
            "goals": list(self.goals),
            "facts": dict(self.facts),
            "pending": list(self.pending),
            "last_output_digest": self.last_output_digest,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(slots=True)
class TrajectoryStep:
    action: PlannerAction
    observation: Any | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None

    def dump(self) -> dict[str, Any]:
        return {
            "action": self.action.model_dump(mode="json"),
            "observation": self._serialise_observation(),
            "error": self.error,
            "failure": dict(self.failure) if self.failure else None,
        }

    def _serialise_observation(self) -> Any:
        if isinstance(self.observation, BaseModel):
            return self.observation.model_dump(mode="json")
        return self.observation


@dataclass(slots=True)
class Trajectory:
    query: str
    context_meta: Mapping[str, Any] | None = None
    steps: list[TrajectoryStep] = field(default_factory=list)
    summary: TrajectorySummary | None = None
    hint_state: dict[str, Any] = field(default_factory=dict)
    resume_user_input: str | None = None

    def to_history(self) -> list[dict[str, Any]]:
        return [step.dump() for step in self.steps]

    def serialise(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "context_meta": dict(self.context_meta or {}),
            "steps": self.to_history(),
            "summary": self.summary.model_dump(mode="json")
            if self.summary
            else None,
            "hint_state": dict(self.hint_state),
            "resume_user_input": self.resume_user_input,
        }

    @classmethod
    def from_serialised(cls, payload: Mapping[str, Any]) -> Trajectory:
        trajectory = cls(
            query=payload["query"],
            context_meta=payload.get("context_meta"),
        )
        for step_data in payload.get("steps", []):
            action = PlannerAction.model_validate(step_data["action"])
            step = TrajectoryStep(
                action=action,
                observation=step_data.get("observation"),
                error=step_data.get("error"),
                failure=step_data.get("failure"),
            )
            trajectory.steps.append(step)
        summary_data = payload.get("summary")
        if summary_data:
            trajectory.summary = TrajectorySummary.model_validate(summary_data)
        trajectory.hint_state.update(payload.get("hint_state", {}))
        trajectory.resume_user_input = payload.get("resume_user_input")
        return trajectory

    def compress(self) -> TrajectorySummary:
        facts: dict[str, Any] = {}
        pending: list[str] = []
        last_observation = None
        if self.steps:
            last_step = self.steps[-1]
            if last_step.observation is not None:
                last_observation = last_step._serialise_observation()
                facts["last_observation"] = last_observation
            if last_step.error:
                facts["last_error"] = last_step.error
        for step in self.steps:
            if step.error:
                pending.append(
                    f"retry {step.action.next_node or 'finish'}"
                )
        digest = None
        if last_observation is not None:
            digest_raw = json.dumps(last_observation, ensure_ascii=False)
            digest = digest_raw if len(digest_raw) <= 120 else f"{digest_raw[:117]}..."
        summary = TrajectorySummary(
            goals=[self.query],
            facts=facts,
            pending=pending,
            last_output_digest=digest,
            note="rule_based",
        )
        self.summary = summary
        return summary


@dataclass(slots=True)
class _PauseRecord:
    trajectory: Trajectory
    reason: str
    payload: dict[str, Any]
    constraints: dict[str, Any] | None = None


@dataclass(slots=True)
class _PlanningHints:
    ordering_hints: tuple[str, ...]
    parallel_groups: tuple[tuple[str, ...], ...]
    sequential_only: set[str]
    disallow_nodes: set[str]
    prefer_nodes: tuple[str, ...]
    max_parallel: int | None
    budget_hints: dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> _PlanningHints:
        if not payload:
            return cls((), (), set(), set(), (), None, {})
        ordering = tuple(str(item) for item in payload.get("ordering_hints", ()))
        parallel_groups = tuple(
            tuple(str(node) for node in group)
            for group in payload.get("parallel_groups", ())
        )
        sequential = {str(item) for item in payload.get("sequential_only", ())}
        disallow = {str(item) for item in payload.get("disallow_nodes", ())}
        prefer = tuple(str(item) for item in payload.get("prefer_nodes", ()))
        budget_raw = dict(payload.get("budget_hints", {}))
        max_parallel_value = payload.get("max_parallel")
        if not isinstance(max_parallel_value, int):
            candidate = budget_raw.get("max_parallel")
            max_parallel_value = candidate if isinstance(candidate, int) else None
        return cls(
            ordering_hints=ordering,
            parallel_groups=parallel_groups,
            sequential_only=sequential,
            disallow_nodes=disallow,
            prefer_nodes=prefer,
            max_parallel=max_parallel_value,
            budget_hints=budget_raw,
        )

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        constraints: list[str] = []
        if self.max_parallel is not None:
            constraints.append(f"max_parallel={self.max_parallel}")
        if self.sequential_only:
            constraints.append(
                "sequential_only=" + ",".join(sorted(self.sequential_only))
            )
        if constraints:
            payload["constraints"] = "; ".join(constraints)
        if self.ordering_hints:
            payload["preferred_order"] = list(self.ordering_hints)
        if self.parallel_groups:
            payload["parallel_groups"] = [list(group) for group in self.parallel_groups]
        if self.disallow_nodes:
            payload["disallow_nodes"] = sorted(self.disallow_nodes)
        if self.prefer_nodes:
            payload["preferred_nodes"] = list(self.prefer_nodes)
        if self.budget_hints:
            payload["budget"] = dict(self.budget_hints)
        return payload

    def empty(self) -> bool:
        return not (
            self.ordering_hints
            or self.parallel_groups
            or self.sequential_only
            or self.disallow_nodes
            or self.prefer_nodes
            or self.max_parallel is not None
            or self.budget_hints
        )


class _ConstraintTracker:
    __slots__ = (
        "_deadline_at",
        "_hop_budget",
        "_hops_used",
        "_time_source",
        "deadline_triggered",
        "hop_exhausted",
    )

    def __init__(
        self,
        *,
        deadline_s: float | None,
        hop_budget: int | None,
        time_source: Callable[[], float],
    ) -> None:
        now = time_source()
        self._deadline_at = now + deadline_s if deadline_s is not None else None
        self._hop_budget = hop_budget
        self._hops_used = 0
        self._time_source = time_source
        self.deadline_triggered = False
        self.hop_exhausted = hop_budget == 0 and hop_budget is not None

    def check_deadline(self) -> str | None:
        if self._deadline_at is None:
            return None
        if self._time_source() >= self._deadline_at:
            self.deadline_triggered = True
            return prompts.render_deadline_exhausted()
        return None

    def has_budget_for_next_tool(self) -> bool:
        if self._hop_budget is None:
            return True
        return self._hops_used < self._hop_budget

    def record_hop(self) -> None:
        if self._hop_budget is None:
            return
        self._hops_used += 1
        if self._hops_used >= self._hop_budget:
            self.hop_exhausted = True

    def snapshot(self) -> dict[str, Any]:
        remaining: float | None = None
        if self._deadline_at is not None:
            remaining = max(self._deadline_at - self._time_source(), 0.0)
        return {
            "deadline_at": self._deadline_at,
            "deadline_remaining_s": remaining,
            "hop_budget": self._hop_budget,
            "hops_used": self._hops_used,
            "deadline_triggered": self.deadline_triggered,
            "hop_exhausted": self.hop_exhausted,
        }

    @classmethod
    def from_snapshot(
        cls, snapshot: Mapping[str, Any], *, time_source: Callable[[], float]
    ) -> _ConstraintTracker:
        deadline_remaining = snapshot.get("deadline_remaining_s")
        hop_budget = snapshot.get("hop_budget")
        tracker = cls(
            deadline_s=deadline_remaining,
            hop_budget=hop_budget,
            time_source=time_source,
        )
        tracker._hops_used = int(snapshot.get("hops_used", 0))
        tracker._hop_budget = hop_budget
        if deadline_remaining is None and snapshot.get("deadline_at") is None:
            tracker._deadline_at = None
        elif deadline_remaining is not None:
            tracker._deadline_at = time_source() + max(float(deadline_remaining), 0.0)
        else:
            tracker._deadline_at = snapshot.get("deadline_at")
        tracker.deadline_triggered = bool(snapshot.get("deadline_triggered", False))
        tracker.hop_exhausted = bool(snapshot.get("hop_exhausted", False))
        if (
            tracker._hop_budget is not None
            and tracker._hops_used >= tracker._hop_budget
        ):
            tracker.hop_exhausted = True
        return tracker


class _PlannerPauseSignal(Exception):
    def __init__(self, pause: PlannerPause) -> None:
        super().__init__(pause.reason)
        self.pause = pause


@dataclass(slots=True)
class _BranchExecutionResult:
    observation: BaseModel | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    pause: PlannerPause | None = None


class _LiteLLMJSONClient:
    def __init__(
        self,
        llm: str | Mapping[str, Any],
        *,
        temperature: float,
        json_schema_mode: bool,
        max_retries: int = 3,
        timeout_s: float = 60.0,
    ) -> None:
        self._llm = llm
        self._temperature = temperature
        self._json_schema_mode = json_schema_mode
        self._max_retries = max_retries
        self._timeout_s = timeout_s

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        try:
            import litellm
        except ModuleNotFoundError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "LiteLLM is not installed. Install penguiflow[planner] or provide "
                "a custom llm_client."
            ) from exc

        params: dict[str, Any]
        if isinstance(self._llm, str):
            params = {"model": self._llm}
        else:
            params = dict(self._llm)
        params.setdefault("temperature", self._temperature)
        params["messages"] = list(messages)
        if self._json_schema_mode and response_format is not None:
            params["response_format"] = response_format

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                # Add timeout protection
                async with asyncio.timeout(self._timeout_s):
                    response = await litellm.acompletion(**params)
                    choice = response["choices"][0]
                    content = choice["message"]["content"]
                    if content is None:
                        raise RuntimeError("LiteLLM returned empty content")

                    # Log successful LLM call with cost if available
                    cost = response.get("_hidden_params", {}).get("response_cost", 0)
                    if cost and cost > 0:
                        logger.debug(
                            "llm_call_success",
                            extra={"attempt": attempt + 1, "cost_usd": cost},
                        )

                    return content
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "llm_timeout",
                    extra={"attempt": attempt + 1, "timeout_s": self._timeout_s},
                )
            except Exception as exc:
                last_error = exc
                # Check if it's a retryable error (network, rate limit, etc.)
                error_type = exc.__class__.__name__
                if "RateLimit" in error_type or "ServiceUnavailable" in error_type:
                    backoff_s = 2 ** attempt
                    logger.warning(
                        "llm_retry",
                        extra={
                            "attempt": attempt + 1,
                            "error": str(exc),
                            "backoff_s": backoff_s,
                        },
                    )
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(backoff_s)
                        continue
                # Non-retryable error, raise immediately
                raise

        # All retries exhausted
        logger.error(
            "llm_retries_exhausted",
            extra={"max_retries": self._max_retries, "last_error": str(last_error)},
        )
        msg = f"LLM call failed after {self._max_retries} retries"
        raise RuntimeError(msg) from last_error


class _PlannerContext:
    __slots__ = ("meta", "_planner", "_trajectory")

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self.meta = dict(trajectory.context_meta or {})
        self._planner = planner
        self._trajectory = trajectory

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> PlannerPause:
        return await self._planner._pause_from_context(
            reason,
            dict(payload or {}),
            self._trajectory,
        )


class ReactPlanner:
    """JSON-only ReAct planner for autonomous multi-step workflows.

    The ReactPlanner orchestrates a loop where an LLM selects and sequences
    PenguiFlow nodes/tools based on structured JSON contracts. It supports
    pause/resume for approvals, adaptive re-planning on failures, parallel
    execution, and trajectory compression for long-running sessions.

    Thread Safety
    -------------
    NOT thread-safe. Create separate planner instances per task.

    Parameters
    ----------
    llm : str | Mapping[str, Any] | None
        LiteLLM model name (e.g., "gpt-4") or config dict. Required if
        llm_client is not provided.
    nodes : Sequence[Node] | None
        Sequence of PenguiFlow nodes to make available as tools. Either
        (nodes + registry) or catalog must be provided.
    catalog : Sequence[NodeSpec] | None
        Pre-built tool catalog. If provided, nodes and registry are ignored.
    registry : ModelRegistry | None
        Model registry for type resolution. Required if nodes is provided.
    llm_client : JSONLLMClient | None
        Custom LLM client implementation. If provided, llm is ignored.
    max_iters : int
        Maximum planning iterations before returning no_path. Default: 8.
    temperature : float
        LLM sampling temperature. Default: 0.0 for deterministic output.
    json_schema_mode : bool
        Enable strict JSON schema enforcement via LLM response_format.
        Default: True.
    system_prompt_extra : str | None
        Additional guidance appended to system prompt.
    token_budget : int | None
        If set, triggers trajectory summarization when history exceeds limit.
        Token count is estimated by character length (approx).
    pause_enabled : bool
        Allow nodes to trigger pause/resume flow. Default: True.
    state_store : StateStore | None
        Optional durable state adapter for pause/resume persistence.
    summarizer_llm : str | Mapping[str, Any] | None
        Separate (cheaper) LLM for trajectory compression. Falls back to
        main LLM if not set.
    planning_hints : Mapping[str, Any] | None
        Structured constraints and preferences (ordering, disallowed nodes,
        max_parallel, etc.). See plan.md for schema.
    repair_attempts : int
        Max attempts to repair invalid JSON from LLM. Default: 3.
    deadline_s : float | None
        Wall-clock deadline for planning session (seconds from start).
    hop_budget : int | None
        Maximum tool invocations allowed.
    time_source : Callable[[], float] | None
        Override time.monotonic for testing.
    event_callback : PlannerEventCallback | None
        Optional callback receiving PlannerEvent instances for observability.
    llm_timeout_s : float
        Per-LLM-call timeout in seconds. Default: 60.0.
    llm_max_retries : int
        Max retry attempts for transient LLM failures. Default: 3.
    absolute_max_parallel : int
        System-level safety limit on parallel execution regardless of hints.
        Default: 50.

    Raises
    ------
    ValueError
        If neither (nodes + registry) nor catalog is provided, or if neither
        llm nor llm_client is provided.
    RuntimeError
        If LiteLLM is not installed and llm_client is not provided.

    Examples
    --------
    >>> planner = ReactPlanner(
    ...     llm="gpt-4",
    ...     nodes=[triage_node, retrieve_node, summarize_node],
    ...     registry=my_registry,
    ...     max_iters=10,
    ... )
    >>> result = await planner.run("Explain PenguiFlow's architecture")
    >>> print(result.reason)  # "answer_complete", "no_path", or "budget_exhausted"
    """

    # Default system-level safety limit for parallel execution
    DEFAULT_MAX_PARALLEL = 50

    def __init__(
        self,
        llm: str | Mapping[str, Any] | None = None,
        *,
        nodes: Sequence[Node] | None = None,
        catalog: Sequence[NodeSpec] | None = None,
        registry: ModelRegistry | None = None,
        llm_client: JSONLLMClient | None = None,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        system_prompt_extra: str | None = None,
        token_budget: int | None = None,
        pause_enabled: bool = True,
        state_store: Any | None = None,
        summarizer_llm: str | Mapping[str, Any] | None = None,
        planning_hints: Mapping[str, Any] | None = None,
        repair_attempts: int = 3,
        deadline_s: float | None = None,
        hop_budget: int | None = None,
        time_source: Callable[[], float] | None = None,
        event_callback: PlannerEventCallback | None = None,
        llm_timeout_s: float = 60.0,
        llm_max_retries: int = 3,
        absolute_max_parallel: int = 50,
    ) -> None:
        if catalog is None:
            if nodes is None or registry is None:
                raise ValueError(
                    "Either catalog or (nodes and registry) must be provided"
                )
            catalog = build_catalog(nodes, registry)

        self._specs = list(catalog)
        self._spec_by_name = {spec.name: spec for spec in self._specs}
        self._catalog_records = [spec.to_tool_record() for spec in self._specs]
        self._planning_hints = _PlanningHints.from_mapping(planning_hints)
        hints_payload = (
            self._planning_hints.to_prompt_payload()
            if not self._planning_hints.empty()
            else None
        )
        self._system_prompt = prompts.build_system_prompt(
            self._catalog_records,
            extra=system_prompt_extra,
            planning_hints=hints_payload,
        )
        self._max_iters = max_iters
        self._repair_attempts = repair_attempts
        self._json_schema_mode = json_schema_mode
        self._token_budget = token_budget
        self._pause_enabled = pause_enabled
        self._state_store = state_store
        self._pause_records: dict[str, _PauseRecord] = {}
        self._active_trajectory: Trajectory | None = None
        self._active_tracker: _ConstraintTracker | None = None
        self._deadline_s = deadline_s
        self._hop_budget = hop_budget
        self._time_source = time_source or time.monotonic
        self._event_callback = event_callback
        self._absolute_max_parallel = absolute_max_parallel
        action_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "planner_action",
                "schema": PlannerAction.model_json_schema(),
            },
        }
        self._action_schema: Mapping[str, Any] = action_schema
        self._response_format = action_schema if json_schema_mode else None
        self._summarizer_client: JSONLLMClient | None = None
        if llm_client is not None:
            self._client = llm_client
        else:
            if llm is None:
                raise ValueError("llm or llm_client must be provided")
            self._client = _LiteLLMJSONClient(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )
        if summarizer_llm is not None:
            self._summarizer_client = _LiteLLMJSONClient(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

    async def run(
        self,
        query: str,
        *,
        context_meta: Mapping[str, Any] | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Execute planner on a query until completion or pause.

        Parameters
        ----------
        query : str
            Natural language task description.
        context_meta : Mapping[str, Any] | None
            Optional metadata passed to nodes via ctx.meta.

        Returns
        -------
        PlannerFinish | PlannerPause
            PlannerFinish if task completed/failed, PlannerPause if paused
            for human intervention.

        Raises
        ------
        RuntimeError
            If LLM client fails after all retries.
        """
        logger.info("planner_run_start", extra={"query": query})
        trajectory = Trajectory(query=query, context_meta=context_meta)
        return await self._run_loop(trajectory, tracker=None)

    async def resume(
        self,
        token: str,
        user_input: str | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Resume a paused planning session.

        Parameters
        ----------
        token : str
            Resume token from a previous PlannerPause.
        user_input : str | None
            Optional user response to the pause (e.g., approval decision).

        Returns
        -------
        PlannerFinish | PlannerPause
            Updated result after resuming execution.

        Raises
        ------
        KeyError
            If resume token is invalid or expired.
        """
        logger.info("planner_resume", extra={"token": token[:8] + "..."})
        record = await self._load_pause_record(token)
        trajectory = record.trajectory
        trajectory.context_meta = trajectory.context_meta or {}
        if user_input is not None:
            trajectory.resume_user_input = user_input
        tracker: _ConstraintTracker | None = None
        if record.constraints is not None:
            tracker = _ConstraintTracker.from_snapshot(
                record.constraints,
                time_source=self._time_source,
            )

        # Emit resume event
        self._emit_event(
            PlannerEvent(
                event_type="resume",
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={"user_input": user_input} if user_input else {},
            )
        )

        return await self._run_loop(trajectory, tracker=tracker)

    async def _run_loop(
        self,
        trajectory: Trajectory,
        *,
        tracker: _ConstraintTracker | None,
    ) -> PlannerFinish | PlannerPause:
        last_observation: Any | None = None
        self._active_trajectory = trajectory
        if tracker is None:
            tracker = _ConstraintTracker(
                deadline_s=self._deadline_s,
                hop_budget=self._hop_budget,
                time_source=self._time_source,
            )
        self._active_tracker = tracker
        try:
            while len(trajectory.steps) < self._max_iters:
                deadline_message = tracker.check_deadline()
                if deadline_message is not None:
                    logger.warning(
                        "deadline_exhausted",
                        extra={"step": len(trajectory.steps)},
                    )
                    return self._finish(
                        trajectory,
                        reason="budget_exhausted",
                        payload=last_observation,
                        thought=deadline_message,
                        constraints=tracker,
                    )

                # Emit step start event
                step_start_ts = self._time_source()
                self._emit_event(
                    PlannerEvent(
                        event_type="step_start",
                        ts=step_start_ts,
                        trajectory_step=len(trajectory.steps),
                    )
                )

                action = await self.step(trajectory)

                # Log the action received from LLM
                logger.info(
                    "planner_action",
                    extra={
                        "step": len(trajectory.steps),
                        "thought": action.thought,
                        "next_node": action.next_node,
                        "has_plan": action.plan is not None,
                    },
                )

                # Check constraints BEFORE executing parallel plan or any action
                constraint_error = self._check_action_constraints(
                    action, trajectory, tracker
                )
                if constraint_error is not None:
                    trajectory.steps.append(
                        TrajectoryStep(action=action, error=constraint_error)
                    )
                    trajectory.summary = None
                    continue

                if action.plan:
                    parallel_observation, pause = await self._execute_parallel_plan(
                        action, trajectory, tracker
                    )
                    if pause is not None:
                        return pause
                    trajectory.summary = None
                    last_observation = parallel_observation
                    trajectory.resume_user_input = None
                    continue

                if action.next_node is None:
                    payload = action.args or last_observation
                    return self._finish(
                        trajectory,
                        reason="answer_complete",
                        payload=payload,
                        thought=action.thought,
                        constraints=tracker,
                    )

                spec = self._spec_by_name.get(action.next_node)
                if spec is None:
                    error = prompts.render_invalid_node(
                        action.next_node,
                        list(self._spec_by_name.keys()),
                    )
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

                try:
                    parsed_args = spec.args_model.model_validate(action.args or {})
                except ValidationError as exc:
                    error = prompts.render_validation_error(
                        spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                    )
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

                ctx = _PlannerContext(self, trajectory)
                try:
                    result = await spec.node.func(parsed_args, ctx)
                except _PlannerPauseSignal as signal:
                    tracker.record_hop()
                    trajectory.steps.append(
                        TrajectoryStep(
                            action=action,
                            observation={
                                "pause": signal.pause.reason,
                                "payload": signal.pause.payload,
                            },
                        )
                    )
                    trajectory.summary = None
                    await self._record_pause(signal.pause, trajectory, tracker)
                    return signal.pause
                except Exception as exc:
                    failure_payload = self._build_failure_payload(
                        spec, parsed_args, exc
                    )
                    error = (
                        f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
                    )
                    trajectory.steps.append(
                        TrajectoryStep(
                            action=action,
                            error=error,
                            failure=failure_payload,
                        )
                    )
                    tracker.record_hop()
                    trajectory.summary = None
                    last_observation = None
                    continue

                try:
                    observation = spec.out_model.model_validate(result)
                except ValidationError as exc:
                    error = prompts.render_output_validation_error(
                        spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                    )
                    tracker.record_hop()
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    last_observation = None
                    continue

                trajectory.steps.append(
                    TrajectoryStep(action=action, observation=observation)
                )
                tracker.record_hop()
                trajectory.summary = None
                last_observation = observation.model_dump(mode="json")
                self._record_hint_progress(spec.name, trajectory)
                trajectory.resume_user_input = None

                # Emit step complete event
                step_latency = (self._time_source() - step_start_ts) * 1000  # ms
                self._emit_event(
                    PlannerEvent(
                        event_type="step_complete",
                        ts=self._time_source(),
                        trajectory_step=len(trajectory.steps) - 1,
                        thought=action.thought,
                        node_name=spec.name,
                        latency_ms=step_latency,
                    )
                )

            if tracker.deadline_triggered or tracker.hop_exhausted:
                thought = (
                    prompts.render_deadline_exhausted()
                    if tracker.deadline_triggered
                    else prompts.render_hop_budget_violation(self._hop_budget or 0)
                )
                return self._finish(
                    trajectory,
                    reason="budget_exhausted",
                    payload=last_observation,
                    thought=thought,
                    constraints=tracker,
                )
            return self._finish(
                trajectory,
                reason="no_path",
                payload=last_observation,
                thought="iteration limit reached",
                constraints=tracker,
            )
        finally:
            self._active_trajectory = None
            self._active_tracker = None

    async def step(self, trajectory: Trajectory) -> PlannerAction:
        base_messages = await self._build_messages(trajectory)
        messages: list[dict[str, str]] = list(base_messages)
        last_error: str | None = None

        for _ in range(self._repair_attempts):
            if last_error is not None:
                messages = list(base_messages) + [
                    {
                        "role": "system",
                        "content": prompts.render_repair_message(last_error),
                    }
                ]

            response_format: Mapping[str, Any] | None = self._response_format
            if response_format is None and getattr(
                self._client, "expects_json_schema", False
            ):
                response_format = self._action_schema

            raw = await self._client.complete(
                messages=messages,
                response_format=response_format,
            )

            try:
                return PlannerAction.model_validate_json(raw)
            except ValidationError as exc:
                last_error = json.dumps(exc.errors(), ensure_ascii=False)
                continue

        raise RuntimeError("Planner failed to produce valid JSON after repair attempts")

    async def _execute_parallel_plan(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
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
        entries: list[tuple[ParallelCall, NodeSpec, BaseModel]] = []
        for plan_item in action.plan:
            spec = self._spec_by_name.get(plan_item.node)
            if spec is None:
                validation_errors.append(
                    prompts.render_invalid_node(
                        plan_item.node, list(self._spec_by_name.keys())
                    )
                )
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

        ctx = _PlannerContext(self, trajectory)
        results = await asyncio.gather(
            *(
                self._run_parallel_branch(spec, parsed_args, ctx)
                for (_, spec, parsed_args) in entries
            )
        )

        branch_payloads: list[dict[str, Any]] = []
        success_payloads: list[Any] = []
        failure_entries: list[dict[str, Any]] = []
        pause_result: PlannerPause | None = None

        for (_, spec, parsed_args), outcome in zip(
            entries, results, strict=False
        ):
            tracker.record_hop()
            payload: dict[str, Any] = {
                "node": spec.name,
                "args": parsed_args.model_dump(mode="json"),
            }
            if outcome.pause is not None and pause_result is None:
                pause_result = outcome.pause
                payload["pause"] = {
                    "reason": outcome.pause.reason,
                    "payload": dict(outcome.pause.payload),
                }
            elif outcome.observation is not None:
                obs_json = outcome.observation.model_dump(mode="json")
                payload["observation"] = obs_json
                success_payloads.append(obs_json)
                self._record_hint_progress(spec.name, trajectory)
            else:
                error_text = outcome.error or prompts.render_parallel_unknown_failure(
                    spec.name
                )
                payload["error"] = error_text
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
                    failure_entries.append(
                        {"node": spec.name, "error": error_text}
                    )
            branch_payloads.append(payload)

        stats = {"success": len(success_payloads), "failed": len(failure_entries)}
        observation: dict[str, Any] = {
            "branches": branch_payloads,
            "stats": stats,
        }

        if pause_result is not None:
            observation["join"] = {
                "status": "skipped",
                "reason": "pause",
            }
            trajectory.steps.append(
                TrajectoryStep(action=action, observation=observation)
            )
            trajectory.summary = None
            await self._record_pause(pause_result, trajectory, tracker)
            return observation, pause_result

        join_payload: dict[str, Any] | None = None
        join_error: str | None = None
        join_failure: Mapping[str, Any] | None = None
        join_spec: NodeSpec | None = None
        join_args_template: dict[str, Any] | None = None

        if action.join is not None:
            join_spec = self._spec_by_name.get(action.join.node)
            if join_spec is None:
                join_error = prompts.render_invalid_node(
                    action.join.node, list(self._spec_by_name.keys())
                )
            elif failure_entries:
                join_payload = {
                    "node": join_spec.name,
                    "status": "skipped",
                    "reason": "branch_failures",
                    "failures": list(failure_entries),
                }
            else:
                join_args_template = dict(action.join.args or {})
                join_fields = join_spec.args_model.model_fields
                if "expect" in join_fields and "expect" not in join_args_template:
                    join_args_template["expect"] = len(entries)
                if "results" in join_fields and "results" not in join_args_template:
                    join_args_template["results"] = list(success_payloads)
                if "branches" in join_fields and "branches" not in join_args_template:
                    join_args_template["branches"] = list(branch_payloads)
                if "failures" in join_fields and "failures" not in join_args_template:
                    join_args_template["failures"] = []
                if (
                    "success_count" in join_fields
                    and "success_count" not in join_args_template
                ):
                    join_args_template["success_count"] = len(success_payloads)
                if (
                    "failure_count" in join_fields
                    and "failure_count" not in join_args_template
                ):
                    join_args_template["failure_count"] = len(failure_entries)

                try:
                    join_args = join_spec.args_model.model_validate(join_args_template)
                except ValidationError as exc:
                    join_error = prompts.render_validation_error(
                        join_spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                    )
                else:
                    join_ctx = _PlannerContext(self, trajectory)
                    join_ctx.meta.update(
                        {
                            "parallel_results": branch_payloads,
                            "parallel_success_count": len(success_payloads),
                            "parallel_failure_count": len(failure_entries),
                        }
                    )
                    if failure_entries:
                        join_ctx.meta["parallel_failures"] = list(failure_entries)
                    join_ctx.meta["parallel_input"] = dict(join_args_template)

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
                        trajectory.steps.append(
                            TrajectoryStep(action=action, observation=observation)
                        )
                        trajectory.summary = None
                        await self._record_pause(signal.pause, trajectory, tracker)
                        return observation, signal.pause
                    except Exception as exc:
                        tracker.record_hop()
                        join_error = (
                            f"tool '{join_spec.name}' raised "
                            f"{exc.__class__.__name__}: {exc}"
                        )
                        join_failure = self._build_failure_payload(
                            join_spec, join_args, exc
                        )
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
                            self._record_hint_progress(join_spec.name, trajectory)
                            join_payload = {
                                "node": join_spec.name,
                                "observation": join_model.model_dump(mode="json"),
                            }

        if action.join is not None and "join" not in observation:
            if join_payload is not None:
                observation["join"] = join_payload
            else:
                join_name = (
                    join_spec.name
                    if join_spec is not None
                    else action.join.node
                    if action.join is not None
                    else "join"
                )
                join_entry: dict[str, Any] = {"node": join_name}
                if join_error is not None:
                    join_entry["error"] = join_error
                if join_failure is not None:
                    join_entry["failure"] = dict(join_failure)
                if "error" in join_entry or "failure" in join_entry:
                    observation["join"] = join_entry
                elif action.join is not None and join_spec is None:
                    observation["join"] = join_entry

        trajectory.steps.append(
            TrajectoryStep(action=action, observation=observation)
        )
        trajectory.summary = None
        return observation, None

    async def _run_parallel_branch(
        self, spec: NodeSpec, args: BaseModel, ctx: _PlannerContext
    ) -> _BranchExecutionResult:
        try:
            raw = await spec.node.func(args, ctx)
        except _PlannerPauseSignal as signal:
            return _BranchExecutionResult(pause=signal.pause)
        except Exception as exc:
            failure_payload = self._build_failure_payload(spec, args, exc)
            error = (
                f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
            )
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

    async def _build_messages(self, trajectory: Trajectory) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": prompts.build_user_prompt(
                    trajectory.query,
                    trajectory.context_meta,
                ),
            },
        ]

        history_messages: list[dict[str, str]] = []
        for step in trajectory.steps:
            action_payload = json.dumps(
                step.action.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            history_messages.append({"role": "assistant", "content": action_payload})
            history_messages.append(
                {
                    "role": "user",
                    "content": prompts.render_observation(
                        observation=step._serialise_observation(),
                        error=step.error,
                        failure=step.failure,
                    ),
                }
            )

        if trajectory.resume_user_input:
            history_messages.append(
                {
                    "role": "user",
                    "content": prompts.render_resume_user_input(
                        trajectory.resume_user_input
                    ),
                }
            )

        if self._token_budget is None:
            return messages + history_messages

        candidate = messages + history_messages
        if self._estimate_size(candidate) <= self._token_budget:
            return candidate

        summary = await self._summarise_trajectory(trajectory)
        summary_message = {
            "role": "system",
            "content": prompts.render_summary(summary.compact()),
        }
        condensed: list[dict[str, str]] = messages + [summary_message]
        if trajectory.steps:
            last_step = trajectory.steps[-1]
            condensed.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        last_step.action.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
            condensed.append(
                {
                    "role": "user",
                    "content": prompts.render_observation(
                        observation=last_step._serialise_observation(),
                        error=last_step.error,
                        failure=last_step.failure,
                    ),
                }
            )
        if trajectory.resume_user_input:
            condensed.append(
                {
                    "role": "user",
                    "content": prompts.render_resume_user_input(
                        trajectory.resume_user_input
                    ),
                }
            )
        return condensed

    def _estimate_size(self, messages: Sequence[Mapping[str, str]]) -> int:
        """Estimate token count for messages.

        Uses a heuristic formula that accounts for JSON structure and
        typical token-to-character ratios for English text with JSON.

        Returns approximately 4 characters = 1 token for GPT models.
        This is conservative to avoid context overflow.
        """
        total_chars = 0
        for item in messages:
            content = item.get("content", "")
            role = item.get("role", "")
            # Count content characters
            total_chars += len(content)
            # Add overhead for message structure (role, JSON wrapping, etc.)
            total_chars += len(role) + 20  # Approx overhead per message

        # Conservative estimate: 3.5 chars = 1 token (slightly aggressive)
        # This ensures we trigger summarization before hitting actual limits
        estimated_tokens = int(total_chars / 3.5)

        logger.debug(
            "token_estimate",
            extra={"chars": total_chars, "estimated_tokens": estimated_tokens},
        )

        return estimated_tokens

    async def _summarise_trajectory(
        self, trajectory: Trajectory
    ) -> TrajectorySummary:
        if trajectory.summary is not None:
            return trajectory.summary

        base_summary = trajectory.compress()
        summary_text = prompts.render_summary(base_summary.compact())
        if (
            self._summarizer_client is not None
            and self._token_budget is not None
            and len(summary_text) > self._token_budget
        ):
            messages = prompts.build_summarizer_messages(
                trajectory.query,
                trajectory.to_history(),
                base_summary.compact(),
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "trajectory_summary",
                    "schema": TrajectorySummary.model_json_schema(),
                },
            }
            try:
                raw = await self._summarizer_client.complete(
                    messages=messages,
                    response_format=response_format,
                )
                summary = TrajectorySummary.model_validate_json(raw)
                summary.note = summary.note or "llm"
                trajectory.summary = summary
                logger.debug("trajectory_summarized", extra={"method": "llm"})
                return summary
            except (TimeoutError, ValidationError, RuntimeError) as exc:
                # Log specific summarizer failure but fallback gracefully
                logger.warning(
                    "summarizer_failed_fallback",
                    extra={"error": str(exc), "error_type": exc.__class__.__name__},
                )
                base_summary.note = "rule_based_fallback"
        trajectory.summary = base_summary
        logger.debug("trajectory_summarized", extra={"method": "rule_based"})
        return base_summary

    def _check_action_constraints(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
    ) -> str | None:
        hints = self._planning_hints
        node_name = action.next_node
        if node_name and not tracker.has_budget_for_next_tool():
            limit = self._hop_budget if self._hop_budget is not None else 0
            return prompts.render_hop_budget_violation(limit)
        if node_name and node_name in hints.disallow_nodes:
            return prompts.render_disallowed_node(node_name)

        # Check parallel execution limits
        if action.plan:
            # Absolute system-level safety limit
            if len(action.plan) > self._absolute_max_parallel:
                logger.warning(
                    "parallel_limit_absolute",
                    extra={
                        "requested": len(action.plan),
                        "limit": self._absolute_max_parallel,
                    },
                )
                return prompts.render_parallel_limit(self._absolute_max_parallel)
            # Hint-based limit
            if hints.max_parallel is not None and len(action.plan) > hints.max_parallel:
                return prompts.render_parallel_limit(hints.max_parallel)
        if hints.sequential_only and action.plan:
            for item in action.plan:
                candidate = item.node
                if candidate in hints.sequential_only:
                    return prompts.render_sequential_only(candidate)
        if hints.ordering_hints and node_name is not None:
            state = trajectory.hint_state.setdefault(
                "ordering_state",
                {"completed": [], "warned": False},
            )
            completed = state.setdefault("completed", [])
            expected_index = len(completed)
            if expected_index < len(hints.ordering_hints):
                expected_node = hints.ordering_hints[expected_index]
                if node_name != expected_node:
                    if (
                        node_name in hints.ordering_hints
                        and not state.get("warned", False)
                    ):
                        state["warned"] = True
                        return prompts.render_ordering_hint_violation(
                            hints.ordering_hints,
                            node_name,
                        )
        return None

    def _record_hint_progress(self, node_name: str, trajectory: Trajectory) -> None:
        hints = self._planning_hints
        if not hints.ordering_hints:
            return
        state = trajectory.hint_state.setdefault(
            "ordering_state",
            {"completed": [], "warned": False},
        )
        completed = state.setdefault("completed", [])
        expected_index = len(completed)
        if (
            expected_index < len(hints.ordering_hints)
            and node_name == hints.ordering_hints[expected_index]
        ):
            completed.append(node_name)
            state["warned"] = False

    def _build_failure_payload(
        self, spec: NodeSpec, args: BaseModel, exc: Exception
    ) -> dict[str, Any]:
        suggestion = getattr(exc, "suggestion", None)
        if suggestion is None:
            suggestion = getattr(exc, "remedy", None)
        payload: dict[str, Any] = {
            "node": spec.name,
            "args": args.model_dump(mode="json"),
            "error_code": exc.__class__.__name__,
            "message": str(exc),
        }
        if suggestion:
            payload["suggestion"] = str(suggestion)
        return payload

    async def pause(
        self, reason: PlannerPauseReason, payload: Mapping[str, Any] | None = None
    ) -> PlannerPause:
        if self._active_trajectory is None:
            raise RuntimeError("pause() requires an active planner run")
        try:
            await self._pause_from_context(
                reason,
                dict(payload or {}),
                self._active_trajectory,
            )
        except _PlannerPauseSignal as signal:
            return signal.pause
        raise RuntimeError("pause request did not trigger")

    async def _pause_from_context(
        self,
        reason: PlannerPauseReason,
        payload: dict[str, Any],
        trajectory: Trajectory,
    ) -> PlannerPause:
        if not self._pause_enabled:
            raise RuntimeError("Pause/resume is disabled for this planner")
        pause = PlannerPause(
            reason=reason,
            payload=dict(payload),
            resume_token=uuid4().hex,
        )
        await self._record_pause(pause, trajectory, self._active_tracker)
        raise _PlannerPauseSignal(pause)

    async def _record_pause(
        self,
        pause: PlannerPause,
        trajectory: Trajectory,
        tracker: _ConstraintTracker | None,
    ) -> None:
        snapshot = Trajectory.from_serialised(trajectory.serialise())
        record = _PauseRecord(
            trajectory=snapshot,
            reason=pause.reason,
            payload=dict(pause.payload),
            constraints=tracker.snapshot() if tracker is not None else None,
        )
        await self._store_pause_record(pause.resume_token, record)

    async def _store_pause_record(self, token: str, record: _PauseRecord) -> None:
        self._pause_records[token] = record
        if self._state_store is None:
            return
        saver = getattr(self._state_store, "save_planner_state", None)
        if saver is None:
            logger.debug(
                "state_store_no_save_method",
                extra={"token": token[:8] + "..."},
            )
            return

        try:
            payload = self._serialise_pause_record(record)
            result = saver(token, payload)
            if inspect.isawaitable(result):
                await result
            logger.debug("pause_record_saved", extra={"token": token[:8] + "..."})
        except Exception as exc:
            # Log error but don't fail the pause operation
            # In-memory fallback already succeeded
            logger.error(
                "state_store_save_failed",
                extra={
                    "token": token[:8] + "...",
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )

    async def _load_pause_record(self, token: str) -> _PauseRecord:
        record = self._pause_records.pop(token, None)
        if record is not None:
            logger.debug("pause_record_loaded", extra={"source": "memory"})
            return record

        if self._state_store is not None:
            loader = getattr(self._state_store, "load_planner_state", None)
            if loader is not None:
                try:
                    result = loader(token)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is None:
                        raise KeyError(token)
                    trajectory = Trajectory.from_serialised(result["trajectory"])
                    payload = dict(result.get("payload", {}))
                    reason = result.get("reason", "await_input")
                    constraints = result.get("constraints")
                    logger.debug("pause_record_loaded", extra={"source": "state_store"})
                    return _PauseRecord(
                        trajectory=trajectory,
                        reason=reason,
                        payload=payload,
                        constraints=constraints,
                    )
                except KeyError:
                    raise
                except Exception as exc:
                    # Log error and re-raise as KeyError with context
                    logger.error(
                        "state_store_load_failed",
                        extra={
                            "token": token[:8] + "...",
                            "error": str(exc),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    raise KeyError(f"Failed to load pause record: {exc}") from exc

        raise KeyError(token)

    def _serialise_pause_record(self, record: _PauseRecord) -> dict[str, Any]:
        return {
            "trajectory": record.trajectory.serialise(),
            "reason": record.reason,
            "payload": dict(record.payload),
            "constraints": dict(record.constraints)
            if record.constraints is not None
            else None,
        }

    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        # Log the event
        logger.info(event.event_type, extra=event.to_payload())

        # Invoke callback if provided
        if self._event_callback is not None:
            try:
                self._event_callback(event)
            except Exception:
                logger.exception(
                    "event_callback_error",
                    extra={
                        "event_type": event.event_type,
                        "step": event.trajectory_step,
                    },
                )

    def _finish(
        self,
        trajectory: Trajectory,
        *,
        reason: Literal["answer_complete", "no_path", "budget_exhausted"],
        payload: Any,
        thought: str,
        constraints: _ConstraintTracker | None = None,
        error: str | None = None,
    ) -> PlannerFinish:
        metadata = {
            "reason": reason,
            "thought": thought,
            "steps": trajectory.to_history(),
            "step_count": len(trajectory.steps),
        }
        if constraints is not None:
            metadata["constraints"] = constraints.snapshot()
        if error is not None:
            metadata["error"] = error

        # Emit finish event
        extra_data: dict[str, Any] = {"reason": reason}
        if error:
            extra_data["error"] = error
        self._emit_event(
            PlannerEvent(
                event_type="finish",
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                thought=thought,
                extra=extra_data,
            )
        )

        logger.info(
            "planner_finish",
            extra={
                "reason": reason,
                "step_count": len(trajectory.steps),
                "thought": thought,
            },
        )

        return PlannerFinish(reason=reason, payload=payload, metadata=metadata)


__all__ = [
    "ParallelCall",
    "ParallelJoin",
    "PlannerAction",
    "PlannerEvent",
    "PlannerEventCallback",
    "PlannerFinish",
    "PlannerPause",
    "ReactPlanner",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
]
