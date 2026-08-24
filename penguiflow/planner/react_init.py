"""ReactPlanner initialization helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from fnmatch import fnmatch
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..artifacts import ArtifactStore, NoOpArtifactStore, discover_artifact_store
from ..catalog import NodeSpec, ToolLoadingMode, build_catalog
from ..llm import CooldownStore, ModelFallbackConfig, create_native_adapter
from ..llm.fallback import GenericFallbackLLMClient
from ..llm.types import ReasoningDisplay, validate_reasoning_display
from ..node import Node
from ..registry import ModelRegistry
from ..skills.provider import CompositeSkillProvider, LocalSkillProvider, SkillProvider, SkillProviderFactory
from ..skills.tools.skill_get_tool import SkillGetArgs, SkillGetResponse, skill_get
from ..skills.tools.skill_list_tool import SkillListArgs, SkillListResponse, skill_list
from ..skills.tools.skill_propose_tool import SkillProposeArgs, SkillProposeResponse, skill_propose
from ..skills.tools.skill_search_tool import SkillSearchArgs, SkillSearchResponse, skill_search
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
    PlannerEvent,
    ReflectionConfig,
    ReflectionCritique,
    SkillsConfig,
    ToolExamplesConfig,
    ToolPolicy,
    ToolSearchConfig,
)
from .tool_aliasing import build_aliased_tool_catalog
from .tool_get_tool import ToolGetArgs, ToolGetResponse
from .tool_get_tool import tool_get as tool_get_tool
from .tool_search_cache import ToolSearchCache
from .tool_search_tool import ToolSearchArgs, ToolSearchResponse
from .tool_search_tool import tool_search as tool_search_tool
from .trajectory import TrajectorySummary

logger = logging.getLogger("penguiflow.planner")


def _build_litellm_client(
    llm: str | Mapping[str, Any],
    *,
    temperature: float,
    json_schema_mode: bool,
    max_retries: int,
    timeout_s: float,
    streaming_enabled: bool = False,
    use_native_reasoning: bool = True,
    reasoning_effort: str | None = None,
    reasoning_display: ReasoningDisplay = None,
    llm_fallback: ModelFallbackConfig | None = None,
    cooldown_store: CooldownStore | None = None,
) -> JSONLLMClient:
    def _factory(model: str, *, api_key: str | None = None, **kwargs: Any) -> _LiteLLMJSONClient:
        llm_value: str | dict[str, Any]
        llm_config = dict(extra_kwargs)
        llm_config["model"] = model
        if api_key is not None:
            llm_config["api_key"] = api_key
        llm_value = llm_config if extra_kwargs or api_key is not None else model
        return _LiteLLMJSONClient(llm_value, **kwargs)

    if llm_fallback is None:
        return _LiteLLMJSONClient(
            llm,
            temperature=temperature,
            json_schema_mode=json_schema_mode,
            max_retries=max_retries,
            timeout_s=timeout_s,
            streaming_enabled=streaming_enabled,
            use_native_reasoning=use_native_reasoning,
            reasoning_effort=reasoning_effort,
            reasoning_display=reasoning_display,
        )

    if isinstance(llm, Mapping):
        model_name = str(llm.get("model", ""))
        api_key = llm.get("api_key")
        extra_kwargs = dict(llm)
        extra_kwargs.pop("model", None)
        extra_kwargs.pop("api_key", None)
    else:
        model_name = llm
        api_key = None
        extra_kwargs = {}

    # NOTE: ``extra_kwargs`` (provider options carried in the llm mapping, e.g.
    # ``api_base``) are NOT forwarded as constructor kwargs here. ``_factory``
    # already folds them back into ``llm_value`` (the first positional arg of
    # ``_LiteLLMJSONClient``). Passing them again would both duplicate them and
    # crash, since ``_LiteLLMJSONClient.__init__`` has no ``**kwargs``.
    return GenericFallbackLLMClient(
        model_name,
        llm_fallback,
        client_factory=_factory,
        cooldown_store=cooldown_store,
        default_api_key=api_key,
        temperature=temperature,
        json_schema_mode=json_schema_mode,
        max_retries=max_retries,
        timeout_s=timeout_s,
        streaming_enabled=streaming_enabled,
        use_native_reasoning=use_native_reasoning,
        reasoning_effort=reasoning_effort,
        reasoning_display=reasoning_display,
        retry_rate_limit_errors=False,
    )


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
    short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None = None,
    background_tasks: BackgroundTasksConfig | None = None,
    error_recovery: ErrorRecoveryConfig | None = None,
    action_format: str = ActionFormat.AUTO,
    multi_action_sequential: bool = False,
    multi_action_read_only_only: bool = True,
    multi_action_max_tools: int = 2,
    auto_seq_enabled: bool = False,
    auto_seq_execute: bool = False,
    auto_seq_read_only_only: bool = True,
    use_native_llm: bool = False,
    guardrail_gateway: Any | None = None,
    guardrail_conversation_history_turns: int = 1,
    tool_search_config: ToolSearchConfig | None = None,
    tool_examples_config: ToolExamplesConfig | None = None,
    skills_config: SkillsConfig | None = None,
    skills_provider: SkillProvider | None = None,
    skills_provider_factory: SkillProviderFactory | None = None,
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
        reasoning_display: Reasoning display mode ("summarized" or "omitted").
        llm_fallback: Optional ModelFallbackConfig enabling rate-limit fallback
            across an ordered chain of models (native LLM layer only).
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
    tool_search_config = tool_search_config or ToolSearchConfig()
    tool_examples_config = tool_examples_config or ToolExamplesConfig()
    if skills_provider is not None and skills_provider_factory is not None:
        raise ValueError("skills_provider and skills_provider_factory are mutually exclusive")

    explicit_runtime_provider = skills_provider is not None or skills_provider_factory is not None
    if skills_config is None and explicit_runtime_provider:
        skills_config = SkillsConfig(enabled=True)
    skills_config = skills_config or SkillsConfig()
    if explicit_runtime_provider and not skills_config.enabled:
        raise ValueError("skills_provider / skills_provider_factory require skills.enabled=True")
    planner._tool_search_config = tool_search_config
    planner._tool_examples_config = tool_examples_config
    planner._skills_config = skills_config
    planner._skills_provider = None
    planner._tool_search_cache = None
    planner._tool_search_max_results = tool_search_config.max_search_results
    planner._execution_specs = []
    planner._execution_spec_by_name = {}
    planner._tool_visibility_allowed_names = None
    planner._active_tool_names = None
    planner._session_tool_activations = {}

    if catalog is None:
        if nodes is None or registry is None:
            raise ValueError("Either catalog or (nodes and registry) must be provided")
        default_loading = (
            tool_search_config.default_loading_mode if tool_search_config.enabled else ToolLoadingMode.ALWAYS
        )
        catalog = build_catalog(nodes, registry, default_loading_mode=default_loading)

    specs = list(catalog)
    if tool_search_config.enabled:
        tool_search_spec = NodeSpec(
            node=Node(tool_search_tool, name="tool_search"),
            name="tool_search",
            desc="Discover tools by capability and keywords.",
            args_model=ToolSearchArgs,
            out_model=ToolSearchResponse,
            side_effects="pure",
            tags=(),
            auth_scopes=(),
            cost_hint=None,
            latency_hint_ms=None,
            safety_notes=None,
            extra={},
            loading_mode=ToolLoadingMode.ALWAYS,
        )
        if not any(spec.name == "tool_search" for spec in specs):
            specs.append(tool_search_spec)

        tool_get_spec = NodeSpec(
            node=Node(tool_get_tool, name="tool_get"),
            name="tool_get",
            desc="Fetch a tool's schema/examples by name.",
            args_model=ToolGetArgs,
            out_model=ToolGetResponse,
            side_effects="pure",
            tags=(),
            auth_scopes=(),
            cost_hint=None,
            latency_hint_ms=None,
            safety_notes=None,
            extra={},
            loading_mode=ToolLoadingMode.ALWAYS,
        )
        if not any(spec.name == "tool_get" for spec in specs):
            specs.append(tool_get_spec)

    if skills_config.enabled:
        skill_tools: dict[str, tuple[Callable[..., Any], type[BaseModel], type[BaseModel], str]] = {
            "skill_search": (
                skill_search,
                SkillSearchArgs,
                SkillSearchResponse,
                "Discover skills by capability.",
            ),
            "skill_get": (
                skill_get,
                SkillGetArgs,
                SkillGetResponse,
                "Fetch skill content by name.",
            ),
            "skill_list": (
                skill_list,
                SkillListArgs,
                SkillListResponse,
                "List available skills.",
            ),
        }
        if skills_config.proposal.enabled:
            skill_tools["skill_propose"] = (
                skill_propose,
                SkillProposeArgs,
                SkillProposeResponse,
                "Draft a skill playbook from freeform source material.",
            )
        for name, (func, args_model, out_model, desc) in skill_tools.items():
            if any(spec.name == name for spec in specs):
                continue
            specs.append(
                NodeSpec(
                    node=Node(func, name=name),
                    name=name,
                    desc=desc,
                    args_model=args_model,
                    out_model=out_model,
                    side_effects="pure",
                    tags=(),
                    auth_scopes=(),
                    cost_hint=None,
                    latency_hint_ms=None,
                    safety_notes=None,
                    extra={},
                    loading_mode=ToolLoadingMode.ALWAYS,
                )
            )

    planner._stream_final_response = stream_final_response
    if tool_call_mode not in ("prompted", "native"):
        raise ValueError(f"Unknown tool_call_mode {tool_call_mode!r}; expected 'prompted' or 'native'")
    planner._tool_call_mode = tool_call_mode
    planner._llm_transport = llm_transport
    planner._final_response_model = final_response_model
    planner._final_response_retries = final_response_retries
    planner._final_response_schema = None
    if final_response_model is not None:
        from .llm import _sanitize_json_schema

        planner._final_response_schema = _sanitize_json_schema(final_response_model.model_json_schema())
    planner._tool_policy = tool_policy
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

    planner._execution_specs = specs
    planner._execution_spec_by_name = {spec.name: spec for spec in specs}

    if tool_search_config.enabled:
        visible_specs = [
            spec
            for spec in specs
            if spec.loading_mode == ToolLoadingMode.ALWAYS
            or any(fnmatch(spec.name, pattern) for pattern in tool_search_config.always_loaded_patterns)
        ]
    else:
        visible_specs = specs

    planner._specs = visible_specs
    spec_by_name, catalog_records, alias_to_real = build_aliased_tool_catalog(planner._specs)
    planner._spec_by_name = spec_by_name
    planner._catalog_records = catalog_records
    planner._tool_aliases = alias_to_real
    planner._tool_visibility_allowed_names = {spec.name for spec in specs}

    if tool_search_config.enabled:
        cache = ToolSearchCache(
            cache_dir=tool_search_config.cache_dir,
            preferred_namespaces=tool_search_config.preferred_namespaces,
            always_loaded_patterns=tool_search_config.always_loaded_patterns,
            fts_fallback_to_regex=tool_search_config.fts_fallback_to_regex,
            enable_incremental_index=tool_search_config.enable_incremental_index,
            rebuild_cache_on_init=tool_search_config.rebuild_cache_on_init,
            max_search_results=tool_search_config.max_search_results,
        )
        cache.sync_tools(specs)
        logger.info(
            "tool_search_cache_ready",
            extra={
                "db_path": str(cache.db_path),
                "tool_count": cache.tool_count(),
                "fts_available": cache.fts_available,
                "execution_tool_count": len(specs),
                "visible_tool_count": len(planner._specs),
            },
        )
        planner._tool_search_cache = cache
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
    tool_search_available = tool_search_config.enabled and any(spec.name == "tool_search" for spec in planner._specs)
    if tool_search_available:
        system_prompt_extra = prompts.merge_prompt_extras(
            system_prompt_extra,
            prompts.render_tool_discovery_guidance(),
        )

    skills_available = skills_config.enabled and any(spec.name == "skill_search" for spec in planner._specs)
    if skills_available:
        system_prompt_extra = prompts.merge_prompt_extras(
            system_prompt_extra,
            prompts.render_skill_discovery_guidance(),
        )
        if skills_config.proposal.enabled:
            system_prompt_extra = prompts.merge_prompt_extras(
                system_prompt_extra,
                prompts.render_skill_proposal_guidance(),
            )

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
        tool_examples=tool_examples_config,
        structured_final_schema=planner._final_response_schema,
        tool_call_mode=tool_call_mode,
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
    planner._pending_persistence_tasks = {}
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
    planner._event_buffer = []
    planner._absolute_max_parallel = absolute_max_parallel
    planner._use_native_reasoning = use_native_reasoning
    planner._reasoning_effort = reasoning_effort
    reasoning_display = validate_reasoning_display(reasoning_display)
    planner._reasoning_display = reasoning_display
    if skills_config.enabled:
        local_provider: LocalSkillProvider | None = None
        if skills_config.skill_packs:
            local_provider = LocalSkillProvider(skills_config)
            pack_results = local_provider.load_packs()
            for result in pack_results:
                planner._emit_event(
                    PlannerEvent(
                        event_type="skill_pack_loaded",
                        ts=planner._time_source(),
                        trajectory_step=0,
                        extra={
                            "pack_name": result.pack_name,
                            "skill_count": result.skill_count,
                            "updated_count": result.updated_count,
                        },
                    )
                )

        runtime_provider = skills_provider
        if runtime_provider is None and skills_provider_factory is not None:
            runtime_provider = skills_provider_factory(skills_config)

        if runtime_provider is not None and local_provider is not None:
            planner._skills_provider = CompositeSkillProvider(
                [runtime_provider, local_provider],
                config=skills_config,
            )
        elif runtime_provider is not None:
            planner._skills_provider = runtime_provider
        elif local_provider is not None:
            planner._skills_provider = local_provider
        else:
            planner._skills_provider = LocalSkillProvider(skills_config)
    from .llm import (
        _build_minimal_planner_schema,
        _build_planner_action_schema_conditional_finish,
        _supports_conditional_finish_schema,
    )

    schema_model_name = ""
    if isinstance(llm, str):
        schema_model_name = llm
    elif isinstance(llm, Mapping):
        schema_model_name = str(llm.get("model", ""))
    elif llm_client is not None:
        client_model = getattr(llm_client, "_model", None)
        if client_model is None:
            client_model = getattr(llm_client, "model", None)
        if isinstance(client_model, str):
            schema_model_name = client_model
        elif isinstance(client_model, Mapping):
            schema_model_name = str(client_model.get("model", ""))

    action_schema_payload = (
        _build_planner_action_schema_conditional_finish(structured_schema=planner._final_response_schema)
        if _supports_conditional_finish_schema(schema_model_name)
        else _build_minimal_planner_schema()
    )

    action_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "planner_action",
            "schema": action_schema_payload,
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
    planner._auto_seq_enabled = bool(auto_seq_enabled)
    planner._auto_seq_execute = bool(auto_seq_execute)
    planner._auto_seq_read_only_only = bool(auto_seq_read_only_only)

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

    # One cooldown store shared by every native client of this run, so a 429
    # seen by any client (main or auxiliary) protects the others.
    fallback_store = CooldownStore() if llm_fallback is not None else None

    if llm_client is not None:
        from .dspy_client import DSPyLLMClient

        # Built-in rate-limit fallback is only wired into PenguiFlow-managed clients
        # (native adapters and the LiteLLM JSON client). A user-supplied ``llm_client``
        # — including the deprecated DSPyLLMClient — is never auto-upgraded, so fail
        # loudly rather than silently ignore ``llm_fallback``.
        if llm_fallback is not None:
            raise ValueError(
                "llm_fallback is not supported with a custom llm_client. It only applies "
                "to PenguiFlow-managed clients: use the native LLM layer (default) or "
                "use_native_llm=False (LiteLLM). DSPyLLMClient is deprecated and does not "
                "participate in fallback."
            )

        is_dspy = isinstance(llm_client, DSPyLLMClient)
        planner._client = llm_client
        # When using a custom client, auxiliary clients default to LiteLLM for consistency
        planner._use_native_llm = False

        # CRITICAL: DSPy clients are hardcoded to a single output schema, so distinct
        # instances are needed for reflection (ReflectionCritique), clarification
        # (ClarificationResponse), and summarization (TrajectorySummary).
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
                transport=llm_transport,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                streaming_enabled=stream_final_response,
                use_native_reasoning=use_native_reasoning,
                reasoning_effort=reasoning_effort,
                reasoning_display=reasoning_display,
                fallback=llm_fallback,
                cooldown_store=fallback_store,
            )
        else:
            planner._client = _build_litellm_client(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                streaming_enabled=stream_final_response,
                use_native_reasoning=use_native_reasoning,
                reasoning_effort=reasoning_effort,
                reasoning_display=reasoning_display,
                llm_fallback=llm_fallback,
                cooldown_store=fallback_store,
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
                transport=llm_transport,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                fallback=llm_fallback,
                cooldown_store=fallback_store,
            )
        else:
            planner._memory_summarizer_client = _build_litellm_client(
                planner._memory_config.summarizer_model,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                llm_fallback=llm_fallback,
                cooldown_store=fallback_store,
            )

    # LiteLLM-based separate clients (override DSPy if explicitly provided)
    if summarizer_llm is not None:
        if getattr(planner, "_use_native_llm", False):
            planner._summarizer_client = create_native_adapter(
                summarizer_llm,
                transport=llm_transport,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                fallback=llm_fallback,
                cooldown_store=fallback_store,
            )
        else:
            planner._summarizer_client = _build_litellm_client(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                llm_fallback=llm_fallback,
                cooldown_store=fallback_store,
            )

    # Only set reflection client from reflection_llm if not already set by DSPy
    if planner._reflection_client is None:
        if reflection_config and reflection_config.use_separate_llm:
            if reflection_llm is None:
                raise ValueError("reflection_llm required when use_separate_llm=True")
            if getattr(planner, "_use_native_llm", False):
                planner._reflection_client = create_native_adapter(
                    reflection_llm,
                    transport=llm_transport,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                    fallback=llm_fallback,
                    cooldown_store=fallback_store,
                )
            else:
                planner._reflection_client = _build_litellm_client(
                    reflection_llm,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                    llm_fallback=llm_fallback,
                    cooldown_store=fallback_store,
                )
