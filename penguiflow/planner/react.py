"""JSON-only ReAct planner loop with pause/resume and summarisation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from ..artifacts import ArtifactStore
from ..catalog import NodeSpec, build_catalog
from ..node import Node
from ..registry import ModelRegistry
from . import prompts
from .artifact_handling import (  # noqa: F401
    _ArtifactCollector,
    _EventEmittingArtifactStoreProxy,
    _extract_source_payloads,
    _model_json_schema_extra,
    _normalise_artifact_value,
    _produces_sources,
    _source_field_map,
    _SourceCollector,
)
from .constraints import _ConstraintTracker
from .context import PlannerPauseReason
from .error_recovery import ErrorRecoveryConfig
from .llm import (
    _estimate_size,
    _sanitize_json_schema,
    _unwrap_model,  # noqa: F401
    build_messages,
    critique_answer,
    generate_clarification,
    request_revision,
    summarise_trajectory,
)
from .memory import (
    ConversationTurn,
    MemoryKey,
    ShortTermMemory,
    ShortTermMemoryConfig,
    TrajectoryDigest,  # noqa: F401
)
from .memory_integration import (
    _apply_memory_context,
    _build_memory_turn,
    _extract_memory_key_from_tool_context,
    _get_memory_for_key,
    _get_short_term_memory_summarizer,
    _maybe_memory_hydrate,
    _maybe_memory_persist,
    _maybe_record_memory_turn,
    _resolve_memory_key,
)
from .models import (
    BackgroundTasksConfig,
    FinalPayload,
    JoinInjection,
    JSONLLMClient,
    ObservationGuardrailConfig,
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
    ToolVisibilityPolicy,
)
from .parallel import execute_parallel_plan
from .pause_management import (
    _load_pause_record as _load_pause_record_impl,
)
from .pause_management import (
    _pause_from_context as _pause_from_context_impl,
)
from .pause_management import (
    _record_pause as _record_pause_impl,
)
from .pause_management import (
    _serialise_pause_record as _serialise_pause_record_impl,
)
from .pause_management import (
    _store_pause_record as _store_pause_record_impl,
)
from .pause_management import (
    pause as _pause_impl,
)
from .payload_builders import (
    _build_failure_payload as _build_failure_payload_impl,
)
from .payload_builders import (
    _build_final_payload as _build_final_payload_impl,
)
from .payload_builders import (
    _clamp_observation as _clamp_observation_impl,
)
from .payload_builders import (
    _emit_observation_clamped_event as _emit_observation_clamped_event_impl,
)
from .payload_builders import _fallback_answer  # noqa: F401
from .payload_builders import (
    _truncate_observation_preserving_structure as _truncate_observation_preserving_structure_impl,
)
from .planner_context import _PlannerContext
from .react_init import init_react_planner as _init_react_planner
from .react_runtime import (
    _check_deadline as _check_deadline_impl,
)
from .react_runtime import (
    _emit_step_start as _emit_step_start_impl,
)
from .react_runtime import (
    _handle_finish_action as _handle_finish_action_impl,
)
from .react_runtime import (
    _handle_parallel_plan as _handle_parallel_plan_impl,
)
from .react_runtime import (
    _log_action_received as _log_action_received_impl,
)
from .react_runtime import (
    resume as _resume_impl,
)
from .react_runtime import (
    run as _run_impl,
)
from .react_runtime import (
    run_loop as _run_loop_impl,
)
from .react_step import step as _step_impl
from .react_utils import _safe_json_dumps  # noqa: F401
from .streaming import _ArtifactChunk, _StreamChunk, _StreamingArgsExtractor, _StreamingThoughtExtractor  # noqa: F401
from .trajectory import Trajectory, TrajectoryStep, TrajectorySummary
from .validation_repair import (  # noqa: F401
    AUTO_STR_SENTINEL,
    _autofill_missing_args,
    _coerce_tool_context,
    _default_for_annotation,
    _salvage_action_payload,
    _scan_placeholder_paths,
    _summarize_validation_error,
    _validate_llm_context,
)
from .validation_repair import (
    _apply_arg_validation as _apply_arg_validation_impl,
)
from .validation_repair import (
    _attempt_arg_fill as _attempt_arg_fill_impl,
)
from .validation_repair import (
    _attempt_finish_repair as _attempt_finish_repair_impl,
)
from .validation_repair import (
    _attempt_graceful_failure as _attempt_graceful_failure_impl,
)
from .validation_repair import (
    _extract_field_descriptions as _extract_field_descriptions_impl,
)
from .validation_repair import (
    _is_arg_fill_eligible as _is_arg_fill_eligible_impl,
)
from .validation_repair import (
    _parse_arg_fill_response as _parse_arg_fill_response_impl,
)
from .validation_repair import (
    _parse_finish_repair_response as _parse_finish_repair_response_impl,
)
from .validation_repair import (
    _record_invalid_response as _record_invalid_response_impl,
)

if TYPE_CHECKING:
    from ..steering import SteeringInbox
    from .artifact_registry import ArtifactRegistry
    from .constraints import _CostTracker
    from .guardrails import GuardrailContext, GuardrailDecision, GuardrailEvent, GuardrailGateway
    from .hints import _PlanningHints
    from .pause import _PauseRecord

logger = logging.getLogger("penguiflow.planner")


class _GuardrailStreamHandler:
    """Serializes guardrail evaluation for streamed LLM chunks."""

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self._planner = planner
        self._trajectory = trajectory
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._queue.put_nowait(payload)

    async def close(self) -> None:
        await self._queue.put(None)
        await self._task

    async def _run(self) -> None:
        while True:
            payload = await self._queue.get()
            if payload is None:
                break
            await self._planner._emit_guardrailed_llm_stream_chunk(self._trajectory, payload)


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
    max_consecutive_arg_failures : int
        Max consecutive tool arg validation failures before forcing a finish
        with requires_followup=True. Helps small models avoid infinite loops
        when they repeatedly produce invalid args. Default: 3.
    arg_fill_enabled : bool
        Enable arg-fill mode for missing tool arguments. When True, if a tool
        call has valid tool selection but missing/invalid args, the planner
        will make a simplified LLM call asking only for the missing values
        instead of requiring a full JSON repair. This significantly improves
        success rates for small models. Default: True.
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
    _absolute_max_parallel: int
    _action_schema: Mapping[str, Any]
    _action_seq: int
    _active_tracker: _ConstraintTracker | None
    _active_trajectory: Trajectory | None
    _arg_fill_enabled: bool
    _artifact_registry: ArtifactRegistry
    _artifact_store: ArtifactStore
    _catalog_records: list[dict[str, Any]]
    _tool_aliases: dict[str, str]
    _clarification_client: JSONLLMClient | None
    _client: JSONLLMClient
    _cost_tracker: _CostTracker
    _deadline_s: float | None
    _event_callback: PlannerEventCallback | None
    _hop_budget: int | None
    _json_schema_mode: bool
    _max_consecutive_arg_failures: int
    _max_iters: int
    _memory_by_key: dict[str, ShortTermMemory]
    _memory_config: ShortTermMemoryConfig
    _memory_ephemeral_key: MemoryKey | None
    _memory_singleton: ShortTermMemory | None
    _memory_summarizer: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None
    _memory_summarizer_client: JSONLLMClient | None
    _observation_guardrail: ObservationGuardrailConfig
    _pause_enabled: bool
    _pause_records: dict[str, _PauseRecord]
    _planning_hints: _PlanningHints
    _ready_answer_seq: int | None
    _reflection_client: JSONLLMClient | None
    _reflection_config: ReflectionConfig | None
    _repair_attempts: int
    _response_format: Mapping[str, Any] | None
    _spec_by_name: dict[str, NodeSpec]
    _specs: list[NodeSpec]
    _state_store: Any | None
    _stream_final_response: bool
    _summarizer_client: JSONLLMClient | None
    _system_prompt: str
    _system_prompt_extra: str | None
    _finish_repair_history_count: int
    _arg_fill_repair_history_count: int
    _multi_action_history_count: int
    _time_source: Callable[[], float]
    _token_budget: int | None
    _tool_policy: ToolPolicy | None
    _background_tasks: BackgroundTasksConfig
    _multi_action_sequential: bool
    _multi_action_read_only_only: bool
    _multi_action_max_tools: int
    _auto_seq_enabled: bool
    _auto_seq_execute: bool
    _auto_seq_read_only_only: bool
    _use_native_reasoning: bool
    _reasoning_effort: str | None
    _guardrail_gateway: GuardrailGateway | None
    _guardrail_context: GuardrailContext | None
    _guardrail_run_id: str | None
    _init_kwargs: dict[str, Any] | None
    _guardrail_stream_handler: _GuardrailStreamHandler | None
    _guardrail_stream_decision: GuardrailDecision | None
    _session_dispatch_enabled: bool
    _session_registry_lock: asyncio.Lock
    _session_locks: dict[str, asyncio.Lock]
    _session_planners: dict[str, ReactPlanner]
    _fallback_run_lock: asyncio.Lock

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
        artifact_store: ArtifactStore | None = None,
        observation_guardrail: ObservationGuardrailConfig | None = None,
        summarizer_llm: str | Mapping[str, Any] | None = None,
        planning_hints: Mapping[str, Any] | None = None,
        repair_attempts: int = 3,
        max_consecutive_arg_failures: int = 3,
        arg_fill_enabled: bool = True,
        deadline_s: float | None = None,
        hop_budget: int | None = None,
        time_source: Callable[[], float] | None = None,
        event_callback: PlannerEventCallback | None = None,
        llm_timeout_s: float = 60.0,
        llm_max_retries: int = 3,
        use_native_reasoning: bool = True,
        reasoning_effort: str | None = None,
        absolute_max_parallel: int = 50,
        reflection_config: ReflectionConfig | None = None,
        reflection_llm: str | Mapping[str, Any] | None = None,
        tool_policy: ToolPolicy | None = None,
        stream_final_response: bool = False,
        short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None = None,
        background_tasks: BackgroundTasksConfig | None = None,
        error_recovery: ErrorRecoveryConfig | None = None,
        multi_action_sequential: bool = False,
        multi_action_read_only_only: bool = True,
        multi_action_max_tools: int = 2,
        auto_seq_enabled: bool = False,
        auto_seq_execute: bool = False,
        auto_seq_read_only_only: bool = True,
        use_native_llm: bool = False,
        guardrail_gateway: Any | None = None,
        guardrail_conversation_history_turns: int = 1,
    ) -> None:
        # NOTE: ReactPlanner has mutable per-run state and is not safe to call concurrently on a single
        # instance. We provide an internal hotfix path that serializes calls per session_id while
        # allowing concurrency across sessions by routing execution to per-session forked planners.
        # This avoids API changes for downstream orchestrators that reuse a single ReactPlanner.
        self._session_dispatch_enabled = True
        self._session_registry_lock = asyncio.Lock()
        self._session_locks = {}
        self._session_planners = {}
        self._fallback_run_lock = asyncio.Lock()

        # Store init kwargs so the planner can be safely forked for background tasks.
        # This is intentionally best-effort and uses references for non-serialisable objects.
        self._init_kwargs = {
            "llm": llm,
            "nodes": nodes,
            "catalog": catalog,
            "registry": registry,
            "llm_client": llm_client,
            "max_iters": max_iters,
            "temperature": temperature,
            "json_schema_mode": json_schema_mode,
            "system_prompt_extra": system_prompt_extra,
            "token_budget": token_budget,
            "pause_enabled": pause_enabled,
            "state_store": state_store,
            "artifact_store": artifact_store,
            "observation_guardrail": observation_guardrail,
            "summarizer_llm": summarizer_llm,
            "planning_hints": dict(planning_hints) if isinstance(planning_hints, Mapping) else planning_hints,
            "repair_attempts": repair_attempts,
            "max_consecutive_arg_failures": max_consecutive_arg_failures,
            "arg_fill_enabled": arg_fill_enabled,
            "deadline_s": deadline_s,
            "hop_budget": hop_budget,
            "time_source": time_source,
            "event_callback": event_callback,
            "llm_timeout_s": llm_timeout_s,
            "llm_max_retries": llm_max_retries,
            "use_native_reasoning": use_native_reasoning,
            "reasoning_effort": reasoning_effort,
            "absolute_max_parallel": absolute_max_parallel,
            "reflection_config": reflection_config,
            "reflection_llm": reflection_llm,
            "tool_policy": tool_policy,
            "stream_final_response": stream_final_response,
            "short_term_memory": short_term_memory,
            "background_tasks": background_tasks,
            "error_recovery": error_recovery,
            "multi_action_sequential": multi_action_sequential,
            "multi_action_read_only_only": multi_action_read_only_only,
            "multi_action_max_tools": multi_action_max_tools,
            "auto_seq_enabled": auto_seq_enabled,
            "auto_seq_execute": auto_seq_execute,
            "auto_seq_read_only_only": auto_seq_read_only_only,
            "use_native_llm": use_native_llm,
            "guardrail_gateway": guardrail_gateway,
            "guardrail_conversation_history_turns": guardrail_conversation_history_turns,
        }
        _init_react_planner(
            self,
            llm,
            nodes=nodes,
            catalog=catalog,
            registry=registry,
            llm_client=llm_client,
            max_iters=max_iters,
            temperature=temperature,
            json_schema_mode=json_schema_mode,
            system_prompt_extra=system_prompt_extra,
            token_budget=token_budget,
            pause_enabled=pause_enabled,
            state_store=state_store,
            artifact_store=artifact_store,
            observation_guardrail=observation_guardrail,
            summarizer_llm=summarizer_llm,
            planning_hints=planning_hints,
            repair_attempts=repair_attempts,
            max_consecutive_arg_failures=max_consecutive_arg_failures,
            arg_fill_enabled=arg_fill_enabled,
            deadline_s=deadline_s,
            hop_budget=hop_budget,
            time_source=time_source,
            event_callback=event_callback,
            llm_timeout_s=llm_timeout_s,
            llm_max_retries=llm_max_retries,
            use_native_reasoning=use_native_reasoning,
            reasoning_effort=reasoning_effort,
            absolute_max_parallel=absolute_max_parallel,
            reflection_config=reflection_config,
            reflection_llm=reflection_llm,
            tool_policy=tool_policy,
            stream_final_response=stream_final_response,
            short_term_memory=short_term_memory,
            background_tasks=background_tasks,
            error_recovery=error_recovery,
            multi_action_sequential=multi_action_sequential,
            multi_action_read_only_only=multi_action_read_only_only,
            multi_action_max_tools=multi_action_max_tools,
            auto_seq_enabled=auto_seq_enabled,
            auto_seq_execute=auto_seq_execute,
            auto_seq_read_only_only=auto_seq_read_only_only,
            use_native_llm=use_native_llm,
            guardrail_gateway=guardrail_gateway,
            guardrail_conversation_history_turns=guardrail_conversation_history_turns,
        )
        self._guardrail_stream_handler = None
        self._guardrail_stream_decision = None

    def fork(
        self,
        *,
        catalog_filter: Callable[[NodeSpec], bool] | None = None,
        tool_policy: ToolPolicy | None = None,
        inherit_policy: bool = True,
        background_tasks: BackgroundTasksConfig | None | Literal["inherit"] = "inherit",
    ) -> ReactPlanner:
        """Create a new planner instance with the same configuration.

        Background task orchestration requires a fresh ReactPlanner per task because
        the planner maintains mutable per-run state and is not thread-safe.
        """

        init_kwargs = dict(self._init_kwargs or {})
        if tool_policy is not None:
            base_policy = init_kwargs.get("tool_policy")
            if inherit_policy and isinstance(base_policy, ToolPolicy):
                allowed: set[str] | None
                if base_policy.allowed_tools is None:
                    allowed = tool_policy.allowed_tools
                elif tool_policy.allowed_tools is None:
                    allowed = base_policy.allowed_tools
                else:
                    allowed = base_policy.allowed_tools & tool_policy.allowed_tools
                init_kwargs["tool_policy"] = ToolPolicy(
                    allowed_tools=allowed,
                    denied_tools=set(base_policy.denied_tools) | set(tool_policy.denied_tools),
                    require_tags=set(base_policy.require_tags) | set(tool_policy.require_tags),
                )
            else:
                init_kwargs["tool_policy"] = tool_policy
        base_catalog = init_kwargs.get("catalog")
        nodes = init_kwargs.get("nodes")
        registry = init_kwargs.get("registry")

        specs: list[NodeSpec]
        if isinstance(base_catalog, Sequence):
            specs = list(base_catalog)
        elif isinstance(nodes, Sequence) and isinstance(registry, ModelRegistry):
            specs = list(build_catalog(nodes, registry))
        else:
            # Best-effort fallback for legacy planners constructed before _init_kwargs existed.
            specs = list(getattr(self, "_specs", []) or [])

        if catalog_filter is not None:
            specs = [spec for spec in specs if catalog_filter(spec)]

        # Force catalog-based init to avoid depending on registry merging behaviour.
        init_kwargs["catalog"] = specs
        init_kwargs["nodes"] = None
        init_kwargs["registry"] = None
        init_kwargs["event_callback"] = None
        if background_tasks != "inherit":
            init_kwargs["background_tasks"] = background_tasks

        child = ReactPlanner(**init_kwargs)
        # Child planners created for background tasks and per-session dispatch should not themselves
        # perform session dispatch; the dispatch happens at the owning/root planner.
        child._session_dispatch_enabled = False
        return child

    def _extract_session_id(
        self,
        *,
        tool_context: Mapping[str, Any] | None,
        memory_key: MemoryKey | None,
    ) -> str | None:
        if tool_context is not None:
            value = tool_context.get("session_id")
            if value is not None:
                return str(value)
        if memory_key is not None:
            return str(memory_key.session_id)
        return None

    async def _get_or_create_session_runtime(self, session_id: str) -> tuple[asyncio.Lock, ReactPlanner]:
        async with self._session_registry_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            planner = self._session_planners.get(session_id)
            if planner is None:
                planner = self.fork()
                # Preserve legacy behaviour where memory state is observable on the original planner
                # instance. Per-session planners share the same memory map so callers that reuse a
                # single ReactPlanner can still access `_memory_by_key` after a run.
                planner._memory_by_key = self._memory_by_key
                self._session_planners[session_id] = planner
            return lock, planner

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the configured artifact store (NoOp when disabled)."""
        return self._artifact_store

    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        context_meta: Mapping[str, Any] | None = None,  # Deprecated
        tool_context: Mapping[str, Any] | None = None,
        memory_key: MemoryKey | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
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
        memory_key : MemoryKey | None
            Optional explicit short-term memory key. If omitted, the planner may
            derive a key from `tool_context` using the configured memory isolation
            paths. If no key is available and memory is configured to require an
            explicit key, memory behaves as disabled for this call.

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
        session_id = None
        if self._session_dispatch_enabled:
            session_id = self._extract_session_id(tool_context=tool_context, memory_key=memory_key)

        if session_id:
            session_lock, session_planner = await self._get_or_create_session_runtime(session_id)
            previous_callback = getattr(session_planner, "_event_callback", None)
            session_planner._event_callback = getattr(self, "_event_callback", None)
            try:
                async with session_lock:
                    return await session_planner.run(
                        query,
                        llm_context=llm_context,
                        context_meta=context_meta,
                        tool_context=tool_context,
                        memory_key=memory_key,
                        steering=steering,
                        tool_visibility=tool_visibility,
                    )
            finally:
                session_planner._event_callback = previous_callback

        # Fallback: serialize when no session_id is provided to avoid corrupting shared mutable state.
        async with self._fallback_run_lock:
            previous = getattr(self, "_steering", None)
            self._steering = steering
            try:
                return await _run_impl(
                    self,
                    query,
                    llm_context=llm_context,
                    context_meta=context_meta,
                    tool_context=tool_context,
                    memory_key=memory_key,
                    tool_visibility=tool_visibility,
                )
            finally:
                self._steering = previous

    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
        memory_key: MemoryKey | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
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
        memory_key : MemoryKey | None
            Optional explicit short-term memory key for the resumed session. If
            omitted, the planner may derive a key from `tool_context` using the
            configured memory isolation paths. If no key is available and memory
            is configured to require an explicit key, memory behaves as disabled
            for this call.

        Returns
        -------
        PlannerFinish | PlannerPause
            Updated result after resuming execution.

        Raises
        ------
        KeyError
            If resume token is invalid or expired.
        """
        session_id = None
        if self._session_dispatch_enabled:
            session_id = self._extract_session_id(tool_context=tool_context, memory_key=memory_key)

        if session_id:
            session_lock, session_planner = await self._get_or_create_session_runtime(session_id)
            previous_callback = getattr(session_planner, "_event_callback", None)
            session_planner._event_callback = getattr(self, "_event_callback", None)
            try:
                async with session_lock:
                    return await session_planner.resume(
                        token,
                        user_input=user_input,
                        tool_context=tool_context,
                        memory_key=memory_key,
                        steering=steering,
                        tool_visibility=tool_visibility,
                    )
            finally:
                session_planner._event_callback = previous_callback

        async with self._fallback_run_lock:
            previous = getattr(self, "_steering", None)
            self._steering = steering
            try:
                return await _resume_impl(
                    self,
                    token,
                    user_input=user_input,
                    tool_context=tool_context,
                    memory_key=memory_key,
                    tool_visibility=tool_visibility,
                )
            finally:
                self._steering = previous

    def _resolve_memory_key(
        self,
        explicit: MemoryKey | None,
        tool_context: Mapping[str, Any] | None,
    ) -> MemoryKey | None:
        return _resolve_memory_key(self, explicit, tool_context)

    def _get_memory_for_key(self, key: MemoryKey) -> ShortTermMemory | None:
        return _get_memory_for_key(self, key)

    def _get_short_term_memory_summarizer(self) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
        return _get_short_term_memory_summarizer(self)

    def _extract_memory_key_from_tool_context(self, tool_context: Mapping[str, Any]) -> MemoryKey | None:
        return _extract_memory_key_from_tool_context(self, tool_context)

    async def _apply_memory_context(
        self,
        llm_context: dict[str, Any] | None,
        key: MemoryKey | None,
    ) -> dict[str, Any] | None:
        return await _apply_memory_context(self, llm_context, key)

    async def _maybe_memory_hydrate(self, memory: ShortTermMemory, key: MemoryKey) -> None:
        await _maybe_memory_hydrate(self, memory, key)

    async def _maybe_memory_persist(self, memory: ShortTermMemory, key: MemoryKey) -> None:
        await _maybe_memory_persist(self, memory, key)

    def _build_memory_turn(self, query: str, result: PlannerFinish, trajectory: Trajectory) -> ConversationTurn:
        return _build_memory_turn(self, query, result, trajectory)

    async def _maybe_record_memory_turn(
        self,
        query: str,
        result: PlannerFinish | PlannerPause,
        trajectory: Trajectory,
        key: MemoryKey | None,
    ) -> None:
        await _maybe_record_memory_turn(self, query, result, trajectory, key)

    def _check_deadline(
        self,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        last_observation: Any | None,
    ) -> PlannerFinish | None:
        return _check_deadline_impl(
            self,
            trajectory,
            tracker,
            artifact_collector,
            source_collector,
            last_observation,
        )

    def _emit_step_start(self, trajectory: Trajectory) -> tuple[float, int]:
        return _emit_step_start_impl(self, trajectory)

    def _log_action_received(self, action: PlannerAction, trajectory: Trajectory) -> None:
        _log_action_received_impl(self, action, trajectory)

    async def _handle_parallel_plan(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        *,
        action_seq: int,
    ) -> tuple[Any | None, PlannerPause | None]:
        return await _handle_parallel_plan_impl(
            self,
            action,
            trajectory,
            tracker,
            artifact_collector,
            source_collector,
            action_seq=action_seq,
        )

    async def _handle_finish_action(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        last_observation: Any | None,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        *,
        action_seq: int,
    ) -> PlannerFinish:
        return await _handle_finish_action_impl(
            self,
            action,
            trajectory,
            tracker,
            last_observation,
            artifact_collector,
            source_collector,
            action_seq=action_seq,
        )

    async def _run_loop(
        self,
        trajectory: Trajectory,
        *,
        tracker: _ConstraintTracker | None,
    ) -> PlannerFinish | PlannerPause:
        return await _run_loop_impl(
            self,
            trajectory,
            tracker=tracker,
        )

    async def step(self, trajectory: Trajectory) -> PlannerAction:
        return await _step_impl(self, trajectory)

    async def _execute_parallel_plan(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        *,
        action_seq: int,
    ) -> tuple[Any | None, PlannerPause | None]:
        return await execute_parallel_plan(
            self,
            action,
            trajectory,
            tracker,
            action_seq=action_seq,
            artifact_collector=artifact_collector,
            source_collector=source_collector,
        )

    def _make_context(self, trajectory: Trajectory) -> _PlannerContext:
        return _PlannerContext(self, trajectory)

    async def _build_messages(self, trajectory: Trajectory) -> list[dict[str, str]]:
        return await build_messages(self, trajectory)

    def _estimate_size(self, messages: Sequence[Mapping[str, str]]) -> int:
        return _estimate_size(messages)

    async def _summarise_trajectory(self, trajectory: Trajectory) -> TrajectorySummary:
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
        *,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> PlannerAction:
        return await request_revision(self, trajectory, critique, on_stream_chunk=on_stream_chunk)

    async def _generate_clarification(
        self,
        trajectory: Trajectory,
        failed_answer: str | dict[str, Any] | Any,
        critique: ReflectionCritique,
        revision_attempts: int,
    ) -> str:
        return await generate_clarification(self, trajectory, failed_answer, critique, revision_attempts)

    def _check_action_constraints(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
    ) -> str | None:
        hints = self._planning_hints
        node_name = action.next_node if action.is_tool_call() else None
        if node_name is not None and not tracker.has_budget_for_next_tool():
            limit = self._hop_budget if self._hop_budget is not None else 0
            return prompts.render_hop_budget_violation(limit)
        if node_name is not None and node_name in hints.disallow_nodes:
            return prompts.render_disallowed_node(node_name)

        # Check parallel execution limits
        if action.next_node == "parallel":
            steps = action.args.get("steps")
            plan_len = len(steps) if isinstance(steps, list) else 0
            # Absolute system-level safety limit
            if plan_len > self._absolute_max_parallel:
                logger.warning(
                    "parallel_limit_absolute",
                    extra={
                        "requested": plan_len,
                        "limit": self._absolute_max_parallel,
                    },
                )
                return prompts.render_parallel_limit(self._absolute_max_parallel)
            # Hint-based limit
            if hints.max_parallel is not None and plan_len > hints.max_parallel:
                return prompts.render_parallel_limit(hints.max_parallel)
        if hints.sequential_only and action.next_node == "parallel":
            steps = action.args.get("steps")
            if isinstance(steps, list):
                for item in steps:
                    if not isinstance(item, Mapping):
                        continue
                    candidate = item.get("node")
                    if isinstance(candidate, str) and candidate in hints.sequential_only:
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
                    if node_name in hints.ordering_hints and not state.get("warned", False):
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
        if expected_index < len(hints.ordering_hints) and node_name == hints.ordering_hints[expected_index]:
            completed.append(node_name)
            state["warned"] = False

    def _build_failure_payload(self, spec: NodeSpec, args: BaseModel, exc: Exception) -> dict[str, Any]:
        return _build_failure_payload_impl(spec, args, exc)

    async def _clamp_observation(
        self,
        observation: dict[str, Any],
        spec_name: str,
        trajectory_step: int,
    ) -> tuple[dict[str, Any], bool]:
        return await _clamp_observation_impl(
            observation=observation,
            spec_name=spec_name,
            trajectory_step=trajectory_step,
            config=self._observation_guardrail,
            artifact_store=self._artifact_store,
            artifact_registry=self._artifact_registry,
            active_trajectory=self._active_trajectory,
            emit_event=self._emit_event,
            time_source=self._time_source,
        )

    def _truncate_observation_preserving_structure(
        self,
        observation: dict[str, Any],
        max_total_chars: int,
        max_field_chars: int,
    ) -> dict[str, Any]:
        return _truncate_observation_preserving_structure_impl(
            observation,
            max_total_chars,
            max_field_chars,
            config=self._observation_guardrail,
        )

    def _emit_observation_clamped_event(
        self,
        node_name: str,
        trajectory_step: int,
        original_size: int,
        clamped_size: int,
        method: str,
    ) -> None:
        _emit_observation_clamped_event_impl(
            emit_event=self._emit_event,
            time_source=self._time_source,
            node_name=node_name,
            trajectory_step=trajectory_step,
            original_size=original_size,
            clamped_size=clamped_size,
            method=method,
        )

    def _build_final_payload(
        self,
        args: Mapping[str, Any] | Any | None,
        last_observation: Any,
        artifacts: Mapping[str, Any],
        sources: Sequence[Mapping[str, Any]] | None,
    ) -> FinalPayload:
        return _build_final_payload_impl(
            args=args,
            last_observation=last_observation,
            artifacts=artifacts,
            sources=sources,
        )

    async def pause(self, reason: PlannerPauseReason, payload: Mapping[str, Any] | None = None) -> PlannerPause:
        return await _pause_impl(self, reason, payload)

    async def _pause_from_context(
        self,
        reason: PlannerPauseReason,
        payload: dict[str, Any],
        trajectory: Trajectory,
    ) -> PlannerPause:
        return await _pause_from_context_impl(self, reason, payload, trajectory)

    async def _record_pause(
        self,
        pause: PlannerPause,
        trajectory: Trajectory,
        tracker: _ConstraintTracker | None,
    ) -> None:
        await _record_pause_impl(self, pause, trajectory, tracker)

    async def _store_pause_record(self, token: str, record: _PauseRecord) -> None:
        await _store_pause_record_impl(self, token, record)

    async def _load_pause_record(self, token: str) -> _PauseRecord:
        return await _load_pause_record_impl(self, token)

    def _serialise_pause_record(self, record: _PauseRecord) -> dict[str, Any]:
        return _serialise_pause_record_impl(record)

    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        for reserved in ("args", "msg", "levelname", "levelno", "exc_info"):
            payload.pop(reserved, None)
        log_fn = logger.debug if event.event_type == "llm_stream_chunk" else logger.info
        log_fn(event.event_type, extra=payload)

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
        self._last_event = event

    def _guardrails_enabled(self) -> bool:
        return self._guardrail_gateway is not None and self._guardrail_context is not None

    def _guardrails_enforced(self) -> bool:
        if not self._guardrails_enabled():
            return False
        mode = getattr(getattr(self._guardrail_gateway, "config", None), "mode", "enforce")
        return mode == "enforce"

    def _build_guardrail_event(
        self,
        *,
        event_type: str,
        text_content: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        trajectory: Trajectory | None = None,
    ) -> GuardrailEvent:
        from .guardrails import GuardrailEvent

        merged_payload = dict(payload or {})
        if trajectory is not None:
            for key, value in self._guardrail_context_payload(trajectory).items():
                merged_payload.setdefault(key, value)

        return GuardrailEvent(
            event_type=event_type,
            run_id=self._guardrail_run_id or "unknown",
            text_content=text_content,
            tool_name=tool_name,
            tool_args=tool_args,
            payload=merged_payload,
        )

    def _guardrail_context_payload(self, trajectory: Trajectory) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        summary = trajectory.summary
        if summary and summary.last_output_digest:
            payload["last_assistant"] = summary.last_output_digest
        elif trajectory.steps:
            last_step = trajectory.steps[-1]
            action = last_step.action
            if action.is_terminal():
                answer = action.answer_text()
                if isinstance(answer, str) and answer.strip():
                    payload["last_assistant"] = answer
            else:
                observation = last_step.observation
                if isinstance(observation, str) and observation.strip():
                    payload["last_assistant"] = observation
                elif isinstance(observation, Mapping):
                    for key in ("answer", "raw_answer", "summary"):
                        value = observation.get(key)
                        if isinstance(value, str) and value.strip():
                            payload["last_assistant"] = value
                            break

        for ctx in (trajectory.tool_context, trajectory.llm_context):
            if isinstance(ctx, Mapping):
                scope = ctx.get("task_scope") or ctx.get("agent_scope")
                if isinstance(scope, str) and scope.strip():
                    payload["task_scope"] = scope.strip()
                    break

        history_turns = int(getattr(self, "_guardrail_conversation_history_turns", 0) or 0)
        conversation_history: list[dict[str, str]] = []
        if history_turns > 0 and isinstance(trajectory.llm_context, Mapping):
            conversation_memory = trajectory.llm_context.get("conversation_memory")
            if isinstance(conversation_memory, Mapping):
                recent_turns = conversation_memory.get("recent_turns")
                if isinstance(recent_turns, list):
                    for item in recent_turns[-history_turns:]:
                        if not isinstance(item, Mapping):
                            continue
                        user = item.get("user")
                        assistant = item.get("assistant")
                        if isinstance(user, str) and isinstance(assistant, str):
                            conversation_history.append({"user": user, "assistant": assistant})
        payload["conversation_history"] = conversation_history

        return payload

    async def _evaluate_guardrail(self, event: GuardrailEvent) -> GuardrailDecision | None:
        if not self._guardrails_enabled():
            return None
        gateway = self._guardrail_gateway
        ctx = self._guardrail_context
        if gateway is None or ctx is None:
            return None
        return await gateway.evaluate(ctx, event)

    def _guardrail_decision_payload(self, decision: GuardrailDecision) -> dict[str, Any]:
        stop = decision.stop
        return {
            "action": decision.action.value,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "decision_id": decision.decision_id,
            "severity": decision.severity.value,
            "effects": list(decision.effects),
            "stop": {
                "error_code": stop.error_code,
                "user_message": stop.user_message,
                "internal_reason": stop.internal_reason,
            }
            if stop is not None
            else None,
        }

    def _ensure_guardrail_stream_handler(self, trajectory: Trajectory) -> None:
        if not self._guardrails_enabled():
            return
        if self._guardrail_stream_handler is None:
            self._guardrail_stream_handler = _GuardrailStreamHandler(self, trajectory)

    def _enqueue_llm_stream_chunk(self, trajectory: Trajectory, payload: dict[str, Any]) -> None:
        if self._event_callback is None:
            return
        if not self._guardrails_enabled() or not self._guardrails_enforced():
            self._emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=payload["ts"],
                    trajectory_step=payload["trajectory_step"],
                    extra=payload["extra"],
                )
            )
            return
        self._ensure_guardrail_stream_handler(trajectory)
        if self._guardrail_stream_handler is not None:
            self._guardrail_stream_handler.enqueue(payload)

    async def _emit_guardrailed_llm_stream_chunk(self, trajectory: Trajectory, payload: dict[str, Any]) -> None:
        from .guardrails.utils import apply_redactions_to_text

        text = payload["extra"].get("text", "")
        event = self._build_guardrail_event(
            event_type="llm_stream_chunk",
            text_content=text,
            payload={"phase": payload["extra"].get("phase"), "channel": payload["extra"].get("channel")},
            trajectory=trajectory,
        )
        decision = await self._evaluate_guardrail(event)
        if decision and decision.action.value == "STOP":
            self._guardrail_stream_decision = decision
            return
        if decision and decision.action.value == "PAUSE":
            self._guardrail_stream_decision = decision
            return
        if decision and decision.action.value == "REDACT" and decision.redactions:
            redacted = apply_redactions_to_text(text, decision.redactions)
            payload = dict(payload)
            extra = dict(payload["extra"])
            extra["text"] = redacted
            payload["extra"] = extra
        self._emit_event(
            PlannerEvent(
                event_type="llm_stream_chunk",
                ts=payload["ts"],
                trajectory_step=payload["trajectory_step"],
                extra=payload["extra"],
            )
        )

    async def _finalize_guardrail_stream(self) -> None:
        if self._guardrail_stream_handler is None:
            return
        handler = self._guardrail_stream_handler
        self._guardrail_stream_handler = None
        await handler.close()

    def _register_resource_callbacks(self) -> None:
        """Wire ToolNode resource update callbacks into planner events."""
        seen: set[int] = set()
        for spec in self._specs:
            extra = spec.extra
            if not isinstance(extra, Mapping):
                continue
            tool_node = extra.get("tool_node")
            if tool_node is None:
                continue
            tool_node_id = id(tool_node)
            if tool_node_id in seen:
                continue
            seen.add(tool_node_id)

            set_callback = getattr(tool_node, "set_resource_updated_callback", None)
            if not callable(set_callback):
                continue

            namespace = extra.get("namespace")
            if not namespace:
                namespace = getattr(getattr(tool_node, "config", None), "name", "unknown")

            def _emit_resource_update(uri: str, *, _namespace: str = str(namespace)) -> None:
                step = len(self._active_trajectory.steps) if self._active_trajectory else 0
                self._emit_event(
                    PlannerEvent(
                        event_type="resource_updated",
                        ts=self._time_source(),
                        trajectory_step=step,
                        extra={"uri": uri, "namespace": _namespace},
                    )
                )

            set_callback(_emit_resource_update)

    def _record_arg_event(
        self,
        trajectory: Trajectory,
        *,
        event_type: str,
        spec: NodeSpec,
        error_summary: str | None,
        placeholders: Sequence[str],
        placeholder_paths: Sequence[str],
        autofilled_fields: Sequence[str],
        source: str,
    ) -> None:
        metadata = trajectory.metadata
        key = "invalid_args" if event_type == "planner_args_invalid" else "suspect_args"
        entries = metadata.get(key)
        if not isinstance(entries, list):
            entries = []
            metadata[key] = entries

        entry = {
            "step": len(trajectory.steps),
            "tool": spec.name,
            "error_summary": error_summary,
            "placeholders": list(placeholders),
            "placeholder_paths": list(placeholder_paths),
            "autofilled_fields": list(autofilled_fields),
            "source": source,
            "ts": self._time_source(),
        }
        entries.append(entry)

        count_key = "args_invalid_count" if event_type == "planner_args_invalid" else "args_suspect_count"
        metadata[count_key] = int(metadata.get(count_key, 0)) + 1

        if event_type == "planner_args_invalid":
            metadata["consecutive_arg_failures"] = int(metadata.get("consecutive_arg_failures", 0)) + 1
            # Also track per-tool failures so one tool's repeated failures
            # aren't reset by successful calls to other tools
            per_tool_key = f"consecutive_arg_failures_{spec.name}"
            metadata[per_tool_key] = int(metadata.get(per_tool_key, 0)) + 1
            if autofilled_fields:
                # Track both global (for metadata reporting) and per-tool (for threshold checks)
                metadata["autofill_rejection_count"] = int(metadata.get("autofill_rejection_count", 0)) + 1
                autofill_per_tool_key = f"autofill_rejection_count_{spec.name}"
                metadata[autofill_per_tool_key] = int(metadata.get(autofill_per_tool_key, 0)) + 1

        self._emit_event(
            PlannerEvent(
                event_type=event_type,
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={
                    "tool": spec.name,
                    "error_summary": error_summary,
                    "placeholders": list(placeholders),
                    "placeholder_paths": list(placeholder_paths),
                    "autofilled_fields": list(autofilled_fields),
                    "source": source,
                },
            )
        )

    def _apply_arg_validation(
        self,
        trajectory: Trajectory,
        *,
        spec: NodeSpec,
        action: PlannerAction,
        parsed_args: BaseModel,
        autofilled_fields: Sequence[str],
    ) -> str | None:
        return _apply_arg_validation_impl(
            trajectory=trajectory,
            spec=spec,
            action=action,
            parsed_args=parsed_args,
            autofilled_fields=autofilled_fields,
            record_arg_event=self._record_arg_event,
        )

    def _extract_field_descriptions(self, spec: NodeSpec) -> dict[str, str]:
        return _extract_field_descriptions_impl(spec)

    def _is_arg_fill_eligible(
        self,
        spec: NodeSpec,
        missing_fields: Sequence[str],
        trajectory: Trajectory,
    ) -> bool:
        return _is_arg_fill_eligible_impl(
            spec,
            missing_fields,
            trajectory,
            arg_fill_enabled=self._arg_fill_enabled,
        )

    def _parse_arg_fill_response(
        self,
        raw: str,
        expected_fields: Sequence[str],
    ) -> dict[str, Any] | None:
        return _parse_arg_fill_response_impl(raw, expected_fields)

    async def _attempt_arg_fill(
        self,
        trajectory: Trajectory,
        spec: NodeSpec,
        action: PlannerAction,
        missing_fields: list[str],
    ) -> dict[str, Any] | None:
        return await _attempt_arg_fill_impl(
            trajectory=trajectory,
            spec=spec,
            action=action,
            missing_fields=missing_fields,
            build_messages=self._build_messages,
            client=self._client,
            cost_tracker=self._cost_tracker,
            emit_event=self._emit_event,
            time_source=self._time_source,
        )

    async def _attempt_finish_repair(
        self,
        trajectory: Trajectory,
        action: PlannerAction,
        *,
        action_seq: int,
    ) -> str | None:
        return await _attempt_finish_repair_impl(
            trajectory=trajectory,
            action=action,
            build_messages=self._build_messages,
            client=self._client,
            cost_tracker=self._cost_tracker,
            emit_event=self._emit_event,
            time_source=self._time_source,
            system_prompt_extra=self._system_prompt_extra,
            action_seq=action_seq,
        )

    async def _attempt_graceful_failure(
        self,
        trajectory: Trajectory,
        *,
        action_seq: int,
    ) -> str | None:
        """Attempt to get a user-friendly message when hitting failure thresholds."""
        return await _attempt_graceful_failure_impl(
            trajectory=trajectory,
            build_messages=self._build_messages,
            client=self._client,
            cost_tracker=self._cost_tracker,
            emit_event=self._emit_event,
            time_source=self._time_source,
            system_prompt_extra=self._system_prompt_extra,
            action_seq=action_seq,
        )

    def _parse_finish_repair_response(self, raw: str) -> str | None:
        return _parse_finish_repair_response_impl(raw)

    def _record_invalid_response(
        self,
        trajectory: Trajectory,
        *,
        attempt: int,
        raw: str,
        error: ValidationError,
        salvage_action: PlannerAction | None,
        will_retry: bool,
    ) -> None:
        _record_invalid_response_impl(
            trajectory=trajectory,
            attempt=attempt,
            raw=raw,
            error=error,
            salvage_action=salvage_action,
            will_retry=will_retry,
            time_source=self._time_source,
            emit_event=self._emit_event,
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
        # Safely serialize contexts - they may contain non-JSON-serializable objects
        llm_context_safe: dict[str, Any] | None = None
        if trajectory.llm_context is not None:
            try:
                llm_context_safe = json.loads(json.dumps(dict(trajectory.llm_context), ensure_ascii=False))
            except (TypeError, ValueError):
                llm_context_safe = None
        tool_context_safe: dict[str, Any] | None = None
        if trajectory.tool_context is not None:
            try:
                tool_context_safe = json.loads(json.dumps(dict(trajectory.tool_context), ensure_ascii=False))
            except (TypeError, ValueError):
                tool_context_safe = None

        metadata: dict[str, Any] = {
            "reason": reason,
            "thought": thought,
            "steps": trajectory.to_history(),
            "step_count": len(trajectory.steps),
            "artifacts": dict(trajectory.artifacts),
            "sources": list(trajectory.sources),
            "llm_context": llm_context_safe or {},
            "tool_context": tool_context_safe or {},
        }
        metadata["cost"] = self._cost_tracker.snapshot()
        if constraints is not None:
            metadata["constraints"] = constraints.snapshot()
        if error is not None:
            metadata["error"] = error
        if metadata_extra:
            metadata.update(metadata_extra)
        if trajectory.metadata:
            metadata["trajectory_metadata"] = dict(trajectory.metadata)

        metadata["validation_failures_count"] = int(trajectory.metadata.get("validation_failures_count", 0))
        metadata["repair_attempts"] = int(trajectory.metadata.get("repair_attempts", 0))
        metadata["salvage_used"] = bool(trajectory.metadata.get("salvage_used", False))
        metadata["args_invalid_count"] = int(trajectory.metadata.get("args_invalid_count", 0))
        metadata["args_suspect_count"] = int(trajectory.metadata.get("args_suspect_count", 0))
        metadata["consecutive_arg_failures"] = int(trajectory.metadata.get("consecutive_arg_failures", 0))
        metadata["autofill_rejection_count"] = int(trajectory.metadata.get("autofill_rejection_count", 0))
        metadata["arg_fill_success_count"] = int(trajectory.metadata.get("arg_fill_success_count", 0))
        metadata["arg_fill_failure_count"] = int(trajectory.metadata.get("arg_fill_failure_count", 0))
        metadata["finish_repair_success_count"] = int(trajectory.metadata.get("finish_repair_success_count", 0))
        metadata["finish_repair_failure_count"] = int(trajectory.metadata.get("finish_repair_failure_count", 0))
        # Used by streaming UIs to correlate the final done event with the
        # step_start action_seq that began the finishing step.
        metadata["answer_action_seq"] = self._action_seq

        # Accumulate repair counts for tiered guidance in future runs
        # These persist in the planner instance across runs
        self._finish_repair_history_count += int(metadata.get("finish_repair_success_count", 0) or 0)
        self._arg_fill_repair_history_count += int(metadata.get("arg_fill_success_count", 0) or 0)

        # Emit finish event
        extra_data: dict[str, Any] = {
            "reason": reason,
            "cost": metadata["cost"],
            "answer_action_seq": self._action_seq,
        }
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
    "FinalPayload",
    "ReactPlanner",
    "_sanitize_json_schema",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
]
