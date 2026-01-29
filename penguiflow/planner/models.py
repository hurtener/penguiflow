"""Shared planner models and protocols."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict, cast

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from ..catalog import NodeSpec, ToolLoadingMode
from ..skills.models import SkillPackConfig, SkillsConfig, SkillsDirectoryConfig  # noqa: F401
from .context import PlannerPauseReason

# ---------------------------------------------------------------------------
# TypedDict schemas for unified action args (RFC_UNIFIED_ACTION_SCHEMA)
# ---------------------------------------------------------------------------


class PlanStep(TypedDict):
    """Single step in a parallel plan."""

    node: str
    args: dict[str, Any]


class PlanJoin(TypedDict, total=False):
    """Aggregation config after parallel execution."""

    node: str | None
    args: dict[str, Any]
    inject: dict[str, str]


class PlanArgs(TypedDict):
    """Args schema for next_node='parallel'."""

    steps: list[PlanStep]
    join: NotRequired[PlanJoin]


class TaskArgs(TypedDict, total=False):
    """Args schema for next_node='task.subagent' and next_node='task.tool'."""

    name: str
    # subagent mode
    query: str
    # tool job mode
    tool: str
    tool_args: dict[str, Any]
    # merge behavior
    merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]
    # task groups
    group: str
    group_id: str
    group_sealed: bool
    group_report: Literal["all", "any", "none"]
    group_merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]
    retain_turn: bool


class FinalResponseArgs(TypedDict, total=False):
    """Args schema for next_node='final_response'."""

    answer: str
    artifacts: dict[str, Any]
    sources: list[dict[str, Any]]
    suggested_actions: list[dict[str, Any]]
    confidence: float
    language: str


# ---------------------------------------------------------------------------
# Action format configuration (RFC_UNIFIED_ACTION_SCHEMA)
# ---------------------------------------------------------------------------


class ActionFormat:
    """Configuration for action schema parsing format.

    Values:
        UNIFIED: Use the unified 2-field format (next_node + args only)
        LEGACY: Use the legacy 5-field format with separate tool_name, plan, etc.
        AUTO: Automatically detect format from LLM response structure
    """

    UNIFIED = "unified"
    LEGACY = "legacy"
    AUTO = "auto"


class JSONLLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]: ...


@dataclass(frozen=True, slots=True)
class PlannerEvent:
    """Structured event emitted during planner execution for observability."""

    # Types: step_start, step_complete, llm_call, pause, resume, finish,
    # stream_chunk, artifact_chunk, llm_stream_chunk
    event_type: str
    ts: float
    trajectory_step: int
    thought: str | None = None
    node_name: str | None = None
    latency_ms: float | None = None
    token_estimate: int | None = None
    error: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    # Keys reserved by Python's logging.LogRecord that must not appear in extra
    _RESERVED_LOG_KEYS = frozenset(
        {
            "args",
            "msg",
            "levelname",
            "levelno",
            "exc_info",
            "message",
            "name",
            "filename",
            "pathname",
            "module",
            "lineno",
            "funcName",
            "created",
            "thread",
            "threadName",
            "process",
            "stack_info",
            "exc_text",
        }
    )

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
            # Filter out reserved logging keys to prevent LogRecord conflicts
            for key, value in self.extra.items():
                if key not in self._RESERVED_LOG_KEYS:
                    payload[key] = value
        return payload


# Observability callback type
PlannerEventCallback = Callable[[PlannerEvent], None]


class ParallelCall(BaseModel):
    node: str
    args: dict[str, Any] = Field(default_factory=dict)


class JoinInjection(BaseModel):
    """Mapping of join args to parallel execution data sources."""

    mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, value: Any) -> Any:
        """Allow shorthand {'field': '$results'} without 'mapping' wrapper."""

        if isinstance(value, Mapping) and "mapping" not in value:
            return {"mapping": value}
        return value


class ParallelJoin(BaseModel):
    node: str
    args: dict[str, Any] = Field(default_factory=dict)
    inject: JoinInjection | None = None


class Source(BaseModel):
    """Citation or reference used in a response."""

    title: str
    url: str | None = None
    snippet: str | None = None
    relevance_score: float | None = None


class SuggestedAction(BaseModel):
    """Recommended follow-up action for downstream consumers."""

    action_id: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)


class FinalPayload(BaseModel):
    """Standard structure for planner final answers."""

    raw_answer: str = Field(description="Human-readable answer text.")
    artifacts: dict[str, Any] = Field(
        default_factory=dict,
        description="Heavy tool outputs collected during execution.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence score from planner/reflection.",
    )
    sources: list[Source] = Field(
        default_factory=list,
        description="Citations gathered from retrieval tools.",
    )
    route: str | None = Field(
        default=None,
        description="Categorization of the answer type.",
    )
    suggested_actions: list[SuggestedAction] = Field(
        default_factory=list,
        description="Suggested next steps for the user or UI.",
    )
    requires_followup: bool = Field(
        default=False,
        description="True if user input/clarification is needed.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered during execution.",
    )
    language: str | None = Field(
        default=None,
        description="ISO 639-1 language code for the answer.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific fields not covered by the standard schema.",
    )


class PlannerAction(BaseModel):
    """Unified action format (RFC_UNIFIED_ACTION_SCHEMA).

    The LLM-facing schema is always:
    - ``next_node``: non-null string opcode or tool name
    - ``args``: object payload (defaults to {})

    Internally, we keep a best-effort ``thought`` field for trajectory logging and
    repair prompts, but it is excluded from the JSON schema so it is not required
    (or encouraged) in structured outputs.

    Special next_node values:
    - "final_response": Terminal action, args.answer streams to user
    - "parallel": Parallel execution, args contains steps and join config
    - "task.subagent": Background subagent task, args contains query and group config
    - "task.tool": Background single-tool job, args contains tool/tool_args and group config
    - Any other value: Tool call, args passed to the tool
    """

    next_node: str
    args: dict[str, Any] = Field(default_factory=dict)
    thought: SkipJsonSchema[str] = ""
    # Internal field to carry raw LLM response for debugging (excluded from serialization)
    raw_llm_response: SkipJsonSchema[str | None] = Field(default=None, exclude=True)
    # Optional additional action candidates extracted from mixed model output
    # (e.g. multiple JSON objects in a single response). Excluded from schema/serialization.
    alternate_actions: SkipJsonSchema[list[dict[str, Any]] | None] = Field(default=None, exclude=True)

    def is_terminal(self) -> bool:
        """True if this is a terminal action (final response to user)."""
        return self.next_node == "final_response"

    def is_parallel(self) -> bool:
        """True if this is a parallel execution plan."""
        return self.next_node == "parallel"

    def is_background_task(self) -> bool:
        """True if this is a background task spawn."""
        return self.next_node in {"task.subagent", "task.tool"}

    def is_tool_call(self) -> bool:
        """True if this is a regular tool call."""
        return self.next_node not in SPECIAL_NODE_TYPES

    def get_answer(self) -> str | None:
        """Extract answer text for terminal actions."""
        if not self.is_terminal():
            return None
        return self.args.get("answer")

    def answer_text(self) -> str | None:
        """Extract answer from args.answer or args.raw_answer (backward compatible)."""
        value = self.args.get("answer")
        if isinstance(value, str):
            return value
        legacy = self.args.get("raw_answer")
        if isinstance(legacy, str):
            return legacy
        return None

    def get_plan_steps(self) -> list[PlanStep] | None:
        """Extract parallel plan steps."""
        if not self.is_parallel():
            return None
        steps = self.args.get("steps", [])
        return steps if isinstance(steps, list) else []

    def get_plan_join(self) -> PlanJoin | None:
        """Extract parallel plan join config."""
        if not self.is_parallel():
            return None
        join = self.args.get("join")
        return cast(PlanJoin, join) if isinstance(join, dict) else None


# Special node types that aren't tool names (RFC_UNIFIED_ACTION_SCHEMA)
SPECIAL_NODE_TYPES = frozenset({"parallel", "task.subagent", "task.tool", "final_response"})

# Backward compatible alias
RESERVED_NEXT_NODES = SPECIAL_NODE_TYPES


@dataclass
class ActionWithReasoning:
    """Container for planner action with associated reasoning.

    Reasoning comes from LiteLLM's native reasoning_content,
    not from a JSON field.
    """

    action: PlannerAction
    reasoning: str | None = None
    reasoning_tokens: int | None = None

    @classmethod
    def from_llm_response(
        cls,
        response: Any,
        action: PlannerAction,
    ) -> ActionWithReasoning:
        """Create from LiteLLM response with reasoning extraction."""
        reasoning = None
        reasoning_tokens = None

        if isinstance(response, Mapping):
            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {}) if isinstance(choice, Mapping) else {}
            reasoning = message.get("reasoning_content")
            usage = response.get("usage", {})
            if isinstance(usage, Mapping):
                reasoning_tokens = usage.get("reasoning_tokens")
        else:
            try:
                message = response.choices[0].message
                reasoning = getattr(message, "reasoning_content", None)
                usage = getattr(response, "usage", None)
                if usage is not None:
                    reasoning_tokens = getattr(usage, "reasoning_tokens", None)
            except (AttributeError, IndexError):
                pass

        return cls(
            action=action,
            reasoning=reasoning,
            reasoning_tokens=reasoning_tokens,
        )


class PlannerPause(BaseModel):
    reason: PlannerPauseReason
    payload: dict[str, Any] = Field(default_factory=dict)
    resume_token: str


class PlannerFinish(BaseModel):
    reason: Literal["answer_complete", "no_path", "budget_exhausted"]
    payload: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolPolicy(BaseModel):
    """Runtime policy for tool availability and permissions."""

    allowed_tools: set[str] | None = None
    denied_tools: set[str] = Field(default_factory=set)
    require_tags: set[str] = Field(default_factory=set)

    def is_allowed(
        self,
        node_name: str,
        node_tags: Mapping[str, Any] | Sequence[str],
    ) -> bool:
        tags = set(node_tags)

        if node_name in self.denied_tools:
            return False

        if self.allowed_tools is not None and node_name not in self.allowed_tools:
            return False

        if self.require_tags and not self.require_tags.issubset(tags):
            return False

        return True


class ToolHintsConfig(BaseModel):
    enabled: bool = False
    top_k: int = Field(default=5, ge=1, le=20)
    include_always_loaded: bool = False
    search_type: Literal["fts", "regex", "exact"] = "fts"


class ToolGroupConfig(BaseModel):
    name: str
    title: str | None = None
    trigger: str | None = None
    task_type: Literal["browser", "api", "code", "domain", "unknown"] | None = None

    match_namespaces: list[str] = Field(default_factory=list)
    match_tags: list[str] = Field(default_factory=list)
    match_name_patterns: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)


class ToolDirectoryConfig(BaseModel):
    enabled: bool = False
    max_groups: int = Field(default=20, ge=1, le=100)
    max_tools_per_group: int = Field(default=6, ge=0, le=50)
    include_tool_counts: bool = True
    include_default_groups: bool = True
    groups: list[ToolGroupConfig] = Field(default_factory=list)


class ToolSearchConfig(BaseModel):
    enabled: bool = False
    cache_dir: str = ".penguiflow"
    default_loading_mode: ToolLoadingMode = ToolLoadingMode.ALWAYS
    always_loaded_patterns: list[str] = ["tasks.*", "tool_search", "tool_get", "finish"]
    activation_scope: Literal["run", "session"] = "run"
    preferred_namespaces: list[str] = []
    fts_fallback_to_regex: bool = True
    enable_incremental_index: bool = True
    rebuild_cache_on_init: bool = False
    max_search_results: int = 10

    # Optional prompt aids (opt-in)
    hints: ToolHintsConfig = Field(default_factory=ToolHintsConfig)
    directory: ToolDirectoryConfig = Field(default_factory=ToolDirectoryConfig)


class ToolExamplesConfig(BaseModel):
    enabled: bool = True
    max_examples_per_tool: int = Field(default=3, ge=1, le=10)
    include_descriptions: bool = True


class ToolVisibilityPolicy(Protocol):
    """Dynamic, per-run filtering for which tools are shown to the LLM.

    This is opt-in and intended for per-tenant/per-user tool visibility without
    constructing a brand new planner instance. Implementations must only return
    specs from the provided `specs` sequence.
    """

    def visible_tools(
        self,
        specs: Sequence[NodeSpec],
        tool_context: Mapping[str, Any],
    ) -> Sequence[NodeSpec]: ...


class BackgroundTasksConfig(BaseModel):
    """Configuration for background tasks/subagent orchestration.

    This is the single source of truth for background task settings, consumed by:
    - ReactPlanner (prompt guidance, tool validation)
    - SessionManager/TaskService (runtime enforcement)
    - Spec generation engine (agent.yaml generation)
    - Template engine (scaffolding new agents)

    Downstream teams configure these values in their agent's Config class,
    which builds this model before passing to ReactPlanner.
    """

    # Core enablement
    enabled: bool = False
    """Master switch for background task capabilities."""

    include_prompt_guidance: bool = True
    """Whether to inject background task guidance into the system prompt."""

    allow_tool_background: bool = False
    """Whether tools marked with background=True can spawn async tasks."""

    # Task execution mode
    default_mode: str = "subagent"
    """Default execution mode: 'subagent' (full reasoning) or 'job' (single tool)."""

    default_merge_strategy: str = "HUMAN_GATED"
    """How task results merge into context: HUMAN_GATED, APPEND, or REPLACE."""

    context_depth: str = "full"
    """Context snapshot depth for spawned tasks: 'full', 'summary', or 'minimal'."""

    propagate_on_cancel: str = "cascade"
    """Cancel propagation: 'cascade' (cancel children), 'orphan' (leave running)."""

    spawn_requires_confirmation: bool = False
    """Whether spawning a task requires explicit user confirmation."""

    # Resource limits
    max_concurrent_tasks: int = 5
    """Maximum number of tasks running concurrently per session."""

    max_tasks_per_session: int = 50
    """Maximum total tasks (active + completed) per session."""

    task_timeout_s: int = 3600
    """Task timeout in seconds (default: 1 hour)."""

    max_pending_steering: int = 2
    """Maximum steering messages queued per task before backpressure."""

    # Proactive report-back settings
    proactive_report_enabled: bool = False
    """Master switch for proactive messages on auto-merge completion."""

    proactive_report_strategies: list[str] = ["APPEND", "REPLACE"]
    """Merge strategies that trigger proactive reports (not HUMAN_GATED)."""

    proactive_report_max_queued: int = 5
    """Maximum queued reports before dropping oldest."""

    proactive_report_timeout_s: float = 30.0
    """Timeout for proactive message generation."""

    proactive_report_max_hops: int = 2
    """Maximum proactive recursion hops before disabling background spawning."""

    proactive_report_fallback_notification: bool = True
    """Fall back to notification panel if generation fails."""

    # Task group settings (RFC_TASK_GROUPS)
    default_group_merge_strategy: str = "APPEND"
    """Default merge strategy for task groups."""

    default_group_report: str = "all"
    """Default report strategy for groups: 'all', 'any', or 'none'."""

    group_timeout_s: float = 600.0
    """Timeout for group completion (seal to complete)."""

    group_partial_on_failure: bool = True
    """If True, report partial results when some tasks in a group fail."""

    max_tasks_per_group: int = 10
    """Maximum tasks allowed in a single group."""

    auto_seal_groups_on_foreground_yield: bool = True
    """Auto-seal OPEN groups when foreground yields to user."""

    retain_turn_timeout_s: float = 60.0
    """Max time foreground waits for retained tasks/groups before force-yield."""

    background_continuation_max_hops: int = 2
    """Maximum background continuation cycles after retain-timeout."""

    background_continuation_cooldown_s: float = 0.0
    """Delay between background continuation cycles."""


class BackgroundTaskHandle(BaseModel):
    """Return type for tools that run asynchronously in the background."""

    task_id: str
    status: str = "PENDING"
    message: str | None = None


class ReflectionCriteria(BaseModel):
    """Quality criteria used when critiquing an answer."""

    completeness: str = "Addresses all parts of the query"
    accuracy: str = "Factually correct based on observations"
    clarity: str = "Well-explained and coherent"


class ReflectionCritique(BaseModel):
    """Structured critique returned by the reflection LLM."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    feedback: str
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ReflectionConfig(BaseModel):
    """Configuration controlling the reflection loop behaviour."""

    enabled: bool = False
    criteria: ReflectionCriteria = Field(default_factory=ReflectionCriteria)
    quality_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    max_revisions: int = Field(default=2, ge=1, le=10)
    use_separate_llm: bool = False


class ClarificationResponse(BaseModel):
    """Response when planner cannot satisfy query after reflection failures."""

    text: str = Field(description="Honest explanation of what was tried and why it didn't work")
    confidence: Literal["satisfied", "unsatisfied"] = Field(description="Whether the query was satisfactorily answered")
    attempted_approaches: list[str] = Field(
        default_factory=list,
        description="List of approaches/tools tried to answer the query",
    )
    clarifying_questions: list[str] = Field(
        default_factory=list,
        description="Questions to ask user to better understand their needs",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="What would help answer this query (data sources, tools, context)",
    )
    reflection_score: float | None = Field(
        default=None,
        description="Final reflection quality score that triggered clarification",
    )
    revision_attempts: int | None = Field(
        default=None,
        description="How many revision attempts were made before giving up",
    )


class ObservationGuardrailConfig(BaseModel):
    """Configuration for planner-level observation size limits.

    This is the final safety net to prevent any tool output from
    overflowing the LLM context window, regardless of source.
    """

    # Character limits
    max_observation_chars: int = Field(
        default=50_000,
        ge=1000,
        description="Maximum characters allowed in a single observation",
    )
    max_field_chars: int = Field(
        default=10_000,
        ge=100,
        description="Maximum characters per field when truncating",
    )

    # Truncation behavior
    truncation_suffix: str = Field(
        default="\n... [truncated: {truncated_chars} chars]",
        description="Suffix appended to truncated content",
    )
    preserve_structure: bool = Field(
        default=True,
        description="Keep JSON structure when truncating, only truncate values",
    )

    # Artifact fallback
    auto_artifact_threshold: int = Field(
        default=20_000,
        ge=0,
        description="Store as artifact if larger than this (0 = disabled)",
    )

    # Preview generation
    preview_length: int = Field(
        default=500,
        ge=0,
        description="Length of preview to include in truncated refs",
    )


__all__ = [
    "ClarificationResponse",
    "JoinInjection",
    "JSONLLMClient",
    "ObservationGuardrailConfig",
    "ParallelCall",
    "ParallelJoin",
    "PlannerAction",
    "PlannerEvent",
    "PlannerEventCallback",
    "PlannerFinish",
    "PlannerPause",
    "ReflectionConfig",
    "ReflectionCritique",
    "ReflectionCriteria",
    "FinalPayload",
    "SkillPackConfig",
    "SkillsConfig",
    "SkillsDirectoryConfig",
    "Source",
    "SuggestedAction",
    "ToolPolicy",
    "ToolExamplesConfig",
    "ToolSearchConfig",
]
