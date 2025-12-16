"""Shared planner models and protocols."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from .context import PlannerPauseReason


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
    _RESERVED_LOG_KEYS = frozenset({"args", "msg", "levelname", "levelno", "exc_info", "message", "name"})

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
    thought: str
    next_node: str | None = None
    args: dict[str, Any] | None = None
    plan: list[ParallelCall] | None = None
    join: ParallelJoin | None = None


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


__all__ = [
    "ClarificationResponse",
    "JoinInjection",
    "JSONLLMClient",
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
    "Source",
    "SuggestedAction",
    "ToolPolicy",
]
