"""ReactPlanner initialization helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..artifacts import (
    ArtifactStore,
    NoOpArtifactStore,
    discover_artifact_store,
)
from ..catalog import NodeSpec, ToolLoadingMode, build_catalog
from ..llm import CooldownStore, ModelFallbackConfig, create_native_adapter
from ..llm.fallback import GenericFallbackLLMClient
from ..llm.types import ReasoningDisplay, validate_reasoning_display
from ..node import Node
from ..skills.models import SkillListResponse, SkillSearchResponse
from ..skills.provider import (
    CompositeSkillProvider,
    LocalSkillProvider,
    SkillProvider,
    SkillProviderFactory,
)
from ..skills.tools.skill_get_tool import (
    SkillGetArgs,
    SkillGetResponse,
    skill_get,
)
from ..skills.tools.skill_list_tool import SkillListArgs, skill_list
from ..skills.tools.skill_propose_tool import (
    SkillProposeArgs,
    SkillProposeResponse,
    skill_propose,
)
from ..skills.tools.skill_search_tool import SkillSearchArgs, skill_search
from . import prompts
from .artifact_registry import ArtifactRegistry
from .constraints import _CostTracker
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

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ..registry import ModelRegistry
    from ..state import StateStore
    from .dspy_client import DSPyLLMClient
    from .error_recovery import ErrorRecoveryConfig
    from .guardrails import GuardrailGateway
    from .models import PlannerEventCallback
    from .react import ReactPlanner

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

    def _factory(
        model: str, *, api_key: str | None = None
    ) -> _LiteLLMJSONClient:
        # Provider options carried in the llm mapping (e.g. ``api_base``) are
        # folded back into the first positional argument, not passed as
        # keyword arguments: ``_LiteLLMJSONClient`` has no ``**kwargs``.
        llm_config = dict(extra_kwargs)
        llm_config["model"] = model
        if api_key is not None:
            llm_config["api_key"] = api_key
        llm_value: str | dict[str, Any] = (
            llm_config if extra_kwargs or api_key is not None else model
        )
        return _LiteLLMJSONClient(
            llm_value,
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

    return GenericFallbackLLMClient(
        model_name,
        llm_fallback,
        client_factory=_factory,
        cooldown_store=cooldown_store,
        default_api_key=api_key,
    )


@dataclass(frozen=True, slots=True)
class _ClientBuilder:
    """Builds the planner's PenguiFlow-managed LLM clients."""

    native: bool
    transport: str | None
    temperature: float
    max_retries: int
    timeout_s: float
    fallback: ModelFallbackConfig | None
    cooldown_store: CooldownStore | None

    def main(
        self,
        llm: str | Mapping[str, Any],
        *,
        json_schema_mode: bool,
        streaming_enabled: bool,
        use_native_reasoning: bool,
        reasoning_effort: str | None,
        reasoning_display: ReasoningDisplay,
    ) -> JSONLLMClient:
        """Build the main planning client."""
        if self.native:
            # Use the native LLM layer instead of LiteLLM
            return create_native_adapter(
                llm,
                transport=self.transport,
                temperature=self.temperature,
                json_schema_mode=json_schema_mode,
                max_retries=self.max_retries,
                timeout_s=self.timeout_s,
                streaming_enabled=streaming_enabled,
                use_native_reasoning=use_native_reasoning,
                reasoning_effort=reasoning_effort,
                reasoning_display=reasoning_display,
                fallback=self.fallback,
                cooldown_store=self.cooldown_store,
            )
        return _build_litellm_client(
            llm,
            temperature=self.temperature,
            json_schema_mode=json_schema_mode,
            max_retries=self.max_retries,
            timeout_s=self.timeout_s,
            streaming_enabled=streaming_enabled,
            use_native_reasoning=use_native_reasoning,
            reasoning_effort=reasoning_effort,
            reasoning_display=reasoning_display,
            llm_fallback=self.fallback,
            cooldown_store=self.cooldown_store,
        )

    def auxiliary(self, llm: str | Mapping[str, Any]) -> JSONLLMClient:
        """Build a summarizer, reflection or memory client."""
        if self.native:
            return create_native_adapter(
                llm,
                transport=self.transport,
                temperature=self.temperature,
                json_schema_mode=True,
                max_retries=self.max_retries,
                timeout_s=self.timeout_s,
                fallback=self.fallback,
                cooldown_store=self.cooldown_store,
            )
        return _build_litellm_client(
            llm,
            temperature=self.temperature,
            json_schema_mode=True,
            max_retries=self.max_retries,
            timeout_s=self.timeout_s,
            llm_fallback=self.fallback,
            cooldown_store=self.cooldown_store,
        )


def _init_custom_client(
    planner: ReactPlanner,
    llm_client: JSONLLMClient,
    *,
    llm_fallback: ModelFallbackConfig | None,
    reflection_config: ReflectionConfig | None,
    token_budget: int | None,
    temperature: float,
    llm_max_retries: int,
    llm_timeout_s: float,
) -> None:
    """Install a user-supplied ``llm_client`` as the planning client.

    Raises:
        ValueError: If ``llm_fallback`` is also set.
    """
    from .dspy_client import DSPyLLMClient

    # Built-in rate-limit fallback is only wired into PenguiFlow-managed
    # clients (native adapters and the LiteLLM JSON client). A user-supplied
    # ``llm_client`` — including the deprecated DSPyLLMClient — is never
    # auto-upgraded, so fail loudly rather than silently ignore
    # ``llm_fallback``.
    if llm_fallback is not None:
        msg = (
            "llm_fallback is not supported with a custom "
            "llm_client. It only applies "
            "to PenguiFlow-managed clients: use the native LLM layer "
            "(default) or "
            "use_native_llm=False (LiteLLM). DSPyLLMClient is deprecated "
            "and does not "
            "participate in fallback."
        )
        raise ValueError(msg)

    planner.client = llm_client
    if isinstance(llm_client, DSPyLLMClient):
        _init_dspy_clients(
            planner,
            llm_client,
            reflection_config=reflection_config,
            token_budget=token_budget,
            temperature=temperature,
            llm_max_retries=llm_max_retries,
            llm_timeout_s=llm_timeout_s,
        )


def _init_dspy_clients(
    planner: ReactPlanner,
    llm_client: DSPyLLMClient,
    *,
    reflection_config: ReflectionConfig | None,
    token_budget: int | None,
    temperature: float,
    llm_max_retries: int,
    llm_timeout_s: float,
) -> None:
    """Derive the auxiliary DSPy clients from the main DSPy client.

    DSPy clients are hardcoded to a single output schema, so distinct
    instances are needed for reflection (ReflectionCritique), clarification
    (ClarificationResponse), and summarization (TrajectorySummary).
    """
    from .dspy_client import DSPyLLMClient

    if reflection_config and reflection_config.enabled:
        logger.info(
            "dspy_reflection_client_creation",
            extra={"schema": "ReflectionCritique"},
        )
        planner.reflection_client = DSPyLLMClient.from_base_client(
            llm_client, ReflectionCritique
        )

        # Create DSPy clarification client (used when reflection fails)
        logger.info(
            "dspy_clarification_client_creation",
            extra={"schema": "ClarificationResponse"},
        )
        planner.clarification_client = DSPyLLMClient.from_base_client(
            llm_client, ClarificationResponse
        )

    # Create DSPy summarizer client if summarization enabled
    if token_budget is not None and token_budget > 0:
        logger.info(
            "dspy_summarizer_client_creation",
            extra={"schema": "TrajectorySummary"},
        )
        planner.summarizer_client = DSPyLLMClient.from_base_client(
            llm_client, TrajectorySummary
        )

    if (
        planner.memory_singleton is None
        and planner.memory_config.strategy == "rolling_summary"
    ):
        logger.info(
            "dspy_memory_summarizer_client_creation",
            extra={"schema": "ShortTermMemorySummary"},
        )
        if planner.memory_config.summarizer_model:
            planner.memory_summarizer_client = DSPyLLMClient(
                llm=planner.memory_config.summarizer_model,
                output_schema=_ShortTermMemorySummary,
                temperature=temperature,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )
        else:
            planner.memory_summarizer_client = DSPyLLMClient.from_base_client(
                llm_client,
                _ShortTermMemorySummary,
            )


def _init_auxiliary_clients(
    planner: ReactPlanner,
    builder: _ClientBuilder,
    *,
    summarizer_llm: str | Mapping[str, Any] | None,
    reflection_config: ReflectionConfig | None,
    reflection_llm: str | Mapping[str, Any] | None,
) -> None:
    """Build the memory, summarizer and reflection clients from config.

    Raises:
        ValueError: If reflection needs a separate LLM but none is given.
    """
    memory_config = planner.memory_config
    if (
        planner.memory_summarizer_client is None
        and planner.memory_singleton is None
        and memory_config.strategy == "rolling_summary"
        and memory_config.summarizer_model is not None
    ):
        planner.memory_summarizer_client = builder.auxiliary(
            memory_config.summarizer_model
        )

    # LiteLLM-based separate clients (override DSPy if explicitly provided)
    if summarizer_llm is not None:
        planner.summarizer_client = builder.auxiliary(summarizer_llm)

    # Only set reflection client from reflection_llm if not already set by DSPy
    if (
        planner.reflection_client is None
        and reflection_config
        and reflection_config.use_separate_llm
    ):
        if reflection_llm is None:
            msg = "reflection_llm required when use_separate_llm=True"
            raise ValueError(msg)
        planner.reflection_client = builder.auxiliary(reflection_llm)


def _init_memory(
    planner: ReactPlanner,
    *,
    short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None,
) -> None:
    """Install the short-term memory configuration or instance."""
    planner.memory_config = ShortTermMemoryConfig()
    planner.memory_singleton = None
    planner.memory_by_key = {}
    planner.memory_ephemeral_key = None
    planner.memory_summarizer_client = None
    planner.memory_summarizer = None
    planner.steering = None
    if isinstance(short_term_memory, ShortTermMemoryConfig):
        planner.memory_config = short_term_memory
        if short_term_memory.strategy != "none":
            # Default memory is scoped per session key to prevent leakage
            # across users/sessions.
            planner.memory_by_key = {}
    elif short_term_memory is not None:
        # Custom memory instances are assumed to manage their own isolation
        # semantics.
        planner.memory_singleton = short_term_memory


def _init_action_schema(
    planner: ReactPlanner,
    *,
    json_schema_mode: bool,
    llm: str | Mapping[str, Any] | None,
    llm_client: JSONLLMClient | None,
) -> None:
    """Build the JSON action schema the planning LLM must answer with."""
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
        _build_planner_action_schema_conditional_finish(
            structured_schema=planner.final_response_schema
        )
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
    planner.action_schema = action_schema
    planner.response_format = action_schema if json_schema_mode else None


def init_react_planner(
    planner: ReactPlanner,
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
    guardrail_gateway: GuardrailGateway | None = None,
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
        max_consecutive_arg_failures: Maximum consecutive argument validation
            failures.
        arg_fill_enabled: Enable automatic argument filling.
        deadline_s: Wall-clock deadline in seconds.
        hop_budget: Maximum number of planning hops.
        time_source: Custom time source callable.
        event_callback: Callback for planner events.
        llm_timeout_s: LLM request timeout in seconds.
        llm_max_retries: Maximum LLM retry attempts.
        use_native_reasoning: Enable native reasoning for supported models.
        reasoning_effort: Reasoning effort level (e.g., "low", "medium",
            "high").
        reasoning_display: Reasoning display mode ("summarized" or "omitted").
        llm_fallback: Optional ModelFallbackConfig enabling rate-limit fallback
            across an ordered chain of models (native LLM layer only).
        absolute_max_parallel: Maximum parallel tool executions.
        reflection_config: Configuration for reflection/critique.
        reflection_llm: Separate LLM for reflection.
        tool_policy: Policy for filtering available tools.
        stream_final_response: Enable streaming for final responses.
        final_response_model: Optional Pydantic model the final answer must
            validate against.
        final_response_retries: Corrective attempts allowed when the final
            answer does not validate against ``final_response_model``.
        tool_call_mode: ``"prompted"`` (JSON actions in text) or
            ``"native"`` (provider-native tool calls).
        llm_transport: Transport for every client of this planner, overriding
            the model profile's preferred transport.
        short_term_memory: Short-term memory configuration.
        background_tasks: Configuration for background task handling.
        error_recovery: Configuration for error recovery strategies.
        action_format: Action format mode (auto, json, etc.).
        multi_action_sequential: Execute multiple actions sequentially.
        multi_action_read_only_only: Only allow read-only actions in parallel.
        multi_action_max_tools: Maximum tools per multi-action.
        auto_seq_enabled: Detect deterministic transitions: when exactly one
            ``auto_seq`` tool accepts the previous observation, select it.
        auto_seq_execute: Also execute the selected transition without another
            LLM step (the tool must opt in with ``auto_seq_execute``).
        auto_seq_read_only_only: Only consider read-only tools for auto-seq.
        use_native_llm: When True, use the native LLM layer (penguiflow.llm)
            instead of LiteLLM. The native layer provides type-safe requests,
            provider-specific adapters, and integrated cost tracking.
            Defaults to False for backward compatibility.
        guardrail_gateway: Optional guardrail gateway for safety checks.
        guardrail_conversation_history_turns: Conversation turns included in
            guardrail evaluation context.
        tool_search_config: Tool discovery (``tool_search``/``tool_get``)
            configuration.
        tool_examples_config: Configuration for tool examples in the prompt.
        skills_config: Skill discovery configuration.
        skills_provider: Runtime skill provider (exclusive with
            ``skills_provider_factory``).
        skills_provider_factory: Factory building the runtime skill provider
            from ``skills_config``.

    Raises:
        ValueError: If the configuration is inconsistent, for example both
            ``skills_provider`` and ``skills_provider_factory`` are given, or
            neither ``catalog`` nor ``nodes`` and ``registry``.
    """
    tool_search_config = tool_search_config or ToolSearchConfig()
    tool_examples_config = tool_examples_config or ToolExamplesConfig()
    if skills_provider is not None and skills_provider_factory is not None:
        msg = (
            "skills_provider and skills_provider_factory are mutually "
            "exclusive"
        )
        raise ValueError(msg)

    explicit_runtime_provider = (
        skills_provider is not None or skills_provider_factory is not None
    )
    if skills_config is None and explicit_runtime_provider:
        skills_config = SkillsConfig(enabled=True)
    skills_config = skills_config or SkillsConfig()
    if explicit_runtime_provider and not skills_config.enabled:
        msg = (
            "skills_provider / skills_provider_factory require "
            "skills.enabled=True"
        )
        raise ValueError(msg)
    planner.tool_search_config = tool_search_config
    planner.tool_examples_config = tool_examples_config
    planner.skills_config = skills_config
    planner.skills_provider = None
    planner.tool_search_cache = None
    planner.tool_search_max_results = tool_search_config.max_search_results
    planner.execution_specs = []
    planner.execution_spec_by_name = {}
    planner.tool_visibility_allowed_names = None
    planner.active_tool_names = None
    planner.session_tool_activations = {}

    if catalog is None:
        if nodes is None or registry is None:
            msg = "Either catalog or (nodes and registry) must be provided"
            raise ValueError(msg)
        default_loading = (
            tool_search_config.default_loading_mode
            if tool_search_config.enabled
            else ToolLoadingMode.ALWAYS
        )
        catalog = build_catalog(
            nodes, registry, default_loading_mode=default_loading
        )

    specs = list(catalog)
    _add_tool_search_specs(specs=specs, tool_search_config=tool_search_config)

    _add_skill_tool_specs(skills_config=skills_config, specs=specs)

    _init_final_response(
        planner,
        final_response_model=final_response_model,
        final_response_retries=final_response_retries,
        llm_transport=llm_transport,
        stream_final_response=stream_final_response,
        tool_call_mode=tool_call_mode,
    )
    specs = _apply_tool_policy(planner, specs=specs, tool_policy=tool_policy)

    _init_tool_catalog(
        planner, specs=specs, tool_search_config=tool_search_config
    )

    _init_tool_search_cache(
        planner, specs=specs, tool_search_config=tool_search_config
    )
    planner.register_resource_callbacks()
    _init_guardrails(
        planner,
        guardrail_conversation_history_turns=guardrail_conversation_history_turns,
        guardrail_gateway=guardrail_gateway,
    )
    planner.planning_hints = _PlanningHints.from_mapping(planning_hints)
    hints_payload = (
        planner.planning_hints.to_prompt_payload()
        if not planner.planning_hints.empty()
        else None
    )
    planner.background_tasks = background_tasks or BackgroundTasksConfig()
    planner.error_recovery_config = error_recovery
    system_prompt_extra = _with_discovery_guidance(
        planner,
        skills_config=skills_config,
        system_prompt_extra=system_prompt_extra,
        tool_search_config=tool_search_config,
    )

    system_prompt_extra = _with_rich_output_prompt(
        planner, llm=llm, system_prompt_extra=system_prompt_extra
    )
    planner.system_prompt = prompts.build_system_prompt(
        planner.catalog_records,
        extra=system_prompt_extra,
        planning_hints=hints_payload,
        tool_examples=tool_examples_config,
        structured_final_schema=planner.final_response_schema,
        tool_call_mode=tool_call_mode,
    )
    # Store extra for use in repair prompts (voice/personality context)
    planner.system_prompt_extra = system_prompt_extra
    planner.max_iters = max_iters
    planner.repair_attempts = repair_attempts
    planner.max_consecutive_arg_failures = max_consecutive_arg_failures
    planner.arg_fill_enabled = arg_fill_enabled
    planner.action_format = action_format
    planner.json_schema_mode = json_schema_mode
    planner.token_budget = token_budget
    planner.pause_enabled = pause_enabled
    planner.state_store = state_store

    _init_artifact_store(
        planner, artifact_store=artifact_store, state_store=state_store
    )

    planner.artifact_registry = ArtifactRegistry()

    # Observation guardrail (enabled by default)
    planner.observation_guardrail = (
        observation_guardrail or ObservationGuardrailConfig()
    )

    planner.pause_records = {}
    planner.active_trajectory = None
    planner.active_tracker = None

    # Internal repair history tracking for tiered guidance
    # These accumulate across runs and are used by build_messages() to inject
    # guidance
    planner.finish_repair_history_count = 0
    planner.arg_fill_repair_history_count = 0
    planner.multi_action_history_count = 0
    planner.render_component_failure_history_count = 0
    planner.cost_tracker = _CostTracker()
    planner.deadline_s = deadline_s
    planner.hop_budget = hop_budget
    planner.time_source = time_source or time.monotonic
    planner.event_callback = event_callback
    planner.event_buffer = []
    planner.absolute_max_parallel = absolute_max_parallel
    planner.use_native_reasoning = use_native_reasoning
    planner.reasoning_effort = reasoning_effort
    reasoning_display = validate_reasoning_display(reasoning_display)
    planner.reasoning_display = reasoning_display
    _init_skills_provider(
        planner,
        skills_config=skills_config,
        skills_provider=skills_provider,
        skills_provider_factory=skills_provider_factory,
    )
    _init_action_schema(
        planner,
        json_schema_mode=json_schema_mode,
        llm=llm,
        llm_client=llm_client,
    )
    planner.summarizer_client = None
    planner.reflection_client = None
    planner.clarification_client = None
    planner.reflection_config = reflection_config
    planner.action_seq = 0
    planner.ready_answer_seq = None
    planner.multi_action_sequential = bool(multi_action_sequential)
    planner.multi_action_read_only_only = bool(multi_action_read_only_only)
    planner.multi_action_max_tools = int(multi_action_max_tools)
    planner.auto_seq_enabled = bool(auto_seq_enabled)
    planner.auto_seq_execute = bool(auto_seq_execute)
    planner.auto_seq_read_only_only = bool(auto_seq_read_only_only)

    _init_memory(planner, short_term_memory=short_term_memory)

    if llm_client is not None:
        _init_custom_client(
            planner,
            llm_client,
            llm_fallback=llm_fallback,
            reflection_config=reflection_config,
            token_budget=token_budget,
            temperature=temperature,
            llm_max_retries=llm_max_retries,
            llm_timeout_s=llm_timeout_s,
        )
    elif llm is None:
        msg = "llm or llm_client must be provided"
        raise ValueError(msg)
    # Store the flag for auxiliary clients to reference
    planner.use_native_llm = llm_client is None and use_native_llm
    builder = _ClientBuilder(
        native=planner.use_native_llm,
        transport=llm_transport,
        temperature=temperature,
        max_retries=llm_max_retries,
        timeout_s=llm_timeout_s,
        fallback=llm_fallback,
        # One cooldown store shared by every native client of this run, so a
        # 429 seen by any client (main or auxiliary) protects the others.
        cooldown_store=CooldownStore() if llm_fallback is not None else None,
    )
    if llm_client is None and llm is not None:
        planner.client = builder.main(
            llm,
            json_schema_mode=json_schema_mode,
            streaming_enabled=stream_final_response,
            use_native_reasoning=use_native_reasoning,
            reasoning_effort=reasoning_effort,
            reasoning_display=reasoning_display,
        )
    _init_auxiliary_clients(
        planner,
        builder,
        summarizer_llm=summarizer_llm,
        reflection_config=reflection_config,
        reflection_llm=reflection_llm,
    )


def _add_tool_search_specs(
    *, specs: list[NodeSpec], tool_search_config: ToolSearchConfig
) -> None:
    """Append the built-in tool_search and tool_get tools if missing."""
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


def _add_skill_tool_specs(
    *, skills_config: SkillsConfig, specs: list[NodeSpec]
) -> None:
    """Append the built-in skill tools missing from the catalog."""
    if skills_config.enabled:
        skill_tools: dict[
            str,
            tuple[Callable[..., Any], type[BaseModel], type[BaseModel], str],
        ] = {
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


def _init_final_response(
    planner: ReactPlanner,
    *,
    final_response_model: type[BaseModel] | None,
    final_response_retries: int,
    llm_transport: str | None,
    stream_final_response: bool,
    tool_call_mode: str,
) -> None:
    """Validate the tool-call mode and record the final response schema."""
    planner.stream_final_response = stream_final_response
    if tool_call_mode not in ("prompted", "native"):
        msg = (
            f"Unknown tool_call_mode {tool_call_mode!r}; expected "
            "'prompted' or 'native'"
        )
        raise ValueError(msg)
    planner.tool_call_mode = tool_call_mode
    planner.llm_transport = llm_transport
    planner.final_response_model = final_response_model
    planner.final_response_retries = final_response_retries
    planner.final_response_schema = None
    if final_response_model is not None:
        from .llm import _sanitize_json_schema

        planner.final_response_schema = _sanitize_json_schema(
            final_response_model.model_json_schema()
        )


def _apply_tool_policy(
    planner: ReactPlanner,
    *,
    specs: list[NodeSpec],
    tool_policy: ToolPolicy | None,
) -> list[NodeSpec]:
    """Drop the tools the policy disallows, logging what was removed."""
    planner.tool_policy = tool_policy
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
    return specs


def _init_tool_catalog(
    planner: ReactPlanner,
    *,
    specs: list[NodeSpec],
    tool_search_config: ToolSearchConfig,
) -> None:
    """Expose the always-loaded tools and their aliased prompt catalog."""
    planner.execution_specs = specs
    planner.execution_spec_by_name = {spec.name: spec for spec in specs}

    if tool_search_config.enabled:
        visible_specs = [
            spec
            for spec in specs
            if spec.loading_mode == ToolLoadingMode.ALWAYS
            or any(
                fnmatch(spec.name, pattern)
                for pattern in tool_search_config.always_loaded_patterns
            )
        ]
    else:
        visible_specs = specs

    planner.specs = visible_specs
    spec_by_name, catalog_records, alias_to_real = build_aliased_tool_catalog(
        planner.specs
    )
    planner.spec_by_name = spec_by_name
    planner.catalog_records = catalog_records
    planner.tool_aliases = alias_to_real
    planner.tool_visibility_allowed_names = {spec.name for spec in specs}


def _init_tool_search_cache(
    planner: ReactPlanner,
    *,
    specs: list[NodeSpec],
    tool_search_config: ToolSearchConfig,
) -> None:
    """Index the execution tools for tool_search when it is enabled."""
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
                "visible_tool_count": len(planner.specs),
            },
        )
        planner.tool_search_cache = cache


def _init_guardrails(
    planner: ReactPlanner,
    *,
    guardrail_conversation_history_turns: int,
    guardrail_gateway: GuardrailGateway | None,
) -> None:
    """Attach the guardrail gateway and its run context.

    Raises:
        ValueError: If ``guardrail_conversation_history_turns`` is negative.
    """
    planner.guardrail_gateway = guardrail_gateway
    planner.guardrail_context = None
    planner.guardrail_run_id = None
    planner.guardrail_conversation_history_turns = int(
        guardrail_conversation_history_turns
    )
    if planner.guardrail_conversation_history_turns < 0:
        msg = "guardrail_conversation_history_turns must be >= 0"
        raise ValueError(msg)
    if guardrail_gateway is not None:
        from .guardrails import GuardrailContext

        planner.guardrail_run_id = uuid4().hex
        planner.guardrail_context = GuardrailContext(
            run_id=planner.guardrail_run_id,
            tool_context={
                "available_tools": [spec.name for spec in planner.specs],
            },
        )


def _with_discovery_guidance(
    planner: ReactPlanner,
    *,
    skills_config: SkillsConfig,
    system_prompt_extra: str | None,
    tool_search_config: ToolSearchConfig,
) -> str | None:
    """Append tool, skill and background-task guidance to the prompt extras."""
    tool_search_available = tool_search_config.enabled and any(
        spec.name == "tool_search" for spec in planner.specs
    )
    if tool_search_available:
        system_prompt_extra = prompts.merge_prompt_extras(
            system_prompt_extra,
            prompts.render_tool_discovery_guidance(),
        )

    skills_available = skills_config.enabled and any(
        spec.name == "skill_search" for spec in planner.specs
    )
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

    if (
        planner.background_tasks.enabled
        and planner.background_tasks.include_prompt_guidance
    ):
        system_prompt_extra = prompts.merge_prompt_extras(
            system_prompt_extra,
            prompts.render_background_task_guidance(),
        )
    return system_prompt_extra


def _rich_output_model_name(llm: str | Mapping[str, Any] | None) -> str | None:
    """Return the model name configured in ``llm``, if any."""
    if isinstance(llm, str):
        return llm
    if isinstance(llm, Mapping):
        raw = llm.get("model") or llm.get("name") or llm.get("llm")
        if isinstance(raw, str):
            return raw
    return None


def _with_rich_output_prompt(
    planner: ReactPlanner,
    *,
    llm: str | Mapping[str, Any] | None,
    system_prompt_extra: str | None,
) -> str | None:
    """Append the rich output component catalog to the prompt extras.

    This ensures models know how to call render_component and where to fetch
    schemas (describe_component), even when the host app forgot to inject it.
    """
    if "render_component" not in planner.spec_by_name:
        return system_prompt_extra
    marker = "# Rich Output Components"
    if system_prompt_extra and marker in system_prompt_extra:
        return system_prompt_extra
    try:
        from penguiflow.rich_output.runtime import get_runtime

        runtime = get_runtime()
        if not (
            runtime.config.enabled and runtime.config.include_prompt_catalog
        ):
            return system_prompt_extra
        needle = (_rich_output_model_name(llm) or "").lower()
        needle = needle.replace("_", "-").strip()
        weak_model = "gpt-oss-120b" in needle or "oss-120b" in needle
        rich_prompt = runtime.prompt_section(
            include_examples=True if weak_model else None
        )
    except Exception:  # pragma: no cover - optional integration
        return system_prompt_extra
    if not rich_prompt:
        return system_prompt_extra
    return prompts.merge_prompt_extras(system_prompt_extra, rich_prompt)


def _init_artifact_store(
    planner: ReactPlanner,
    *,
    artifact_store: ArtifactStore | None,
    state_store: StateStore | None,
) -> None:
    """Use the given artifact store, one found on the state store, or none."""
    # Artifact store resolution:
    # 1. Explicit parameter (highest priority)
    # 2. Discovered from state_store
    # 3. NoOpArtifactStore fallback (lowest priority)
    if artifact_store is not None:
        planner.artifact_store = artifact_store
    elif state_store is not None:
        discovered = discover_artifact_store(state_store)
        if discovered is not None:
            planner.artifact_store = discovered
            logger.debug("Discovered ArtifactStore from state_store")
        else:
            planner.artifact_store = NoOpArtifactStore()
    else:
        planner.artifact_store = NoOpArtifactStore()


def _init_skills_provider(
    planner: ReactPlanner,
    *,
    skills_config: SkillsConfig,
    skills_provider: SkillProvider | None,
    skills_provider_factory: SkillProviderFactory | None,
) -> None:
    """Resolve the skill provider from the skills config and overrides."""
    if skills_config.enabled:
        local_provider: LocalSkillProvider | None = None
        if skills_config.skill_packs:
            local_provider = LocalSkillProvider(skills_config)
            pack_results = local_provider.load_packs()
            for result in pack_results:
                planner.emit_event(
                    PlannerEvent(
                        event_type="skill_pack_loaded",
                        ts=planner.time_source(),
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
            planner.skills_provider = CompositeSkillProvider(
                [runtime_provider, local_provider],
                config=skills_config,
            )
        elif runtime_provider is not None:
            planner.skills_provider = runtime_provider
        elif local_provider is not None:
            planner.skills_provider = local_provider
        else:
            planner.skills_provider = LocalSkillProvider(skills_config)
