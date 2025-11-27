"""JSON-only ReAct planner loop with pause/resume and summarisation."""

from __future__ import annotations

import inspect
import json
import logging
import time
import warnings
from collections import ChainMap, defaultdict
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..catalog import NodeSpec, build_catalog
from ..node import Node
from ..registry import ModelRegistry
from . import prompts
from .constraints import _ConstraintTracker, _CostTracker
from .context import PlannerPauseReason, ToolContext
from .hints import _PlanningHints
from .llm import (
    _coerce_llm_response,
    _estimate_size,
    _LiteLLMJSONClient,
    _sanitize_json_schema,
    build_messages,
    critique_answer,
    generate_clarification,
    request_revision,
    summarise_trajectory,
)
from .models import (
    ClarificationResponse,
    JoinInjection,
    JSONLLMClient,
    ParallelCall,
    ParallelJoin,
    PlannerAction,
    PlannerEvent,
    PlannerEventCallback,
    PlannerFinish,
    PlannerPause,
    ReflectionConfig,
    ReflectionCriteria,
    ReflectionCritique,
    ToolPolicy,
)
from .parallel import execute_parallel_plan
from .pause import _PauseRecord, _PlannerPauseSignal
from .trajectory import Trajectory, TrajectoryStep, TrajectorySummary

# Planner-specific logger
logger = logging.getLogger("penguiflow.planner")


def _validate_llm_context(
    llm_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Ensure llm_context is JSON-serialisable."""

    if llm_context is None:
        return None
    if not isinstance(llm_context, Mapping):
        raise TypeError("llm_context must be a mapping of JSON-serializable data")
    try:
        json.dumps(llm_context, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"llm_context must be JSON-serializable: {exc}"
        ) from exc
    return dict(llm_context)


def _coerce_tool_context(
    tool_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalise tool_context to a mutable dict."""

    if tool_context is None:
        return {}
    if not isinstance(tool_context, Mapping):
        raise TypeError("tool_context must be a mapping")
    return dict(tool_context)



@dataclass(slots=True)
class _StreamChunk:
    """Streaming chunk captured during planning."""

    stream_id: str
    seq: int
    text: str
    done: bool
    meta: Mapping[str, Any]
    ts: float


class _PlannerContext(ToolContext):
    __slots__ = (
        "_llm_context",
        "_tool_context",
        "_planner",
        "_trajectory",
        "_chunks",
        "_meta_warned",
    )

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self._llm_context = dict(trajectory.llm_context or {})
        self._tool_context = dict(trajectory.tool_context or {})
        self._planner = planner
        self._trajectory = trajectory
        self._chunks: list[_StreamChunk] = []
        self._meta_warned = False

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return MappingProxyType(self._llm_context)

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> MutableMapping[str, Any]:
        if not self._meta_warned:
            warnings.warn(
                "ctx.meta is deprecated; use ctx.llm_context and ctx.tool_context instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._meta_warned = True
        return ChainMap(self._tool_context, self._llm_context)

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit streaming chunk during tool execution."""

        chunk = _StreamChunk(
            stream_id=stream_id,
            seq=seq,
            text=text,
            done=done,
            meta=dict(meta or {}),
            ts=self._planner._time_source(),
        )
        self._chunks.append(chunk)

        self._planner._emit_event(
            PlannerEvent(
                event_type="stream_chunk",
                ts=chunk.ts,
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "stream_id": stream_id,
                    "seq": seq,
                    "text": text,
                    "done": done,
                    "meta": dict(meta or {}),
                },
            )
        )

    def _collect_chunks(self) -> dict[str, list[dict[str, Any]]]:
        """Collect streaming chunks grouped by stream identifier."""

        if not self._chunks:
            return {}

        streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in self._chunks:
            streams[chunk.stream_id].append(
                {
                    "seq": chunk.seq,
                    "text": chunk.text,
                    "done": chunk.done,
                    "meta": dict(chunk.meta),
                    "ts": chunk.ts,
                }
            )

        for stream_chunks in streams.values():
            stream_chunks.sort(key=lambda payload: payload["seq"])

        return dict(streams)

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
        Optional instructions for interpreting custom context (e.g., memory format).
        Use this to specify how the planner should use structured data passed via
        llm_context. The library provides baseline injection; this parameter lets
        you define format-specific semantics.

        Examples:
        - "memories contains JSON with user preferences; respect them when planning"
        - "context.knowledge is a flat list of facts; cite relevant ones"
        - "Use context.history to avoid repeating failed approaches"
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
    reflection_config : ReflectionConfig | None
        Optional configuration enabling automatic answer critique before
        finishing. Disabled by default.
    reflection_llm : str | Mapping[str, Any] | None
        Optional LiteLLM identifier used for critique when
        ``reflection_config.use_separate_llm`` is ``True``.
    planning_hints : Mapping[str, Any] | None
        Structured constraints and preferences (ordering, disallowed nodes,
        max_parallel, etc.). See plan.md for schema.
    tool_policy : ToolPolicy | None
        Optional runtime policy that filters the tool catalog (whitelists,
        blacklists, or tag requirements) for multi-tenant and safety use cases.
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
        reflection_config: ReflectionConfig | None = None,
        reflection_llm: str | Mapping[str, Any] | None = None,
        tool_policy: ToolPolicy | None = None,
    ) -> None:
        if catalog is None:
            if nodes is None or registry is None:
                raise ValueError(
                    "Either catalog or (nodes and registry) must be provided"
                )
            catalog = build_catalog(nodes, registry)

        self._tool_policy = tool_policy
        specs = list(catalog)
        if tool_policy is not None:
            filtered_specs: list[NodeSpec] = []
            removed: list[str] = []
            for spec in specs:
                if tool_policy.is_allowed(spec.name, spec.tags):
                    filtered_specs.append(spec)
                else:
                    removed.append(spec.name)

            if removed:
                logger.info(
                    "planner_tool_policy_filtered",
                    extra={
                        "removed": removed,
                        "original_count": len(specs),
                        "filtered_count": len(filtered_specs),
                    },
                )
            if not filtered_specs:
                logger.warning(
                    "planner_tool_policy_empty",
                    extra={"original_count": len(specs)},
                )
            specs = filtered_specs

        self._specs = specs
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
        self._cost_tracker = _CostTracker()
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
        self._reflection_client: JSONLLMClient | None = None
        self._clarification_client: JSONLLMClient | None = None
        self._reflection_config = reflection_config

        if llm_client is not None:
            self._client = llm_client

            # CRITICAL: Detect DSPy client and create separate instances for multi-schema support
            # DSPyLLMClient is hardcoded to a single output schema, so we need separate
            # instances for reflection (ReflectionCritique), summarization (TrajectorySummary),
            # and clarification (ClarificationResponse)
            from .dspy_client import DSPyLLMClient
            is_dspy = isinstance(llm_client, DSPyLLMClient)

            # Create DSPy reflection client if reflection enabled
            if is_dspy and reflection_config and reflection_config.enabled:
                assert isinstance(llm_client, DSPyLLMClient)  # for mypy
                logger.info(
                    "dspy_reflection_client_creation",
                    extra={"schema": "ReflectionCritique"},
                )
                self._reflection_client = DSPyLLMClient.from_base_client(
                    llm_client, ReflectionCritique
                )

                # Create DSPy clarification client (used when reflection fails)
                logger.info(
                    "dspy_clarification_client_creation",
                    extra={"schema": "ClarificationResponse"},
                )
                self._clarification_client = DSPyLLMClient.from_base_client(
                    llm_client, ClarificationResponse
                )

            # Create DSPy summarizer client if summarization enabled
            if is_dspy and token_budget is not None and token_budget > 0:
                assert isinstance(llm_client, DSPyLLMClient)  # for mypy
                logger.info(
                    "dspy_summarizer_client_creation",
                    extra={"schema": "TrajectorySummary"},
                )
                self._summarizer_client = DSPyLLMClient.from_base_client(
                    llm_client, TrajectorySummary
                )
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

        # LiteLLM-based separate clients (override DSPy if explicitly provided)
        if summarizer_llm is not None:
            self._summarizer_client = _LiteLLMJSONClient(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

        # Only set reflection client from reflection_llm if not already set by DSPy
        if self._reflection_client is None:
            if reflection_config and reflection_config.use_separate_llm:
                if reflection_llm is None:
                    raise ValueError(
                        "reflection_llm required when use_separate_llm=True"
                    )
                self._reflection_client = _LiteLLMJSONClient(
                    reflection_llm,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                )

    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        context_meta: Mapping[str, Any] | None = None,  # Deprecated
        tool_context: Mapping[str, Any] | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Execute planner on a query until completion or pause.

        Parameters
        ----------
        query : str
            Natural language task description.
        llm_context : Mapping[str, Any] | None
            Optional context visible to LLM (memories, status_history, etc.).
            Should NOT include internal metadata like tenant_id or trace_id.
        context_meta : Mapping[str, Any] | None
            **Deprecated**: Use llm_context instead. This parameter is kept for
            backward compatibility but will be removed in a future version.
        tool_context : Mapping[str, Any] | None
            Tool-only context (callbacks, loggers, telemetry objects). Not
            visible to the LLM. May contain non-serialisable objects.

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
        normalised_llm_context = _validate_llm_context(llm_context)
        normalised_tool_context = _coerce_tool_context(tool_context)
        self._cost_tracker = _CostTracker()
        trajectory = Trajectory(
            query=query,
            llm_context=normalised_llm_context,
            tool_context=normalised_tool_context,
        )
        return await self._run_loop(trajectory, tracker=None)

    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Resume a paused planning session.

        Parameters
        ----------
        token : str
            Resume token from a previous PlannerPause.
        user_input : str | None
            Optional user response to the pause (e.g., approval decision).
        tool_context : Mapping[str, Any] | None
            Tool-only context (callbacks, loggers, telemetry objects). Not
            visible to the LLM. May contain non-serialisable objects. Overrides
            any tool_context captured in the pause record.

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
        provided_tool_context = (
            _coerce_tool_context(tool_context) if tool_context is not None else None
        )
        record = await self._load_pause_record(token)
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
                    candidate_answer = action.args or last_observation
                    metadata_reflection: dict[str, Any] | None = None

                    if (
                        candidate_answer is not None
                        and self._reflection_config
                        and self._reflection_config.enabled
                    ):
                        critique: ReflectionCritique | None = None
                        metadata_reflection = {}
                        for revision_idx in range(
                            self._reflection_config.max_revisions + 1
                        ):
                            critique = await self._critique_answer(
                                trajectory, candidate_answer
                            )

                            self._emit_event(
                                PlannerEvent(
                                    event_type="reflection_critique",
                                    ts=self._time_source(),
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

                            if (
                                critique.passed
                                or critique.score
                                >= self._reflection_config.quality_threshold
                            ):
                                logger.info(
                                    "reflection_passed",
                                    extra={
                                        "score": critique.score,
                                        "revisions": revision_idx,
                                    },
                                )
                                break

                            if revision_idx >= self._reflection_config.max_revisions:
                                threshold = (
                                    self._reflection_config.quality_threshold
                                )

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
                                    clarification_text = await self._generate_clarification(
                                        trajectory=trajectory,
                                        failed_answer=candidate_answer,
                                        critique=critique,
                                        revision_attempts=revision_idx,
                                    )

                                    # Replace candidate answer with clarification
                                    # Ensure proper structure for downstream consumers (like FinalAnswer model)
                                    if isinstance(candidate_answer, dict):
                                        # Update existing dict with clarification
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
                                    self._emit_event(
                                        PlannerEvent(
                                            event_type="reflection_clarification_generated",
                                            ts=self._time_source(),
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

                            revision_action = await self._request_revision(
                                trajectory,
                                critique,
                            )
                            candidate_answer = (
                                revision_action.args or revision_action.model_dump()
                            )
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
                                    self._reflection_config.max_revisions,
                                ),
                                "passed": critique.passed,
                            }
                            if critique.feedback:
                                metadata_reflection["feedback"] = critique.feedback

                    payload = candidate_answer
                    metadata_extra: dict[str, Any] | None = None
                    if metadata_reflection is not None:
                        metadata_extra = {"reflection": metadata_reflection}

                    return self._finish(
                        trajectory,
                        reason="answer_complete",
                        payload=payload,
                        thought=action.thought,
                        constraints=tracker,
                        metadata_extra=metadata_extra,
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
                    await self._record_pause(signal.pause, trajectory, tracker)
                    return signal.pause
                except Exception as exc:
                    failure_payload = self._build_failure_payload(
                        spec, parsed_args, exc
                    )
                    error = (
                        f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
                    )
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
                    continue

                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        observation=observation,
                        streams=step_chunks or None,
                    )
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

            llm_result = await self._client.complete(
                messages=messages,
                response_format=response_format,
            )
            raw, cost = _coerce_llm_response(llm_result)
            self._cost_tracker.record_main_call(cost)

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
        return await execute_parallel_plan(self, action, trajectory, tracker)

    def _make_context(self, trajectory: Trajectory) -> _PlannerContext:
        return _PlannerContext(self, trajectory)

    async def _build_messages(self, trajectory: Trajectory) -> list[dict[str, str]]:
        return await build_messages(self, trajectory)

    def _estimate_size(self, messages: Sequence[Mapping[str, str]]) -> int:
        return _estimate_size(messages)

    async def _summarise_trajectory(
        self, trajectory: Trajectory
    ) -> TrajectorySummary:
        return await summarise_trajectory(self, trajectory)

    async def _critique_answer(
        self,
        trajectory: Trajectory,
        candidate: Any,
    ) -> ReflectionCritique:
        return await critique_answer(self, trajectory, candidate)

    async def _request_revision(
        self,
        trajectory: Trajectory,
        critique: ReflectionCritique,
    ) -> PlannerAction:
        return await request_revision(self, trajectory, critique)

    async def _generate_clarification(
        self,
        trajectory: Trajectory,
        failed_answer: str | dict[str, Any] | Any,
        critique: ReflectionCritique,
        revision_attempts: int,
    ) -> str:
        return await generate_clarification(
            self, trajectory, failed_answer, critique, revision_attempts
        )

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
        snapshot.tool_context = dict(trajectory.tool_context or {})
        record = _PauseRecord(
            trajectory=snapshot,
            reason=pause.reason,
            payload=dict(pause.payload),
            constraints=tracker.snapshot() if tracker is not None else None,
            tool_context=dict(snapshot.tool_context or {}),
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
                    tool_context_payload = result.get("tool_context")
                    tool_context = (
                        dict(tool_context_payload)
                        if isinstance(tool_context_payload, Mapping)
                        else None
                    )
                    logger.debug("pause_record_loaded", extra={"source": "state_store"})
                    return _PauseRecord(
                        trajectory=trajectory,
                        reason=reason,
                        payload=payload,
                        constraints=constraints,
                        tool_context=tool_context,
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
        tool_context: dict[str, Any] | None = None
        if record.tool_context is not None:
            try:
                tool_context = json.loads(
                    json.dumps(record.tool_context, ensure_ascii=False)
                )
            except (TypeError, ValueError):
                tool_context = None
        return {
            "trajectory": record.trajectory.serialise(),
            "reason": record.reason,
            "payload": dict(record.payload),
            "constraints": dict(record.constraints)
            if record.constraints is not None
            else None,
            "tool_context": tool_context,
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
        metadata_extra: Mapping[str, Any] | None = None,
    ) -> PlannerFinish:
        metadata = {
            "reason": reason,
            "thought": thought,
            "steps": trajectory.to_history(),
            "step_count": len(trajectory.steps),
        }
        metadata["cost"] = self._cost_tracker.snapshot()
        if constraints is not None:
            metadata["constraints"] = constraints.snapshot()
        if error is not None:
            metadata["error"] = error
        if metadata_extra:
            metadata.update(metadata_extra)

        # Emit finish event
        extra_data: dict[str, Any] = {"reason": reason, "cost": metadata["cost"]}
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
    "JoinInjection",
    "ParallelCall",
    "ParallelJoin",
    "PlannerAction",
    "PlannerEvent",
    "PlannerEventCallback",
    "PlannerFinish",
    "PlannerPause",
    "ReflectionConfig",
    "ReflectionCriteria",
    "ReflectionCritique",
    "ReactPlanner",
    "_sanitize_json_schema",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
]
