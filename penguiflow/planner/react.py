"""JSON-only ReAct planner loop with pause/resume and summarisation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

from ..artifacts import ArtifactScope, ArtifactStore
from ..catalog import NodeSpec, build_catalog
from ..registry import ModelRegistry
from . import prompts
from .llm import (
    _estimate_size,
    _sanitize_json_schema,
    build_messages,
    critique_answer,
    generate_clarification,
    request_revision,
    summarise_trajectory,
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
    SkillsConfig,
    ToolExamplesConfig,
    ToolPolicy,
    ToolSearchConfig,
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
from .payload_builders import (
    _truncate_observation_preserving_structure as _truncate_preserving_impl,
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
from .trajectory import Trajectory, TrajectoryStep, TrajectorySummary
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
    _ensure_structured_final as _ensure_structured_final_impl,
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
    from pydantic import BaseModel, ValidationError

    from ..llm import ModelFallbackConfig
    from ..llm.types import ContentPart, ReasoningDisplay
    from ..node import Node
    from ..skills.provider import SkillProvider, SkillProviderFactory
    from ..state import StateStore
    from ..steering import SteeringInbox
    from .artifact_handling import (
        _ArtifactCollector,
        _SourceCollector,
    )
    from .artifact_registry import ArtifactRegistry
    from .constraints import _ConstraintTracker, _CostTracker
    from .context import PlannerPauseReason
    from .error_recovery import ErrorRecoveryConfig
    from .guardrails import (
        GuardrailContext,
        GuardrailDecision,
        GuardrailEvent,
        GuardrailGateway,
    )
    from .hints import _PlanningHints
    from .llm_context_hooks import LLMContextHook
    from .memory import (
        ConversationTurn,
        MemoryKey,
        ShortTermMemory,
        ShortTermMemoryConfig,
    )
    from .pause import _PauseRecord
    from .tool_search_cache import ToolSearchCache

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
            await self._planner.emit_guardrailed_llm_stream_chunk(
                self._trajectory, payload
            )


def _last_assistant_text(trajectory: Trajectory) -> str | None:
    """Return the last assistant output the guardrails should see."""
    summary = trajectory.summary
    if summary and summary.last_output_digest:
        return summary.last_output_digest
    if not trajectory.steps:
        return None
    last_step = trajectory.steps[-1]
    if last_step.action.is_terminal():
        answer = last_step.action.answer_text()
        return answer if isinstance(answer, str) and answer.strip() else None
    observation = last_step.observation
    if isinstance(observation, str) and observation.strip():
        return observation
    if isinstance(observation, Mapping):
        for key in ("answer", "raw_answer", "summary"):
            value = observation.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _task_scope(trajectory: Trajectory) -> str | None:
    """Return the task (or agent) scope from the tool or LLM context."""
    for ctx in (trajectory.tool_context, trajectory.llm_context):
        if isinstance(ctx, Mapping):
            scope = ctx.get("task_scope") or ctx.get("agent_scope")
            if isinstance(scope, str) and scope.strip():
                return scope.strip()
    return None


def _conversation_history(
    trajectory: Trajectory, history_turns: int
) -> list[dict[str, str]]:
    """Return the last ``history_turns`` user/assistant turns from memory."""
    if history_turns <= 0 or not isinstance(trajectory.llm_context, Mapping):
        return []
    conversation_memory = trajectory.llm_context.get("conversation_memory")
    if not isinstance(conversation_memory, Mapping):
        return []
    recent_turns = conversation_memory.get("recent_turns")
    if not isinstance(recent_turns, list):
        return []
    history: list[dict[str, str]] = []
    for item in recent_turns[-history_turns:]:
        if not isinstance(item, Mapping):
            continue
        user = item.get("user")
        assistant = item.get("assistant")
        if isinstance(user, str) and isinstance(assistant, str):
            history.append({"user": user, "assistant": assistant})
    return history


def _ordering_violation(
    hints: _PlanningHints, trajectory: Trajectory, node_name: str
) -> str | None:
    """Warn once when a tool runs out of the hinted order."""
    state = trajectory.hint_state.setdefault(
        "ordering_state",
        {"completed": [], "warned": False},
    )
    completed = state.setdefault("completed", [])
    expected_index = len(completed)
    if expected_index >= len(hints.ordering_hints):
        return None
    expected_node = hints.ordering_hints[expected_index]
    if (
        node_name != expected_node
        and node_name in hints.ordering_hints
        and not state.get("warned", False)
    ):
        state["warned"] = True
        return prompts.render_ordering_hint_violation(
            hints.ordering_hints,
            node_name,
        )
    return None


class ReactPlanner:
    """JSON-only ReAct planner for autonomous multi-step workflows.

    The ReactPlanner orchestrates a loop where an LLM selects and sequences
    PenguiFlow nodes/tools based on structured JSON contracts. It supports
    pause/resume for approvals, adaptive re-planning on failures, parallel
    execution, and trajectory compression for long-running sessions.

    Thread Safety:
        NOT thread-safe. Create separate planner instances per task.

    Args:
        llm: LiteLLM model name (e.g., "gpt-4") or config dict. Required if
            llm_client is not provided.
        nodes: Sequence of PenguiFlow nodes to make available as tools. Either
            (nodes + registry) or catalog must be provided.
        catalog: Pre-built tool catalog. If provided, nodes and registry are
            ignored.
        registry: Model registry for type resolution. Required if nodes is
            provided.
        llm_client: Custom LLM client implementation. If provided, llm is
            ignored.
        max_iters: Maximum planning iterations before returning no_path.
            Default: 8.
        temperature: LLM sampling temperature. Default: 0.0 for deterministic
            output.
        json_schema_mode: Enable strict JSON schema enforcement via LLM
            response_format. Default: True.
        system_prompt_extra: Optional instructions for interpreting custom
            context (e.g., memory format). Use this to specify how the planner
            should use structured data passed via llm_context. The library
            provides baseline injection; this parameter lets you define
            format-specific semantics. Examples:

            - "memories contains JSON with user preferences; respect them when
              planning"
            - "context.knowledge is a flat list of facts; cite relevant ones"
            - "Use context.history to avoid repeating failed approaches"
        token_budget: If set, triggers trajectory summarization when history
            exceeds limit. Token count is estimated by character length
            (approx).
        pause_enabled: Allow nodes to trigger pause/resume flow. Default: True.
        state_store: Optional durable state adapter for pause/resume
            persistence.
        summarizer_llm: Separate (cheaper) LLM for trajectory compression.
            Falls back to main LLM if not set.
        reflection_config: Optional configuration enabling automatic answer
            critique before finishing. Disabled by default.
        reflection_llm: Optional LiteLLM identifier used for critique when
            ``reflection_config.use_separate_llm`` is ``True``.
        planning_hints: Structured constraints and preferences (ordering,
            disallowed nodes, max_parallel, etc.). See plan.md for schema.
        tool_policy: Optional runtime policy that filters the tool catalog
            (whitelists, blacklists, or tag requirements) for multi-tenant and
            safety use cases.
        repair_attempts: Max attempts to repair invalid JSON from LLM. Default:
            3.
        max_consecutive_arg_failures: Max consecutive tool arg validation
            failures before forcing a finish with requires_followup=True. Helps
            small models avoid infinite loops when they repeatedly produce
            invalid args. Default: 3.
        arg_fill_enabled: Enable arg-fill mode for missing tool arguments. When
            True, if a tool call has valid tool selection but missing/invalid
            args, the planner will make a simplified LLM call asking only for
            the missing values instead of requiring a full JSON repair. This
            significantly improves success rates for small models. Default:
            True.
        deadline_s: Wall-clock deadline for planning session (seconds from
            start).
        hop_budget: Maximum tool invocations allowed.
        time_source: Override time.monotonic for testing.
        event_callback: Optional callback receiving PlannerEvent instances for
            observability.
        llm_timeout_s: Per-LLM-call timeout in seconds. Default: 360.0.
        llm_max_retries: Max retry attempts for transient LLM failures.
            Default: 3.
        absolute_max_parallel: System-level safety limit on parallel execution
            regardless of hints. Default: 50.

    Raises:
        ValueError: If neither (nodes + registry) nor catalog is provided, or
            if neither llm nor llm_client is provided.
        RuntimeError: If LiteLLM is not installed and llm_client is not
            provided.

    Example:
        >>> planner = ReactPlanner(
        ...     llm="gpt-4",
        ...     nodes=[triage_node, retrieve_node, summarize_node],
        ...     registry=my_registry,
        ...     max_iters=10,
        ... )
        >>> result = await planner.run("Explain PenguiFlow's architecture")
        >>> print(result.reason)  # "answer_complete", "no_path", ...
    """

    # Default system-level safety limit for parallel execution
    DEFAULT_MAX_PARALLEL = 50
    absolute_max_parallel: int
    action_schema: Mapping[str, Any]
    action_seq: int
    active_tracker: _ConstraintTracker | None
    active_trajectory: Trajectory | None
    arg_fill_enabled: bool
    artifact_registry: ArtifactRegistry
    _artifact_store: ArtifactStore
    catalog_records: list[dict[str, Any]]
    tool_aliases: dict[str, str]
    clarification_client: JSONLLMClient | None
    client: JSONLLMClient
    cost_tracker: _CostTracker
    deadline_s: float | None
    event_callback: PlannerEventCallback | None
    event_buffer: list[PlannerEvent]
    hop_budget: int | None
    _llm_context_hooks: list[LLMContextHook]
    json_schema_mode: bool
    max_consecutive_arg_failures: int
    max_iters: int
    memory_by_key: dict[str, ShortTermMemory]
    memory_config: ShortTermMemoryConfig
    memory_ephemeral_key: MemoryKey | None
    memory_singleton: ShortTermMemory | None
    memory_summarizer: (
        Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None
    )
    memory_summarizer_client: JSONLLMClient | None
    observation_guardrail: ObservationGuardrailConfig
    pause_enabled: bool
    pause_records: dict[str, _PauseRecord]
    planning_hints: _PlanningHints
    ready_answer_seq: int | None
    reflection_client: JSONLLMClient | None
    reflection_config: ReflectionConfig | None
    repair_attempts: int
    response_format: Mapping[str, Any] | None
    spec_by_name: dict[str, NodeSpec]
    specs: list[NodeSpec]
    state_store: StateStore | None
    stream_final_response: bool
    final_response_model: type[BaseModel] | None
    final_response_retries: int
    final_response_schema: dict[str, Any] | None
    tool_call_mode: str
    llm_transport: str | None
    summarizer_client: JSONLLMClient | None
    system_prompt: str
    system_prompt_extra: str | None
    finish_repair_history_count: int
    arg_fill_repair_history_count: int
    multi_action_history_count: int
    time_source: Callable[[], float]
    token_budget: int | None
    tool_policy: ToolPolicy | None
    tool_search_cache: ToolSearchCache | None
    tool_search_config: ToolSearchConfig
    tool_search_max_results: int
    tool_visibility_allowed_names: set[str] | None
    active_tool_names: set[str] | None
    session_tool_activations: dict[str, set[str]]
    execution_specs: list[NodeSpec]
    execution_spec_by_name: dict[str, NodeSpec]
    tool_examples_config: ToolExamplesConfig | None
    skills_config: SkillsConfig | None
    skills_provider: SkillProvider | None
    background_tasks: BackgroundTasksConfig
    multi_action_sequential: bool
    multi_action_read_only_only: bool
    multi_action_max_tools: int
    auto_seq_enabled: bool
    auto_seq_execute: bool
    auto_seq_read_only_only: bool
    use_native_reasoning: bool
    reasoning_effort: str | None
    reasoning_display: str | None
    guardrail_gateway: GuardrailGateway | None
    guardrail_context: GuardrailContext | None
    guardrail_run_id: str | None
    init_kwargs: dict[str, Any] | None
    steering: SteeringInbox | None
    action_format: str
    error_recovery_config: ErrorRecoveryConfig | None
    guardrail_conversation_history_turns: int
    render_component_failure_history_count: int
    use_native_llm: bool
    _guardrail_stream_handler: _GuardrailStreamHandler | None
    guardrail_stream_decision: GuardrailDecision | None
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
        state_store: StateStore | None = None,
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
        llm_timeout_s: float = 360.0,
        llm_max_retries: int = 3,
        use_native_reasoning: bool = True,
        reasoning_effort: str | None = None,
        reasoning_display: ReasoningDisplay = None,
        llm_fallback: ModelFallbackConfig | None = None,
        absolute_max_parallel: int = 50,
        reflection_config: ReflectionConfig | None = None,
        reflection_llm: str | Mapping[str, Any] | None = None,
        tool_policy: ToolPolicy | None = None,
        stream_final_response: bool = False,
        final_response_model: type[BaseModel] | None = None,
        final_response_retries: int = 1,
        tool_call_mode: str = "prompted",
        llm_transport: str | None = None,
        short_term_memory: ShortTermMemory
        | ShortTermMemoryConfig
        | None = None,
        background_tasks: BackgroundTasksConfig | None = None,
        error_recovery: ErrorRecoveryConfig | None = None,
        multi_action_sequential: bool = False,
        multi_action_read_only_only: bool = True,
        multi_action_max_tools: int = 2,
        auto_seq_enabled: bool = False,
        auto_seq_execute: bool = False,
        auto_seq_read_only_only: bool = True,
        use_native_llm: bool = False,
        guardrail_gateway: GuardrailGateway | None = None,
        guardrail_conversation_history_turns: int = 1,
        tool_search: ToolSearchConfig | None = None,
        tool_examples: ToolExamplesConfig | None = None,
        skills: SkillsConfig | None = None,
        skills_provider: SkillProvider | None = None,
        skills_provider_factory: SkillProviderFactory | None = None,
        llm_context_hooks: Sequence[LLMContextHook] | None = None,
    ) -> None:
        """Create a planner; :func:`init_react_planner` has the arguments."""
        # NOTE: ReactPlanner has mutable per-run state and is not safe to call
        # concurrently on a single instance. We provide an internal hotfix path
        # that serializes calls per session_id while allowing concurrency
        # across sessions by routing execution to per-session forked planners.
        # This avoids API changes for downstream orchestrators that reuse a
        # single ReactPlanner.
        self._session_dispatch_enabled = True
        self._session_registry_lock = asyncio.Lock()
        self._session_locks = {}
        self._session_planners = {}
        self._fallback_run_lock = asyncio.Lock()

        # Store init kwargs so the planner can be safely forked for background
        # tasks. This is intentionally best-effort and uses references for
        # non-serialisable objects.
        self.init_kwargs = {
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
            "planning_hints": dict(planning_hints)
            if isinstance(planning_hints, Mapping)
            else planning_hints,
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
            "reasoning_display": reasoning_display,
            "llm_fallback": llm_fallback,
            "absolute_max_parallel": absolute_max_parallel,
            "reflection_config": reflection_config,
            "reflection_llm": reflection_llm,
            "tool_policy": tool_policy,
            "stream_final_response": stream_final_response,
            "final_response_model": final_response_model,
            "final_response_retries": final_response_retries,
            "tool_call_mode": tool_call_mode,
            "llm_transport": llm_transport,
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
            "guardrail_conversation_history_turns": (
                guardrail_conversation_history_turns
            ),
            "tool_search": tool_search,
            "tool_examples": tool_examples,
            "skills": skills,
            "skills_provider": skills_provider,
            "skills_provider_factory": skills_provider_factory,
            "llm_context_hooks": list(llm_context_hooks or []),
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
            reasoning_display=reasoning_display,
            llm_fallback=llm_fallback,
            absolute_max_parallel=absolute_max_parallel,
            reflection_config=reflection_config,
            reflection_llm=reflection_llm,
            tool_policy=tool_policy,
            stream_final_response=stream_final_response,
            final_response_model=final_response_model,
            final_response_retries=final_response_retries,
            tool_call_mode=tool_call_mode,
            llm_transport=llm_transport,
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
            tool_search_config=tool_search,
            tool_examples_config=tool_examples,
            skills_config=skills,
            skills_provider=skills_provider,
            skills_provider_factory=skills_provider_factory,
        )
        self._guardrail_stream_handler = None
        self.guardrail_stream_decision = None
        self._llm_context_hooks = list(llm_context_hooks or [])

    def fork(
        self,
        *,
        catalog_filter: Callable[[NodeSpec], bool] | None = None,
        tool_policy: ToolPolicy | None = None,
        inherit_policy: bool = True,
        background_tasks: BackgroundTasksConfig
        | None
        | Literal["inherit"] = "inherit",
    ) -> ReactPlanner:
        """Create a new planner instance with the same configuration.

        Background task orchestration requires a fresh ReactPlanner per task
        because the planner maintains mutable per-run state and is not
        thread-safe.

        Args:
            catalog_filter: Optional predicate applied to each NodeSpec to
                restrict which tools the forked planner exposes.
            tool_policy: Optional runtime policy applied to the forked planner.
                When ``inherit_policy`` is True it is intersected/merged with
                the parent's policy.
            inherit_policy: When True, merge ``tool_policy`` with the parent
                planner's policy (intersecting allowed tools, unioning denied
                tools and required tags). When False, ``tool_policy`` replaces
                the parent's policy outright. Default: True.
            background_tasks: Background tasks configuration for the forked
                planner. Defaults to ``"inherit"``, which reuses the parent's
                configuration.

        Returns:
            A new ReactPlanner instance sharing this planner's configuration.
        """
        init_kwargs = dict(self.init_kwargs or {})
        if tool_policy is not None:
            base_policy = init_kwargs.get("tool_policy")
            if inherit_policy and isinstance(base_policy, ToolPolicy):
                allowed: set[str] | None
                if base_policy.allowed_tools is None:
                    allowed = tool_policy.allowed_tools
                elif tool_policy.allowed_tools is None:
                    allowed = base_policy.allowed_tools
                else:
                    allowed = (
                        base_policy.allowed_tools & tool_policy.allowed_tools
                    )
                init_kwargs["tool_policy"] = ToolPolicy(
                    allowed_tools=allowed,
                    denied_tools=set(base_policy.denied_tools)
                    | set(tool_policy.denied_tools),
                    require_tags=set(base_policy.require_tags)
                    | set(tool_policy.require_tags),
                )
            else:
                init_kwargs["tool_policy"] = tool_policy
        base_catalog = init_kwargs.get("catalog")
        nodes = init_kwargs.get("nodes")
        registry = init_kwargs.get("registry")

        specs: list[NodeSpec]
        if isinstance(base_catalog, Sequence):
            specs = list(base_catalog)
        elif isinstance(nodes, Sequence) and isinstance(
            registry, ModelRegistry
        ):
            specs = list(build_catalog(nodes, registry))
        else:
            # Best-effort fallback for legacy planners constructed before
            # _init_kwargs existed.
            specs = list(getattr(self, "specs", []) or [])

        if catalog_filter is not None:
            specs = [spec for spec in specs if catalog_filter(spec)]

        # Force catalog-based init to avoid depending on registry merging
        # behaviour.
        init_kwargs["catalog"] = specs
        init_kwargs["nodes"] = None
        init_kwargs["registry"] = None
        init_kwargs["event_callback"] = None
        if background_tasks != "inherit":
            init_kwargs["background_tasks"] = background_tasks

        child = ReactPlanner(**init_kwargs)
        # Child planners created for background tasks and per-session dispatch
        # should not themselves perform session dispatch; the dispatch happens
        # at the owning/root planner.
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

    async def _get_or_create_session_runtime(
        self, session_id: str
    ) -> tuple[asyncio.Lock, ReactPlanner]:
        async with self._session_registry_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            planner = self._session_planners.get(session_id)
            if planner is None:
                planner = self.fork()
                # Preserve legacy behaviour where memory state is observable on
                # the original planner instance. Per-session planners share the
                # same memory map so callers that reuse a single ReactPlanner
                # can still access `_memory_by_key` after a run.
                planner.memory_by_key = self.memory_by_key
                self._session_planners[session_id] = planner
            return lock, planner

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the configured artifact store (NoOp when disabled)."""
        return self._artifact_store

    @artifact_store.setter
    def artifact_store(self, store: ArtifactStore) -> None:
        self._artifact_store = store

    async def run(
        self,
        query: str,
        *,
        input_parts: Sequence[ContentPart] | None = None,
        llm_context: Mapping[str, Any] | None = None,
        context_meta: Mapping[str, Any] | None = None,  # Deprecated
        tool_context: Mapping[str, Any] | None = None,
        memory_key: MemoryKey | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Execute planner on a query until completion or pause.

        Args:
            query: Natural language task description.
            input_parts: Optional image/audio content parts appended to the
                initial user message. Omitted by default to preserve text-only
                behavior.
            llm_context: Optional context visible to LLM (memories,
                status_history, etc.). Should NOT include internal metadata
                like tenant_id or trace_id.
            context_meta: **Deprecated**: Use llm_context instead. This
                parameter is kept for backward compatibility but will be
                removed in a future version.
            tool_context: Tool-only context (callbacks, loggers, telemetry
                objects). Not visible to the LLM. May contain non-serialisable
                objects.
            memory_key: Optional explicit short-term memory key. If omitted,
                the planner may derive a key from `tool_context` using the
                configured memory isolation paths. If no key is available and
                memory is configured to require an explicit key, memory behaves
                as disabled for this call.
            steering: Optional steering inbox for injecting mid-run guidance
                into the planning loop.
            tool_visibility: Optional policy restricting which tools are
                visible for this call.

        Returns:
            PlannerFinish if task completed/failed, PlannerPause if paused for
            human intervention.

        Raises:
            RuntimeError: If LLM client fails after all retries.
        """
        session_id = None
        if self._session_dispatch_enabled:
            session_id = self._extract_session_id(
                tool_context=tool_context, memory_key=memory_key
            )

        if session_id:
            (
                session_lock,
                session_planner,
            ) = await self._get_or_create_session_runtime(session_id)
            previous_callback = getattr(
                session_planner, "event_callback", None
            )
            session_planner.event_callback = getattr(
                self, "event_callback", None
            )
            try:
                async with session_lock:
                    return await session_planner.run(
                        query,
                        input_parts=input_parts,
                        llm_context=llm_context,
                        context_meta=context_meta,
                        tool_context=tool_context,
                        memory_key=memory_key,
                        steering=steering,
                        tool_visibility=tool_visibility,
                    )
            finally:
                session_planner.event_callback = previous_callback

        # Fallback: serialize when no session_id is provided to avoid
        # corrupting shared mutable state.
        async with self._fallback_run_lock:
            previous = getattr(self, "steering", None)
            self.steering = steering
            try:
                return await _run_impl(
                    self,
                    query,
                    input_parts=input_parts,
                    llm_context=llm_context,
                    context_meta=context_meta,
                    tool_context=tool_context,
                    memory_key=memory_key,
                    tool_visibility=tool_visibility,
                )
            finally:
                self.steering = previous

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

        Args:
            token: Resume token from a previous PlannerPause.
            user_input: Optional user response to the pause (e.g., approval
                decision).
            tool_context: Tool-only context (callbacks, loggers, telemetry
                objects). Not visible to the LLM. May contain non-serialisable
                objects. Overrides any tool_context captured in the pause
                record.
            memory_key: Optional explicit short-term memory key for the resumed
                session. If omitted, the planner may derive a key from
                `tool_context` using the configured memory isolation paths. If
                no key is available and memory is configured to require an
                explicit key, memory behaves as disabled for this call.
            steering: Optional steering inbox for injecting mid-run guidance
                into the planning loop.
            tool_visibility: Optional policy restricting which tools are
                visible for this call.

        Returns:
            Updated result after resuming execution.

        Raises:
            KeyError: If resume token is invalid or expired.
        """
        session_id = None
        if self._session_dispatch_enabled:
            session_id = self._extract_session_id(
                tool_context=tool_context, memory_key=memory_key
            )

        if session_id:
            (
                session_lock,
                session_planner,
            ) = await self._get_or_create_session_runtime(session_id)
            previous_callback = getattr(
                session_planner, "event_callback", None
            )
            session_planner.event_callback = getattr(
                self, "event_callback", None
            )
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
                session_planner.event_callback = previous_callback

        async with self._fallback_run_lock:
            previous = getattr(self, "steering", None)
            self.steering = steering
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
                self.steering = previous

    def resolve_memory_key(
        self,
        explicit: MemoryKey | None,
        tool_context: Mapping[str, Any] | None,
    ) -> MemoryKey | None:
        """Return a run's memory key, explicit or from the tool context."""
        return _resolve_memory_key(self, explicit, tool_context)

    def _get_memory_for_key(self, key: MemoryKey) -> ShortTermMemory | None:
        return _get_memory_for_key(self, key)

    def _get_short_term_memory_summarizer(
        self,
    ) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
        return _get_short_term_memory_summarizer(self)

    def _extract_memory_key_from_tool_context(
        self, tool_context: Mapping[str, Any]
    ) -> MemoryKey | None:
        return _extract_memory_key_from_tool_context(self, tool_context)

    async def apply_memory_context(
        self,
        llm_context: dict[str, Any] | None,
        key: MemoryKey | None,
    ) -> dict[str, Any] | None:
        """Merge the short-term memory of ``key`` into the llm_context."""
        return await _apply_memory_context(self, llm_context, key)

    async def _maybe_memory_hydrate(
        self, memory: ShortTermMemory, key: MemoryKey
    ) -> None:
        await _maybe_memory_hydrate(self, memory, key)

    async def _maybe_memory_persist(
        self, memory: ShortTermMemory, key: MemoryKey
    ) -> None:
        await _maybe_memory_persist(self, memory, key)

    def _build_memory_turn(
        self, query: str, result: PlannerFinish, trajectory: Trajectory
    ) -> ConversationTurn:
        return _build_memory_turn(self, query, result, trajectory)

    async def maybe_record_memory_turn(
        self,
        query: str,
        result: PlannerFinish | PlannerPause,
        trajectory: Trajectory,
        key: MemoryKey | None,
    ) -> None:
        """Record the run's turn in the short-term memory of ``key``."""
        await _maybe_record_memory_turn(self, query, result, trajectory, key)

    def _check_deadline(
        self,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        last_observation: object | None,
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

    def _log_action_received(
        self, action: PlannerAction, trajectory: Trajectory
    ) -> None:
        _log_action_received_impl(action, trajectory)

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
        last_observation: object | None,
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

    async def run_loop(
        self,
        trajectory: Trajectory,
        *,
        tracker: _ConstraintTracker | None,
    ) -> PlannerFinish | PlannerPause:
        """Run the planner loop over ``trajectory``."""
        return await _run_loop_impl(
            self,
            trajectory,
            tracker=tracker,
        )

    async def step(self, trajectory: Trajectory) -> PlannerAction:
        """Ask the LLM for the next action."""
        return await _step_impl(self, trajectory)

    async def execute_parallel_plan(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
        *,
        action_seq: int,
    ) -> tuple[Any | None, PlannerPause | None]:
        """Run a parallel plan action."""
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

    async def build_messages(
        self, trajectory: Trajectory
    ) -> list[dict[str, Any]]:
        """Build the chat messages for the next LLM call."""
        return await build_messages(self, trajectory)

    def estimate_size(self, messages: Sequence[Mapping[str, Any]]) -> int:
        """Estimate the token size of ``messages``."""
        return _estimate_size(messages)

    async def _summarise_trajectory(
        self, trajectory: Trajectory
    ) -> TrajectorySummary:
        return await summarise_trajectory(self, trajectory)

    async def critique_answer(
        self,
        trajectory: Trajectory,
        candidate: object,
    ) -> ReflectionCritique:
        """Critique a candidate answer with the reflection LLM."""
        return await critique_answer(self, trajectory, candidate)

    async def request_revision(
        self,
        trajectory: Trajectory,
        critique: ReflectionCritique,
        *,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> PlannerAction:
        """Ask the LLM to revise the answer from the critique."""
        return await request_revision(
            self, trajectory, critique, on_stream_chunk=on_stream_chunk
        )

    async def generate_clarification(
        self,
        trajectory: Trajectory,
        failed_answer: object,
        critique: ReflectionCritique,
        revision_attempts: int,
    ) -> str:
        """Ask the LLM to explain why the query cannot be answered well."""
        return await generate_clarification(
            self, trajectory, failed_answer, critique, revision_attempts
        )

    def check_action_constraints(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
    ) -> str | None:
        """Return why ``action`` breaks a constraint, or ``None``."""
        hints = self.planning_hints
        node_name = action.next_node if action.is_tool_call() else None
        if node_name is not None and not tracker.has_budget_for_next_tool():
            limit = self.hop_budget if self.hop_budget is not None else 0
            return prompts.render_hop_budget_violation(limit)
        if node_name is not None and node_name in hints.disallow_nodes:
            return prompts.render_disallowed_node(node_name)
        if action.next_node == "parallel":
            violation = self._parallel_violation(action)
            if violation is not None:
                return violation
        if hints.ordering_hints and node_name is not None:
            return _ordering_violation(hints, trajectory, node_name)
        return None

    def _parallel_violation(self, action: PlannerAction) -> str | None:
        """Check a parallel plan's size and sequential-only tools."""
        hints = self.planning_hints
        steps = action.args.get("steps")
        plan_len = len(steps) if isinstance(steps, list) else 0
        # Absolute system-level safety limit
        if plan_len > self.absolute_max_parallel:
            logger.warning(
                "parallel_limit_absolute",
                extra={
                    "requested": plan_len,
                    "limit": self.absolute_max_parallel,
                },
            )
            return prompts.render_parallel_limit(self.absolute_max_parallel)
        # Hint-based limit
        if hints.max_parallel is not None and plan_len > hints.max_parallel:
            return prompts.render_parallel_limit(hints.max_parallel)
        if not hints.sequential_only or not isinstance(steps, list):
            return None
        for item in steps:
            candidate = item.get("node") if isinstance(item, Mapping) else None
            if (
                isinstance(candidate, str)
                and candidate in hints.sequential_only
            ):
                return prompts.render_sequential_only(candidate)
        return None

    def record_hint_progress(
        self, node_name: str, trajectory: Trajectory
    ) -> None:
        """Record that a tool ran, advancing the ordering hints."""
        hints = self.planning_hints
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

    def build_failure_payload(
        self, spec: NodeSpec, args: BaseModel, exc: Exception
    ) -> dict[str, Any]:
        """Build the failure payload of a tool that raised."""
        return _build_failure_payload_impl(spec, args, exc)

    async def clamp_observation(
        self,
        observation: dict[str, Any],
        spec_name: str,
        trajectory_step: int,
    ) -> tuple[dict[str, Any], bool]:
        """Clamp a large observation, storing it as an artifact if allowed."""
        scope: ArtifactScope | None = None
        traj = self.active_trajectory
        if traj is not None:
            tool_ctx = traj.tool_context
            if tool_ctx and isinstance(tool_ctx, dict):
                session_id = tool_ctx.get("session_id")
                if session_id:
                    scope = ArtifactScope(
                        session_id=str(session_id),
                        tenant_id=tool_ctx.get("tenant_id"),
                        user_id=tool_ctx.get("user_id"),
                        trace_id=tool_ctx.get("trace_id"),
                    )
        return await _clamp_observation_impl(
            observation=observation,
            spec_name=spec_name,
            trajectory_step=trajectory_step,
            config=self.observation_guardrail,
            artifact_store=self._artifact_store,
            artifact_registry=self.artifact_registry,
            active_trajectory=self.active_trajectory,
            emit_event=self.emit_event,
            time_source=self.time_source,
            scope=scope,
        )

    def _truncate_observation_preserving_structure(
        self,
        observation: dict[str, Any],
        max_total_chars: int,
        max_field_chars: int,
    ) -> dict[str, Any]:
        return _truncate_preserving_impl(
            observation,
            max_total_chars,
            max_field_chars,
            config=self.observation_guardrail,
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
            emit_event=self.emit_event,
            time_source=self.time_source,
            node_name=node_name,
            trajectory_step=trajectory_step,
            original_size=original_size,
            clamped_size=clamped_size,
            method=method,
        )

    def build_final_payload(
        self,
        args: object,
        last_observation: object,
        artifacts: Mapping[str, Any],
        sources: Sequence[Mapping[str, Any]] | None,
    ) -> FinalPayload:
        """Build the final payload of a finished run."""
        return _build_final_payload_impl(
            args=args,
            last_observation=last_observation,
            artifacts=artifacts,
            sources=sources,
        )

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> PlannerPause:
        """Pause the active run."""
        return await _pause_impl(self, reason, payload)

    async def pause_from_context(
        self,
        reason: PlannerPauseReason,
        payload: dict[str, Any],
        trajectory: Trajectory,
    ) -> PlannerPause:
        """Pause a run from a tool context; raises to unwind the tool call."""
        return await _pause_from_context_impl(
            self, reason, payload, trajectory
        )

    async def record_pause(
        self,
        pause: PlannerPause,
        trajectory: Trajectory,
        tracker: _ConstraintTracker | None,
    ) -> None:
        """Record a pause so the run can be resumed later."""
        await _record_pause_impl(self, pause, trajectory, tracker)

    async def _store_pause_record(
        self, token: str, record: _PauseRecord
    ) -> None:
        await _store_pause_record_impl(self, token, record)

    async def load_pause_record(self, token: str) -> _PauseRecord:
        """Load (and forget) the pause record of a resume token."""
        return await _load_pause_record_impl(self, token)

    def _serialise_pause_record(self, record: _PauseRecord) -> dict[str, Any]:
        return _serialise_pause_record_impl(record)

    def emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        self.event_buffer.append(event)

        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        for reserved in ("args", "msg", "levelname", "levelno", "exc_info"):
            payload.pop(reserved, None)
        log_fn = (
            logger.debug
            if event.event_type == "llm_stream_chunk"
            else logger.info
        )
        log_fn(event.event_type, extra=payload)

        # Invoke callback if provided
        if self.event_callback is not None:
            try:
                self.event_callback(event)
            except Exception:
                logger.exception(
                    "event_callback_error",
                    extra={
                        "event_type": event.event_type,
                        "step": event.trajectory_step,
                    },
                )
        self._last_event = event

    def guardrails_enabled(self) -> bool:
        """Whether a guardrail gateway is configured."""
        return (
            self.guardrail_gateway is not None
            and self.guardrail_context is not None
        )

    def guardrails_enforced(self) -> bool:
        """Whether guardrail decisions are enforced (not shadow mode)."""
        if not self.guardrails_enabled():
            return False
        mode = getattr(
            getattr(self.guardrail_gateway, "config", None), "mode", "enforce"
        )
        return mode == "enforce"

    def build_guardrail_event(
        self,
        *,
        event_type: str,
        text_content: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        trajectory: Trajectory | None = None,
    ) -> GuardrailEvent:
        """Build a guardrail event for the active run."""
        from .guardrails import GuardrailEvent

        merged_payload = dict(payload or {})
        if trajectory is not None:
            for key, value in self._guardrail_context_payload(
                trajectory
            ).items():
                merged_payload.setdefault(key, value)

        return GuardrailEvent(
            event_type=event_type,
            run_id=self.guardrail_run_id or "unknown",
            text_content=text_content,
            tool_name=tool_name,
            tool_args=tool_args,
            payload=merged_payload,
        )

    def _guardrail_context_payload(
        self, trajectory: Trajectory
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        last_assistant = _last_assistant_text(trajectory)
        if last_assistant is not None:
            payload["last_assistant"] = last_assistant
        task_scope = _task_scope(trajectory)
        if task_scope is not None:
            payload["task_scope"] = task_scope
        history_turns = int(
            getattr(self, "guardrail_conversation_history_turns", 0) or 0
        )
        payload["conversation_history"] = _conversation_history(
            trajectory, history_turns
        )
        return payload

    async def evaluate_guardrail(
        self, event: GuardrailEvent
    ) -> GuardrailDecision | None:
        """Evaluate a guardrail event, if guardrails are enabled."""
        if not self.guardrails_enabled():
            return None
        gateway = self.guardrail_gateway
        ctx = self.guardrail_context
        if gateway is None or ctx is None:
            return None
        return await gateway.evaluate(ctx, event)

    def guardrail_decision_payload(
        self, decision: GuardrailDecision
    ) -> dict[str, Any]:
        """Return a guardrail decision as a JSON-serializable dict."""
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

    def clear_guardrail_stream_decision(self) -> None:
        """Forget the stream decision before a new LLM attempt."""
        self.guardrail_stream_decision = None

    def _ensure_guardrail_stream_handler(self, trajectory: Trajectory) -> None:
        if not self.guardrails_enabled():
            return
        if self._guardrail_stream_handler is None:
            self._guardrail_stream_handler = _GuardrailStreamHandler(
                self, trajectory
            )

    def enqueue_llm_stream_chunk(
        self, trajectory: Trajectory, payload: dict[str, Any]
    ) -> None:
        """Queue an LLM stream chunk, via the guardrail stream if enabled."""
        if self.event_callback is None:
            return
        if not self.guardrails_enabled() or not self.guardrails_enforced():
            self.emit_event(
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

    async def emit_guardrailed_llm_stream_chunk(
        self, trajectory: Trajectory, payload: dict[str, Any]
    ) -> None:
        """Emit an LLM stream chunk, applying guardrail redactions."""
        from .guardrails.utils import apply_redactions_to_text

        text = payload["extra"].get("text", "")
        event = self.build_guardrail_event(
            event_type="llm_stream_chunk",
            text_content=text,
            payload={
                "phase": payload["extra"].get("phase"),
                "channel": payload["extra"].get("channel"),
            },
            trajectory=trajectory,
        )
        decision = await self.evaluate_guardrail(event)
        if decision and decision.action.value == "STOP":
            self.guardrail_stream_decision = decision
            return
        if decision and decision.action.value == "PAUSE":
            self.guardrail_stream_decision = decision
            return
        if (
            decision
            and decision.action.value == "REDACT"
            and decision.redactions
        ):
            redacted = apply_redactions_to_text(text, decision.redactions)
            payload = dict(payload)
            extra = dict(payload["extra"])
            extra["text"] = redacted
            payload["extra"] = extra
        self.emit_event(
            PlannerEvent(
                event_type="llm_stream_chunk",
                ts=payload["ts"],
                trajectory_step=payload["trajectory_step"],
                extra=payload["extra"],
            )
        )

    async def finalize_guardrail_stream(self) -> None:
        """Flush and close the guardrail stream handler."""
        if self._guardrail_stream_handler is None:
            return
        handler = self._guardrail_stream_handler
        self._guardrail_stream_handler = None
        await handler.close()

    def register_resource_callbacks(self) -> None:
        """Wire ToolNode resource update callbacks into planner events."""
        seen: set[int] = set()
        for spec in self.specs:
            tool_node = spec.extra.get("tool_node")
            if tool_node is None:
                continue
            tool_node_id = id(tool_node)
            if tool_node_id in seen:
                continue
            seen.add(tool_node_id)

            set_callback = getattr(
                tool_node, "set_resource_updated_callback", None
            )
            if not callable(set_callback):
                continue

            namespace = spec.extra.get("namespace")
            if not namespace:
                namespace = getattr(
                    getattr(tool_node, "config", None), "name", "unknown"
                )

            def _emit_resource_update(
                uri: str, *, _namespace: str = str(namespace)
            ) -> None:
                step = (
                    len(self.active_trajectory.steps)
                    if self.active_trajectory
                    else 0
                )
                self.emit_event(
                    PlannerEvent(
                        event_type="resource_updated",
                        ts=self.time_source(),
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
        key = (
            "invalid_args"
            if event_type == "planner_args_invalid"
            else "suspect_args"
        )
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
            "ts": self.time_source(),
        }
        entries.append(entry)

        count_key = (
            "args_invalid_count"
            if event_type == "planner_args_invalid"
            else "args_suspect_count"
        )
        metadata[count_key] = int(metadata.get(count_key, 0)) + 1

        if event_type == "planner_args_invalid":
            metadata["consecutive_arg_failures"] = (
                int(metadata.get("consecutive_arg_failures", 0)) + 1
            )
            # Also track per-tool failures so one tool's repeated failures
            # aren't reset by successful calls to other tools
            per_tool_key = f"consecutive_arg_failures_{spec.name}"
            metadata[per_tool_key] = int(metadata.get(per_tool_key, 0)) + 1
            if autofilled_fields:
                # Track both global (for metadata reporting) and per-tool (for
                # threshold checks)
                metadata["autofill_rejection_count"] = (
                    int(metadata.get("autofill_rejection_count", 0)) + 1
                )
                autofill_per_tool_key = f"autofill_rejection_count_{spec.name}"
                metadata[autofill_per_tool_key] = (
                    int(metadata.get(autofill_per_tool_key, 0)) + 1
                )

        self.emit_event(
            PlannerEvent(
                event_type=event_type,
                ts=self.time_source(),
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

    def apply_arg_validation(
        self,
        trajectory: Trajectory,
        *,
        spec: NodeSpec,
        action: PlannerAction,
        parsed_args: BaseModel,
        autofilled_fields: Sequence[str],
    ) -> str | None:
        """Validate tool arguments beyond their schema (placeholders, etc.)."""
        return _apply_arg_validation_impl(
            trajectory=trajectory,
            spec=spec,
            action=action,
            parsed_args=parsed_args,
            autofilled_fields=autofilled_fields,
            record_arg_event=self._record_arg_event,
        )

    def extract_field_descriptions(self, spec: NodeSpec) -> dict[str, str]:
        """Return hints describing each argument of the tool."""
        return _extract_field_descriptions_impl(spec)

    def is_arg_fill_eligible(
        self,
        spec: NodeSpec,
        missing_fields: Sequence[str],
        trajectory: Trajectory,
    ) -> bool:
        """Whether the missing fields can be filled with the LLM."""
        return _is_arg_fill_eligible_impl(
            spec,
            missing_fields,
            trajectory,
            arg_fill_enabled=self.arg_fill_enabled,
        )

    def parse_arg_fill_response(
        self,
        raw: str,
        expected_fields: Sequence[str],
    ) -> dict[str, Any] | None:
        """Parse an arg-fill response into field values."""
        return _parse_arg_fill_response_impl(raw, expected_fields)

    async def attempt_arg_fill(
        self,
        trajectory: Trajectory,
        spec: NodeSpec,
        missing_fields: list[str],
    ) -> dict[str, Any] | None:
        """Ask the LLM for the missing argument values."""
        return await _attempt_arg_fill_impl(
            trajectory=trajectory,
            spec=spec,
            missing_fields=missing_fields,
            build_messages=self.build_messages,
            client=self.client,
            cost_tracker=self.cost_tracker,
            emit_event=self.emit_event,
            time_source=self.time_source,
        )

    async def attempt_finish_repair(
        self,
        trajectory: Trajectory,
        action: PlannerAction,
        *,
        action_seq: int,
    ) -> str | None:
        """Ask the LLM for the answer a finish action left out."""
        return await _attempt_finish_repair_impl(
            trajectory=trajectory,
            action=action,
            build_messages=self.build_messages,
            client=self.client,
            cost_tracker=self.cost_tracker,
            emit_event=self.emit_event,
            time_source=self.time_source,
            system_prompt_extra=self.system_prompt_extra,
            action_seq=action_seq,
        )

    async def ensure_structured_final(
        self,
        trajectory: Trajectory,
        action: PlannerAction,
        *,
        action_seq: int,
    ) -> None:
        """Validate the finish action's structured payload, repairing it."""
        if self.final_response_model is None:
            return
        await _ensure_structured_final_impl(
            trajectory=trajectory,
            action=action,
            final_response_model=self.final_response_model,
            final_response_schema=self.final_response_schema or {},
            retries=self.final_response_retries,
            build_messages=self.build_messages,
            client=self.client,
            cost_tracker=self.cost_tracker,
            emit_event=self.emit_event,
            time_source=self.time_source,
            action_seq=action_seq,
        )

    async def attempt_graceful_failure(
        self,
        trajectory: Trajectory,
        *,
        action_seq: int,
    ) -> str | None:
        """Get a user-friendly message when hitting failure thresholds."""
        return await _attempt_graceful_failure_impl(
            trajectory=trajectory,
            build_messages=self.build_messages,
            client=self.client,
            cost_tracker=self.cost_tracker,
            emit_event=self.emit_event,
            time_source=self.time_source,
            system_prompt_extra=self.system_prompt_extra,
            action_seq=action_seq,
        )

    def parse_finish_repair_response(self, raw: str) -> str | None:
        """Parse a finish repair response into the answer."""
        return _parse_finish_repair_response_impl(raw)

    def record_invalid_response(
        self,
        trajectory: Trajectory,
        *,
        attempt: int,
        raw: str,
        error: ValidationError,
        salvage_action: PlannerAction | None,
        will_retry: bool,
    ) -> None:
        """Record an LLM response that failed action validation."""
        _record_invalid_response_impl(
            trajectory=trajectory,
            attempt=attempt,
            raw=raw,
            error=error,
            salvage_action=salvage_action,
            will_retry=will_retry,
            time_source=self.time_source,
            emit_event=self.emit_event,
        )

    def finish(
        self,
        trajectory: Trajectory,
        *,
        reason: Literal["answer_complete", "no_path", "budget_exhausted"],
        payload: object,
        thought: str,
        constraints: _ConstraintTracker | None = None,
        error: str | None = None,
        metadata_extra: Mapping[str, Any] | None = None,
    ) -> PlannerFinish:
        """Finish the run with ``payload`` as the final answer."""
        # Safely serialize contexts - they may contain non-JSON-serializable
        # objects
        llm_context_safe: dict[str, Any] | None = None
        if trajectory.llm_context is not None:
            try:
                llm_context_safe = json.loads(
                    json.dumps(
                        dict(trajectory.llm_context), ensure_ascii=False
                    )
                )
            except (TypeError, ValueError):
                llm_context_safe = None
        tool_context_safe: dict[str, Any] | None = None
        if trajectory.tool_context is not None:
            try:
                tool_context_safe = json.loads(
                    json.dumps(
                        dict(trajectory.tool_context), ensure_ascii=False
                    )
                )
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
        metadata["cost"] = self.cost_tracker.snapshot()
        if constraints is not None:
            metadata["constraints"] = constraints.snapshot()
        if error is not None:
            metadata["error"] = error
        if metadata_extra:
            metadata.update(metadata_extra)
        if trajectory.metadata:
            metadata["trajectory_metadata"] = dict(trajectory.metadata)

        metadata["validation_failures_count"] = int(
            trajectory.metadata.get("validation_failures_count", 0)
        )
        metadata["repair_attempts"] = int(
            trajectory.metadata.get("repair_attempts", 0)
        )
        metadata["salvage_used"] = bool(
            trajectory.metadata.get("salvage_used", False)
        )
        metadata["args_invalid_count"] = int(
            trajectory.metadata.get("args_invalid_count", 0)
        )
        metadata["args_suspect_count"] = int(
            trajectory.metadata.get("args_suspect_count", 0)
        )
        metadata["consecutive_arg_failures"] = int(
            trajectory.metadata.get("consecutive_arg_failures", 0)
        )
        metadata["autofill_rejection_count"] = int(
            trajectory.metadata.get("autofill_rejection_count", 0)
        )
        metadata["arg_fill_success_count"] = int(
            trajectory.metadata.get("arg_fill_success_count", 0)
        )
        metadata["arg_fill_failure_count"] = int(
            trajectory.metadata.get("arg_fill_failure_count", 0)
        )
        metadata["finish_repair_success_count"] = int(
            trajectory.metadata.get("finish_repair_success_count", 0)
        )
        metadata["finish_repair_failure_count"] = int(
            trajectory.metadata.get("finish_repair_failure_count", 0)
        )
        # Used by streaming UIs to correlate the final done event with the
        # step_start action_seq that began the finishing step.
        metadata["answer_action_seq"] = self.action_seq

        # Accumulate repair counts for tiered guidance in future runs These
        # persist in the planner instance across runs
        self.finish_repair_history_count += int(
            metadata.get("finish_repair_success_count", 0) or 0
        )
        self.arg_fill_repair_history_count += int(
            metadata.get("arg_fill_success_count", 0) or 0
        )

        # Emit finish event
        extra_data: dict[str, Any] = {
            "reason": reason,
            "cost": metadata["cost"],
            "answer_action_seq": self.action_seq,
        }
        if error:
            extra_data["error"] = error
        self.emit_event(
            PlannerEvent(
                event_type="finish",
                ts=self.time_source(),
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
    "FinalPayload",
    "JoinInjection",
    "ParallelCall",
    "ParallelJoin",
    "PlannerAction",
    "PlannerEvent",
    "PlannerEventCallback",
    "PlannerFinish",
    "PlannerPause",
    "ReactPlanner",
    "ReflectionConfig",
    "ReflectionCriteria",
    "ReflectionCritique",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
    "_sanitize_json_schema",
]
