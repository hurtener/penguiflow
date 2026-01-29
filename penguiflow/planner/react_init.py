"""ReactPlanner initialization helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from ..artifacts import ArtifactStore, NoOpArtifactStore, discover_artifact_store
from ..catalog import NodeSpec, build_catalog
from ..llm import create_native_adapter
from ..node import Node
from ..registry import ModelRegistry
from . import prompts
from .artifact_registry import ArtifactRegistry
from .constraints import _CostTracker
from .error_recovery import ErrorRecoveryConfig
from .hints import _PlanningHints
from .llm import _LiteLLMJSONClient
from .memory import ShortTermMemory, ShortTermMemoryConfig
from .memory_integration import _ShortTermMemorySummary
from .models import (
    ActionFormat,
    BackgroundTasksConfig,
    ClarificationResponse,
    JSONLLMClient,
    ObservationGuardrailConfig,
    ReflectionConfig,
    ReflectionCritique,
    ToolPolicy,
)
from .tool_aliasing import build_aliased_tool_catalog
from .trajectory import TrajectorySummary

logger = logging.getLogger("penguiflow.planner")


def init_react_planner(
    planner: Any,
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
    event_callback: Any | None = None,
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
    action_format: str = ActionFormat.AUTO,
    multi_action_sequential: bool = False,
    multi_action_read_only_only: bool = True,
    multi_action_max_tools: int = 2,
    use_native_llm: bool = False,
    guardrail_gateway: Any | None = None,
    guardrail_conversation_history_turns: int = 1,
) -> None:
    """Initialize a ReactPlanner instance with the specified configuration.

    Args:
        planner: The planner instance to initialize.
        llm: Model identifier string or config dict for the LLM.
        nodes: Sequence of Node instances to build the catalog from.
        catalog: Pre-built catalog of NodeSpec entries.
        registry: ModelRegistry for type adapters.
        llm_client: Custom JSONLLMClient implementation.
        max_iters: Maximum planning iterations.
        temperature: LLM temperature setting.
        json_schema_mode: Enable JSON schema mode for structured output.
        system_prompt_extra: Additional content to append to system prompt.
        token_budget: Maximum token budget for context window.
        pause_enabled: Enable pause/resume functionality.
        state_store: Optional state persistence store.
        artifact_store: Optional artifact storage backend.
        observation_guardrail: Configuration for observation size limits.
        summarizer_llm: Separate LLM for trajectory summarization.
        planning_hints: Mapping of planning hints for the LLM.
        repair_attempts: Maximum repair attempts for invalid responses.
        max_consecutive_arg_failures: Maximum consecutive argument validation failures.
        arg_fill_enabled: Enable automatic argument filling.
        deadline_s: Wall-clock deadline in seconds.
        hop_budget: Maximum number of planning hops.
        time_source: Custom time source callable.
        event_callback: Callback for planner events.
        llm_timeout_s: LLM request timeout in seconds.
        llm_max_retries: Maximum LLM retry attempts.
        use_native_reasoning: Enable native reasoning for supported models.
        reasoning_effort: Reasoning effort level (e.g., "low", "medium", "high").
        absolute_max_parallel: Maximum parallel tool executions.
        reflection_config: Configuration for reflection/critique.
        reflection_llm: Separate LLM for reflection.
        tool_policy: Policy for filtering available tools.
        stream_final_response: Enable streaming for final responses.
        short_term_memory: Short-term memory configuration.
        background_tasks: Configuration for background task handling.
        error_recovery: Configuration for error recovery strategies.
        action_format: Action format mode (auto, json, etc.).
        multi_action_sequential: Execute multiple actions sequentially.
        multi_action_read_only_only: Only allow read-only actions in parallel.
        multi_action_max_tools: Maximum tools per multi-action.
        use_native_llm: When True, use the native LLM layer (penguiflow.llm)
            instead of LiteLLM. The native layer provides type-safe requests,
            provider-specific adapters, and integrated cost tracking.
            Defaults to False for backward compatibility.
        guardrail_gateway: Optional guardrail gateway for safety checks.
    """
    if catalog is None:
        if nodes is None or registry is None:
            raise ValueError("Either catalog or (nodes and registry) must be provided")
        catalog = build_catalog(nodes, registry)

    planner._stream_final_response = stream_final_response
    planner._tool_policy = tool_policy
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

    planner._specs = specs
    spec_by_name, catalog_records, alias_to_real = build_aliased_tool_catalog(planner._specs)
    planner._spec_by_name = spec_by_name
    planner._catalog_records = catalog_records
    planner._tool_aliases = alias_to_real
    planner._register_resource_callbacks()
    planner._guardrail_gateway = guardrail_gateway
    planner._guardrail_context = None
    planner._guardrail_run_id = None
    planner._guardrail_conversation_history_turns = int(guardrail_conversation_history_turns)
    if planner._guardrail_conversation_history_turns < 0:
        raise ValueError("guardrail_conversation_history_turns must be >= 0")
    if guardrail_gateway is not None:
        from .guardrails import GuardrailContext

        planner._guardrail_run_id = uuid4().hex
        planner._guardrail_context = GuardrailContext(
            run_id=planner._guardrail_run_id,
            tool_context={
                "available_tools": [spec.name for spec in planner._specs],
            },
        )
    planner._planning_hints = _PlanningHints.from_mapping(planning_hints)
    hints_payload = planner._planning_hints.to_prompt_payload() if not planner._planning_hints.empty() else None
    planner._background_tasks = background_tasks or BackgroundTasksConfig()
    planner._error_recovery_config = error_recovery
    if planner._background_tasks.enabled and planner._background_tasks.include_prompt_guidance:
        system_prompt_extra = prompts.merge_prompt_extras(
            system_prompt_extra,
            prompts.render_background_task_guidance(),
        )

    # Rich output prompt catalog: ensure models know how to call render_component and where to
    # fetch schemas (describe_component), even when the host app forgot to inject it.
    if "render_component" in planner._spec_by_name:
        try:
            from penguiflow.rich_output.runtime import get_runtime

            runtime = get_runtime()
            if runtime.config.enabled and runtime.config.include_prompt_catalog:
                marker = "# Rich Output Components"
                already_included = bool(system_prompt_extra and marker in system_prompt_extra)
                if not already_included:
                    model_name = None
                    if isinstance(llm, str):
                        model_name = llm
                    elif isinstance(llm, Mapping):
                        raw = llm.get("model") or llm.get("name") or llm.get("llm")
                        if isinstance(raw, str):
                            model_name = raw
                    weak_model = False
                    if model_name:
                        needle = model_name.lower().replace("_", "-").strip()
                        weak_model = "gpt-oss-120b" in needle or "oss-120b" in needle
                    rich_prompt = runtime.prompt_section(include_examples=True if weak_model else None)
                    if rich_prompt:
                        system_prompt_extra = prompts.merge_prompt_extras(system_prompt_extra, rich_prompt)
        except Exception:  # pragma: no cover - optional integration
            pass
    planner._system_prompt = prompts.build_system_prompt(
        planner._catalog_records,
        extra=system_prompt_extra,
        planning_hints=hints_payload,
    )
    # Store extra for use in repair prompts (voice/personality context)
    planner._system_prompt_extra = system_prompt_extra
    planner._max_iters = max_iters
    planner._repair_attempts = repair_attempts
    planner._max_consecutive_arg_failures = max_consecutive_arg_failures
    planner._arg_fill_enabled = arg_fill_enabled
    planner._action_format = action_format
    planner._json_schema_mode = json_schema_mode
    planner._token_budget = token_budget
    planner._pause_enabled = pause_enabled
    planner._state_store = state_store

    # Artifact store resolution:
    # 1. Explicit parameter (highest priority)
    # 2. Discovered from state_store
    # 3. NoOpArtifactStore fallback (lowest priority)
    if artifact_store is not None:
        planner._artifact_store = artifact_store
    elif state_store is not None:
        discovered = discover_artifact_store(state_store)
        if discovered is not None:
            planner._artifact_store = discovered
            logger.debug("Discovered ArtifactStore from state_store")
        else:
            planner._artifact_store = NoOpArtifactStore()
    else:
        planner._artifact_store = NoOpArtifactStore()

    planner._artifact_registry = ArtifactRegistry()

    # Observation guardrail (enabled by default)
    planner._observation_guardrail = observation_guardrail or ObservationGuardrailConfig()

    planner._pause_records = {}
    planner._active_trajectory = None
    planner._active_tracker = None

    # Internal repair history tracking for tiered guidance
    # These accumulate across runs and are used by build_messages() to inject guidance
    planner._finish_repair_history_count = 0
    planner._arg_fill_repair_history_count = 0
    planner._multi_action_history_count = 0
    planner._render_component_failure_history_count = 0
    planner._cost_tracker = _CostTracker()
    planner._deadline_s = deadline_s
    planner._hop_budget = hop_budget
    planner._time_source = time_source or time.monotonic
    planner._event_callback = event_callback
    planner._absolute_max_parallel = absolute_max_parallel
    planner._use_native_reasoning = use_native_reasoning
    planner._reasoning_effort = reasoning_effort
    from .llm import _build_planner_action_schema_conditional_finish

    action_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "planner_action",
            "schema": _build_planner_action_schema_conditional_finish(),
        },
    }
    planner._action_schema = action_schema
    planner._response_format = action_schema if json_schema_mode else None
    planner._summarizer_client = None
    planner._reflection_client = None
    planner._clarification_client = None
    planner._reflection_config = reflection_config
    planner._action_seq = 0
    planner._ready_answer_seq = None
    planner._multi_action_sequential = bool(multi_action_sequential)
    planner._multi_action_read_only_only = bool(multi_action_read_only_only)
    planner._multi_action_max_tools = int(multi_action_max_tools)

    planner._memory_config = ShortTermMemoryConfig()
    planner._memory_singleton = None
    planner._memory_by_key = {}
    planner._memory_ephemeral_key = None
    planner._memory_summarizer_client = None
    planner._memory_summarizer = None
    planner._steering = None
    if isinstance(short_term_memory, ShortTermMemoryConfig):
        planner._memory_config = short_term_memory
        if short_term_memory.strategy != "none":
            # Default memory is scoped per session key to prevent leakage across users/sessions.
            planner._memory_by_key = {}
    elif short_term_memory is not None:
        # Custom memory instances are assumed to manage their own isolation semantics.
        planner._memory_singleton = short_term_memory

    if llm_client is not None:
        planner._client = llm_client
        # When using a custom client, auxiliary clients default to LiteLLM for consistency
        planner._use_native_llm = False

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
            planner._reflection_client = DSPyLLMClient.from_base_client(llm_client, ReflectionCritique)

            # Create DSPy clarification client (used when reflection fails)
            logger.info(
                "dspy_clarification_client_creation",
                extra={"schema": "ClarificationResponse"},
            )
            planner._clarification_client = DSPyLLMClient.from_base_client(llm_client, ClarificationResponse)

        # Create DSPy summarizer client if summarization enabled
        if is_dspy and token_budget is not None and token_budget > 0:
            assert isinstance(llm_client, DSPyLLMClient)  # for mypy
            logger.info(
                "dspy_summarizer_client_creation",
                extra={"schema": "TrajectorySummary"},
            )
            planner._summarizer_client = DSPyLLMClient.from_base_client(llm_client, TrajectorySummary)

        if is_dspy and planner._memory_singleton is None and planner._memory_config.strategy == "rolling_summary":
            assert isinstance(llm_client, DSPyLLMClient)  # for mypy
            logger.info(
                "dspy_memory_summarizer_client_creation",
                extra={"schema": "ShortTermMemorySummary"},
            )
            if planner._memory_config.summarizer_model:
                planner._memory_summarizer_client = DSPyLLMClient(
                    llm=planner._memory_config.summarizer_model,
                    output_schema=_ShortTermMemorySummary,
                    temperature=temperature,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                )
            else:
                planner._memory_summarizer_client = DSPyLLMClient.from_base_client(
                    llm_client,
                    _ShortTermMemorySummary,
                )
    else:
        if llm is None:
            raise ValueError("llm or llm_client must be provided")
        # Store the flag for auxiliary clients to reference
        planner._use_native_llm = use_native_llm
        if use_native_llm:
            # Use the native LLM layer instead of LiteLLM
            planner._client = create_native_adapter(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                streaming_enabled=stream_final_response,
                use_native_reasoning=use_native_reasoning,
                reasoning_effort=reasoning_effort,
            )
        else:
            planner._client = _LiteLLMJSONClient(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                streaming_enabled=stream_final_response,
                use_native_reasoning=use_native_reasoning,
                reasoning_effort=reasoning_effort,
            )

    if (
        planner._memory_summarizer_client is None
        and planner._memory_singleton is None
        and planner._memory_config.strategy == "rolling_summary"
        and planner._memory_config.summarizer_model is not None
    ):
        if getattr(planner, "_use_native_llm", False):
            planner._memory_summarizer_client = create_native_adapter(
                planner._memory_config.summarizer_model,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )
        else:
            planner._memory_summarizer_client = _LiteLLMJSONClient(
                planner._memory_config.summarizer_model,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

    # LiteLLM-based separate clients (override DSPy if explicitly provided)
    if summarizer_llm is not None:
        if getattr(planner, "_use_native_llm", False):
            planner._summarizer_client = create_native_adapter(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )
        else:
            planner._summarizer_client = _LiteLLMJSONClient(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

    # Only set reflection client from reflection_llm if not already set by DSPy
    if planner._reflection_client is None:
        if reflection_config and reflection_config.use_separate_llm:
            if reflection_llm is None:
                raise ValueError("reflection_llm required when use_separate_llm=True")
            if getattr(planner, "_use_native_llm", False):
                planner._reflection_client = create_native_adapter(
                    reflection_llm,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                )
            else:
                planner._reflection_client = _LiteLLMJSONClient(
                    reflection_llm,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                )
