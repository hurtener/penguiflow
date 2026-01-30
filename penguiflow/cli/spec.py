"""Spec schema and validation for `penguiflow generate`."""

from __future__ import annotations

import keyword
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped,unused-ignore]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode  # type: ignore[import-untyped,unused-ignore]

from .spec_errors import (
    SpecErrorDetail,
    SpecPath,
    SpecPathComponent,
    SpecValidationError,
)

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_TOOL_NAMES = set(keyword.kwlist) | {"__init__", "__call__"}
_PASCAL_CASE_PATTERN = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _suggest_snake_case(name: str) -> str | None:
    """Convert a name to snake_case suggestion, or None if not possible."""
    if not name:
        return None
    # Convert CamelCase to snake_case
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    result = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", result)
    result = result.lower()
    # Replace non-alphanumeric with underscores
    result = re.sub(r"[^a-z0-9]+", "_", result)
    # Remove leading/trailing underscores and collapse multiples
    result = re.sub(r"_+", "_", result).strip("_")
    # Ensure starts with letter
    if result and not result[0].isalpha():
        result = "tool_" + result
    return result if result and _TOOL_NAME_PATTERN.match(result) else None


_OPTIONAL_RE = re.compile(r"^Optional\[(?P<inner>.+)\]$", re.IGNORECASE)
_LIST_RE = re.compile(r"^list\[(?P<inner>.+)\]$", re.IGNORECASE)
_DICT_RE = re.compile(r"^dict\[(?P<key>[^,]+),(?P<value>.+)\]$", re.IGNORECASE)
_PRIMITIVE_TYPES = {"str", "int", "float", "bool"}

TypeKind = Literal["str", "int", "float", "bool", "list", "optional", "dict"]


@dataclass(frozen=True)
class TypeExpression:
    """Normalized representation of a spec type annotation."""

    raw: str
    kind: TypeKind
    args: tuple[TypeExpression, ...] = ()

    def render(self) -> str:
        """Render the normalized annotation."""
        if self.kind in _PRIMITIVE_TYPES:
            return self.kind
        if self.kind == "list":
            return f"list[{self.args[0].render()}]"
        if self.kind == "optional":
            return f"Optional[{self.args[0].render()}]"
        if self.kind == "dict":
            key, value = self.args
            return f"dict[{key.render()}, {value.render()}]"
        return self.raw


class UnsupportedTypeAnnotation(ValueError):
    """Raised when a tool field uses an unsupported type annotation."""


def parse_type_annotation(annotation: str) -> TypeExpression:
    """Parse and validate a type annotation string."""
    if not isinstance(annotation, str):
        raise UnsupportedTypeAnnotation("Type annotations must be strings.")
    text = annotation.strip()
    if not text:
        raise UnsupportedTypeAnnotation("Type annotation cannot be empty.")

    def _parse(candidate: str) -> TypeExpression:
        lowered = candidate.lower()
        if lowered in _PRIMITIVE_TYPES:
            return TypeExpression(raw=candidate, kind=cast(TypeKind, lowered))
        if optional_match := _OPTIONAL_RE.match(candidate):
            inner = _parse(optional_match.group("inner").strip())
            return TypeExpression(raw=candidate, kind="optional", args=(inner,))
        if list_match := _LIST_RE.match(candidate):
            inner = _parse(list_match.group("inner").strip())
            return TypeExpression(raw=candidate, kind="list", args=(inner,))
        if dict_match := _DICT_RE.match(candidate):
            key_expr = _parse(dict_match.group("key").strip())
            value_expr = _parse(dict_match.group("value").strip())
            if key_expr.kind not in _PRIMITIVE_TYPES:
                raise UnsupportedTypeAnnotation("dict keys must be primitive types (str, int, float, bool).")
            return TypeExpression(raw=candidate, kind="dict", args=(key_expr, value_expr))

        raise UnsupportedTypeAnnotation(
            f"Unsupported type annotation '{candidate}'. "
            "Supported types: str, int, float, bool, list[T], Optional[T], dict[K,V].",
        )

    return _parse(text)


@dataclass(slots=True)
class LineIndex:
    """Maps spec paths to YAML line numbers."""

    mapping: dict[SpecPath, int]

    def line_for(self, path: Sequence[SpecPathComponent]) -> int | None:
        candidate = tuple(path)
        for end in range(len(candidate), -1, -1):
            prefix = candidate[:end]
            if prefix in self.mapping:
                return self.mapping[prefix]
        return None


def _index_yaml_node(node: Node, path: SpecPath, mapping: dict[SpecPath, int]) -> None:
    current_line = node.start_mark.line + 1
    existing = mapping.get(path)
    mapping[path] = min(existing, current_line) if existing is not None else current_line
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                continue
            key = key_node.value
            key_path = path + (key,)
            mapping[key_path] = key_node.start_mark.line + 1
            _index_yaml_node(value_node, key_path, mapping)
    elif isinstance(node, SequenceNode):
        for idx, item in enumerate(node.value):
            item_path = path + (idx,)
            mapping[item_path] = item.start_mark.line + 1
            _index_yaml_node(item, item_path, mapping)


def _load_yaml(content: str, *, source: Path) -> tuple[Any, LineIndex]:
    try:
        root_node = yaml.compose(content, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else None
        raise SpecValidationError(
            source,
            [SpecErrorDetail(message=str(exc).strip() or "Invalid YAML.", line=line)],
        ) from None

    if root_node is None:
        raise SpecValidationError(
            source,
            [SpecErrorDetail(message="Spec file is empty.", line=None)],
        )

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else None
        raise SpecValidationError(
            source,
            [SpecErrorDetail(message=str(exc).strip() or "Invalid YAML.", line=line)],
        ) from None

    mapping: dict[SpecPath, int] = {}
    _index_yaml_node(root_node, (), mapping)
    return data, LineIndex(mapping)


def _normalize_loc(loc: Sequence[Any]) -> SpecPath:
    normalized: list[SpecPathComponent] = []
    for component in loc:
        if isinstance(component, (str, int)):
            normalized.append(component)
        else:
            normalized.append(str(component))
    return tuple(normalized)


def _errors_from_pydantic(error: ValidationError, *, lines: LineIndex) -> list[SpecErrorDetail]:
    details: list[SpecErrorDetail] = []
    for err in error.errors():
        path = _normalize_loc(err.get("loc", ()))
        message = err.get("msg", "Invalid value")
        details.append(
            SpecErrorDetail(
                message=message,
                path=path,
                line=lines.line_for(path),
            )
        )
    return details


class AgentFlagsSpec(BaseModel):
    streaming: bool = False
    hitl: bool = False
    a2a: bool = False
    memory: bool = True
    background_tasks: bool = False

    model_config = ConfigDict(extra="forbid")


class AgentSpec(BaseModel):
    name: str
    description: str
    template: Literal["minimal", "react", "parallel", "rag_server", "wayfinder", "analyst", "enterprise"]
    flags: AgentFlagsSpec = Field(default_factory=AgentFlagsSpec)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("agent.name is required.")
        return value

    @field_validator("description")
    @classmethod
    def _require_description(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("agent.description is required.")
        return value


class ToolBackgroundSpec(BaseModel):
    """Tool-level background execution configuration."""

    enabled: bool = False
    mode: Literal["job", "subagent"] = "job"
    default_merge_strategy: Literal["APPEND", "REPLACE", "HUMAN_GATED"] = "HUMAN_GATED"
    notify_on_complete: bool = True

    model_config = ConfigDict(extra="forbid")


class ToolSpec(BaseModel):
    name: str
    description: str
    side_effects: Literal["pure", "read", "write", "external", "stateful"] = "pure"
    tags: list[str] = Field(default_factory=list)
    group: str | None = None
    args: dict[str, TypeExpression] = Field(default_factory=dict)
    result: dict[str, TypeExpression] = Field(default_factory=dict)
    background: ToolBackgroundSpec | None = None

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("tool name is required.")
        return value

    @field_validator("description")
    @classmethod
    def _require_description(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("tool description is required.")
        return value

    @field_validator("args", "result", mode="before")
    @classmethod
    def _parse_types(cls, value: dict[str, str] | None) -> dict[str, TypeExpression]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("must be a mapping of field name to type annotation.")

        parsed: dict[str, TypeExpression] = {}
        for field_name, annotation in value.items():
            parsed[field_name] = parse_type_annotation(annotation)
        return parsed


class ExternalToolPresetSpec(BaseModel):
    """Reference to a preset MCP server."""

    preset: str
    auth_override: Literal["bearer", "oauth", "none"] | None = None
    env: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, value: str) -> str:
        from penguiflow.tools.presets import POPULAR_MCP_SERVERS

        valid_presets = set(POPULAR_MCP_SERVERS.keys())
        if value not in valid_presets:
            raise ValueError(f"Unknown preset '{value}'. Valid: {valid_presets}")
        return value


class ExternalToolCustomSpec(BaseModel):
    """Custom MCP/UTCP server connection."""

    name: str
    transport: Literal["mcp", "utcp"] = "mcp"
    connection: str
    auth_type: Literal["bearer", "oauth", "none"] = "none"
    auth_config: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""

    model_config = ConfigDict(extra="forbid")


class ExternalToolsSpec(BaseModel):
    """External tools configuration (MCP servers, UTCP APIs)."""

    presets: list[ExternalToolPresetSpec] = Field(default_factory=list)
    custom: list[ExternalToolCustomSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class FlowDependencySpec(BaseModel):
    """Flow-level dependency definition (e.g., parser, retriever)."""

    name: str
    type_hint: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow dependency name is required.")
        return value

    @field_validator("type_hint")
    @classmethod
    def _require_type_hint(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow dependency type_hint is required.")
        return value


class FlowNodePolicySpec(BaseModel):
    validate_mode: Literal["in", "out", "both", "none"] | None = Field(default=None, alias="validate")
    timeout_s: float | None = None
    max_retries: int | None = None
    backoff_base: float | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @property
    def validate(self) -> Literal["in", "out", "both", "none"] | None:  # type: ignore[override]  # pragma: no cover - passthrough
        return self.validate_mode


class FlowNodeSpec(BaseModel):
    name: str
    description: str
    policy: FlowNodePolicySpec | None = None
    input_type: str | None = None
    output_type: str | None = None
    uses: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow node name is required.")
        return value

    @field_validator("description")
    @classmethod
    def _require_description(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow node description is required.")
        return value

    @field_validator("input_type", "output_type")
    @classmethod
    def _validate_type_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("type identifier cannot be empty when provided.")
        if not _PASCAL_CASE_PATTERN.match(value):
            raise ValueError(f"type identifier '{value}' must be PascalCase (start with uppercase, alphanumeric only).")
        return value


class FlowSpec(BaseModel):
    name: str
    description: str
    dependencies: list[FlowDependencySpec] = Field(default_factory=list)
    nodes: list[FlowNodeSpec] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow name is required.")
        return value

    @field_validator("description")
    @classmethod
    def _require_description(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow description is required.")
        return value


class ServiceConfigSpec(BaseModel):
    enabled: bool = False
    base_url: str | None = None

    model_config = ConfigDict(extra="forbid")


class ServiceSpec(BaseModel):
    memory_iceberg: ServiceConfigSpec = Field(default_factory=ServiceConfigSpec)
    rag_server: ServiceConfigSpec = Field(default_factory=ServiceConfigSpec)
    wayfinder: ServiceConfigSpec = Field(default_factory=ServiceConfigSpec)

    model_config = ConfigDict(extra="forbid")


class LLMSummarizerSpec(BaseModel):
    enabled: bool = False
    model: str | None = None
    provider: str | None = None

    model_config = ConfigDict(extra="forbid")


class ReflectionCriteriaSpec(BaseModel):
    """Custom criteria for reflection quality evaluation."""

    completeness: str = "Addresses all parts of the query"
    accuracy: str = "Factually correct based on observations"
    clarity: str = "Well-explained and coherent"

    model_config = ConfigDict(extra="forbid")


class LLMReflectionSpec(BaseModel):
    enabled: bool = False
    model: str | None = None
    provider: str | None = None
    quality_threshold: float = 0.80
    max_revisions: int = 2
    criteria: ReflectionCriteriaSpec | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("quality_threshold")
    @classmethod
    def _clamp_quality(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("quality_threshold must be between 0.0 and 1.0.")
        return value


class LLMPrimarySpec(BaseModel):
    model: str
    provider: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("model")
    @classmethod
    def _require_model(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("llm.primary.model is required.")
        return value


class LLMSpec(BaseModel):
    primary: LLMPrimarySpec
    summarizer: LLMSummarizerSpec | None = None
    reflection: LLMReflectionSpec | None = None

    model_config = ConfigDict(extra="forbid")


class PlannerHintsSpec(BaseModel):
    ordering: list[str] = Field(default_factory=list)
    parallel_groups: list[list[str]] = Field(default_factory=list)
    sequential_only: list[str] = Field(default_factory=list)
    disallow: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlannerShortTermMemoryBudgetSpec(BaseModel):
    """Budget configuration for built-in short-term memory."""

    full_zone_turns: int = 5
    summary_max_tokens: int = 1000
    total_max_tokens: int = 10000
    overflow_policy: Literal["truncate_summary", "truncate_oldest", "error"] = "truncate_oldest"

    model_config = ConfigDict(extra="forbid")

    @field_validator("full_zone_turns", "summary_max_tokens", "total_max_tokens")
    @classmethod
    def _non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0.")
        return value

    @field_validator("overflow_policy")
    @classmethod
    def _validate_overflow_policy(cls, value: str) -> str:
        if value not in {"truncate_summary", "truncate_oldest", "error"}:
            raise ValueError("must be one of: truncate_summary, truncate_oldest, error.")
        return value

    @field_validator("total_max_tokens")
    @classmethod
    def _summary_within_total(cls, value: int, info) -> int:
        summary = getattr(info.data, "get", lambda *_: None)("summary_max_tokens")
        if isinstance(summary, int) and value and summary > value:
            raise ValueError("summary_max_tokens must be <= total_max_tokens.")
        return value


class PlannerShortTermMemoryIsolationSpec(BaseModel):
    """Isolation configuration for built-in short-term memory."""

    tenant_key: str = "tenant_id"
    user_key: str = "user_id"
    session_key: str = "session_id"
    require_explicit_key: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("tenant_key", "user_key", "session_key")
    @classmethod
    def _non_empty_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must be a non-empty string.")
        return value.strip()


class PlannerShortTermMemorySpec(BaseModel):
    """Built-in short-term memory configuration for ReactPlanner."""

    enabled: bool = True
    strategy: Literal["truncation", "rolling_summary", "none"] = "rolling_summary"

    budget: PlannerShortTermMemoryBudgetSpec = Field(default_factory=PlannerShortTermMemoryBudgetSpec)
    isolation: PlannerShortTermMemoryIsolationSpec = Field(default_factory=PlannerShortTermMemoryIsolationSpec)

    include_trajectory_digest: bool = True
    summarizer_model: str | None = None

    recovery_backlog_limit: int = 20
    retry_attempts: int = 3
    retry_backoff_base_s: float = 2.0
    degraded_retry_interval_s: float = 30.0

    model_config = ConfigDict(extra="forbid")

    @field_validator("summarizer_model")
    @classmethod
    def _non_empty_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("cannot be empty when provided.")
        return value.strip()

    @field_validator("recovery_backlog_limit", "retry_attempts")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0.")
        return value

    @field_validator("retry_backoff_base_s", "degraded_retry_interval_s")
    @classmethod
    def _non_negative_float(cls, value: float) -> float:
        if value < 0:
            raise ValueError("must be >= 0.")
        return value

    @field_validator("strategy")
    @classmethod
    def _validate_strategy(cls, value: str, info) -> str:
        if value not in {"truncation", "rolling_summary", "none"}:
            raise ValueError("must be one of: truncation, rolling_summary, none.")
        enabled = getattr(info.data, "get", lambda *_: True)("enabled")
        if enabled and value == "none":
            raise ValueError("enabled=true is not compatible with strategy=none.")
        if enabled is False and value in {"truncation", "rolling_summary"}:
            raise ValueError("enabled=false requires strategy=none.")
        return value


class PlannerArtifactRetentionSpec(BaseModel):
    """Retention policy for binary/large-text artifacts."""

    ttl_seconds: int = 3600
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_session_bytes: int = 500 * 1024 * 1024
    max_trace_bytes: int = 100 * 1024 * 1024
    max_artifacts_per_trace: int = 100
    max_artifacts_per_session: int = 1000
    cleanup_strategy: Literal["lru", "fifo", "none"] = "lru"

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "ttl_seconds",
        "max_artifact_bytes",
        "max_session_bytes",
        "max_trace_bytes",
        "max_artifacts_per_trace",
        "max_artifacts_per_session",
    )
    @classmethod
    def _non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0.")
        return value


class PlannerArtifactStoreSpec(BaseModel):
    """Artifact store configuration for ReactPlanner."""

    enabled: bool = False
    retention: PlannerArtifactRetentionSpec = Field(default_factory=PlannerArtifactRetentionSpec)

    model_config = ConfigDict(extra="forbid")


class PlannerRichOutputSpec(BaseModel):
    """Rich output component configuration for ReactPlanner."""

    enabled: bool = False
    allowlist: list[str] = Field(default_factory=list)
    include_prompt_catalog: bool = True
    include_prompt_examples: bool = False
    max_payload_bytes: int = 250_000
    max_total_bytes: int = 2_000_000

    model_config = ConfigDict(extra="forbid")

    @field_validator("max_payload_bytes", "max_total_bytes")
    @classmethod
    def _non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0.")
        return value


class PlannerBackgroundTasksSpec(BaseModel):
    """Background task orchestration configuration for ReactPlanner."""

    enabled: bool = False
    allow_tool_background: bool = False
    default_mode: Literal["subagent", "job"] = "subagent"
    default_merge_strategy: Literal["APPEND", "REPLACE", "HUMAN_GATED"] = "HUMAN_GATED"
    context_depth: Literal["full", "summary", "none"] = "full"
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"
    spawn_requires_confirmation: bool = False
    include_prompt_guidance: bool = True
    max_concurrent_tasks: int = Field(default=5, ge=1, le=20)
    max_tasks_per_session: int = Field(default=50, ge=1, le=200)
    task_timeout_s: int = Field(default=3600, ge=60)
    max_pending_steering: int = Field(default=2, ge=1, le=10)

    model_config = ConfigDict(extra="forbid")


class PlannerToolHintsSpec(BaseModel):
    enabled: bool = False
    top_k: int = Field(default=5, ge=1, le=20)
    include_always_loaded: bool = False
    search_type: Literal["fts", "regex", "exact"] = "fts"

    model_config = ConfigDict(extra="forbid")


class PlannerToolDirectoryGroupSpec(BaseModel):
    name: str
    title: str | None = None
    trigger: str | None = None
    task_type: Literal["browser", "api", "code", "domain", "unknown"] | None = None
    match_namespaces: list[str] = Field(default_factory=list)
    match_tags: list[str] = Field(default_factory=list)
    match_name_patterns: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlannerToolDirectorySpec(BaseModel):
    enabled: bool = False
    max_groups: int = Field(default=20, ge=1, le=100)
    max_tools_per_group: int = Field(default=6, ge=0, le=50)
    include_tool_counts: bool = True
    include_default_groups: bool = True
    groups: list[PlannerToolDirectoryGroupSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlannerToolSearchSpec(BaseModel):
    """Tool search + deferred activation configuration for ReactPlanner."""

    enabled: bool = False
    cache_dir: str = ".penguiflow"
    default_loading_mode: Literal["always", "deferred"] = "always"
    always_loaded_patterns: list[str] = Field(default_factory=lambda: ["tasks.*", "tool_search", "tool_get", "finish"])
    activation_scope: Literal["run", "session"] = "run"
    preferred_namespaces: list[str] = Field(default_factory=list)
    fts_fallback_to_regex: bool = True
    enable_incremental_index: bool = True
    rebuild_cache_on_init: bool = False
    max_search_results: int = Field(default=10, ge=1, le=50)

    hints: PlannerToolHintsSpec = Field(default_factory=PlannerToolHintsSpec)
    directory: PlannerToolDirectorySpec = Field(default_factory=PlannerToolDirectorySpec)

    model_config = ConfigDict(extra="forbid")


class PlannerSkillsDirectorySpec(BaseModel):
    """Skills directory exposure configuration."""

    enabled: bool = True
    max_entries: int = Field(default=30, ge=1, le=200)
    include_fields: list[Literal["name", "title", "trigger", "task_type"]] = Field(
        default_factory=lambda: cast(
            list[Literal["name", "title", "trigger", "task_type"]],
            ["name", "title", "trigger"],
        )
    )
    selection_strategy: Literal["pinned_then_recent", "pinned_then_top"] = "pinned_then_recent"

    model_config = ConfigDict(extra="forbid")


class PlannerSkillPackSpec(BaseModel):
    """Developer-registered skill pack."""

    name: str
    path: str
    format: Literal["md", "yaml", "json", "jsonl"] | None = None
    scope_mode: Literal["project", "tenant", "global"] = "project"
    enabled: bool = True
    update_existing_pack_skills: bool = True
    prune_missing_pack_skills: bool = True
    pinned_skill_names: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlannerSkillsSpec(BaseModel):
    """Local-first skills configuration for ReactPlanner."""

    enabled: bool = False
    cache_dir: str = ".penguiflow"
    max_tokens: int = Field(default=2000, ge=200, le=10000)
    summarize: bool = False
    redact_pii: bool = True
    scope_mode: Literal["project", "tenant", "global"] = "project"
    top_k: int = Field(default=6, ge=1, le=20)
    fts_fallback_to_regex: bool = True
    prune_packs_not_in_config: bool = True
    skill_packs: list[PlannerSkillPackSpec] = Field(default_factory=list)
    directory: PlannerSkillsDirectorySpec = Field(default_factory=PlannerSkillsDirectorySpec)

    model_config = ConfigDict(extra="forbid")


class PlannerSpec(BaseModel):
    max_iters: int = 12
    hop_budget: int = 8
    absolute_max_parallel: int = 5
    system_prompt_extra: str
    memory_prompt: str | None = None
    short_term_memory: PlannerShortTermMemorySpec | None = None
    artifact_store: PlannerArtifactStoreSpec = Field(default_factory=PlannerArtifactStoreSpec)
    rich_output: PlannerRichOutputSpec = Field(default_factory=PlannerRichOutputSpec)
    background_tasks: PlannerBackgroundTasksSpec = Field(default_factory=PlannerBackgroundTasksSpec)
    tool_search: PlannerToolSearchSpec = Field(default_factory=PlannerToolSearchSpec)
    skills: PlannerSkillsSpec = Field(default_factory=PlannerSkillsSpec)
    hints: PlannerHintsSpec | None = None
    stream_final_response: bool = False
    # When models emit multiple JSON objects in a single response, optionally
    # execute additional tool calls sequentially (guarded by read-only gating).
    multi_action_sequential: bool = False
    multi_action_read_only_only: bool = True
    multi_action_max_tools: int = Field(default=2, ge=0, le=10)

    model_config = ConfigDict(extra="forbid")

    @field_validator("system_prompt_extra")
    @classmethod
    def _require_system_prompt(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("planner.system_prompt_extra is required.")
        return value

    @field_validator("memory_prompt")
    @classmethod
    def _non_empty_memory_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("planner.memory_prompt cannot be empty when provided.")
        return value


class Spec(BaseModel):
    agent: AgentSpec
    tools: list[ToolSpec]
    external_tools: ExternalToolsSpec = Field(default_factory=ExternalToolsSpec)
    flows: list[FlowSpec] = Field(default_factory=list)
    services: ServiceSpec = Field(default_factory=ServiceSpec)
    llm: LLMSpec
    planner: PlannerSpec

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def _validate_tool_names(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    seen: set[str] = set()
    for idx, tool in enumerate(spec.tools):
        path = ("tools", idx, "name")
        line = lines.line_for(path)
        if not _TOOL_NAME_PATTERN.match(tool.name):
            suggested = _suggest_snake_case(tool.name)
            errors.append(
                SpecErrorDetail(
                    message="Tool name must be snake_case (lowercase, digits, underscores).",
                    path=path,
                    line=line,
                    suggestion=f"Use '{suggested}' instead" if suggested else None,
                )
            )
        if tool.name in _RESERVED_TOOL_NAMES:
            errors.append(
                SpecErrorDetail(
                    message=f"Tool name '{tool.name}' is reserved; choose a different identifier.",
                    path=path,
                    line=line,
                )
            )
        if tool.name in seen:
            errors.append(
                SpecErrorDetail(
                    message=f"Duplicate tool name '{tool.name}'.",
                    path=path,
                    line=line,
                )
            )
        seen.add(tool.name)
    return errors


def _validate_flows(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    tool_names = {tool.name for tool in spec.tools}
    for flow_idx, flow in enumerate(spec.flows):
        node_names = [node.name for node in flow.nodes]
        if not flow.nodes and not flow.steps:
            errors.append(
                SpecErrorDetail(
                    message="Flow must define at least one node or step.",
                    path=("flows", flow_idx),
                    line=lines.line_for(("flows", flow_idx)),
                )
            )

        if len(node_names) != len(set(node_names)):
            errors.append(
                SpecErrorDetail(
                    message="Flow node names must be unique within a flow.",
                    path=("flows", flow_idx, "nodes"),
                    line=lines.line_for(("flows", flow_idx, "nodes")),
                )
            )

        # Validate dependency names are unique
        dependency_names = {dep.name for dep in flow.dependencies}
        if len(dependency_names) != len(flow.dependencies):
            errors.append(
                SpecErrorDetail(
                    message="Flow dependency names must be unique within a flow.",
                    path=("flows", flow_idx, "dependencies"),
                    line=lines.line_for(("flows", flow_idx, "dependencies")),
                )
            )

        for node_idx, node in enumerate(flow.nodes):
            node_path = ("flows", flow_idx, "nodes", node_idx, "name")
            node_line = lines.line_for(node_path)
            if not _TOOL_NAME_PATTERN.match(node.name):
                errors.append(
                    SpecErrorDetail(
                        message="Flow node names must be snake_case.",
                        path=node_path,
                        line=node_line,
                    )
                )
            if node.name not in tool_names:
                errors.append(
                    SpecErrorDetail(
                        message=f"Flow node '{node.name}' must reference a defined tool.",
                        path=node_path,
                        line=node_line,
                    )
                )

            # Validate node 'uses' references only defined dependencies
            for uses_idx, dep_name in enumerate(node.uses):
                if dep_name not in dependency_names:
                    uses_path = ("flows", flow_idx, "nodes", node_idx, "uses", uses_idx)
                    uses_line = lines.line_for(uses_path)
                    errors.append(
                        SpecErrorDetail(
                            message=f"Node '{node.name}' references undefined dependency '{dep_name}'.",
                            path=uses_path,
                            line=uses_line,
                        )
                    )

        allowed_steps = set(node_names) | tool_names
        for step_idx, step_name in enumerate(flow.steps):
            step_path = ("flows", flow_idx, "steps", step_idx)
            step_line = lines.line_for(step_path)
            if not _TOOL_NAME_PATTERN.match(step_name):
                errors.append(
                    SpecErrorDetail(
                        message="Flow step names must be snake_case.",
                        path=step_path,
                        line=step_line,
                    )
                )
            if step_name not in allowed_steps:
                errors.append(
                    SpecErrorDetail(
                        message=(f"Flow step '{step_name}' is not defined in nodes and must reference a known tool."),
                        path=step_path,
                        line=step_line,
                    )
                )
    return errors


def _validate_services(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    default_urls = {
        "memory_iceberg": "http://localhost:8000",
        "rag_server": "http://localhost:8081",
        "wayfinder": "http://localhost:8082",
    }
    for field_name in ("memory_iceberg", "rag_server", "wayfinder"):
        cfg: ServiceConfigSpec = getattr(spec.services, field_name)
        if cfg.enabled and not cfg.base_url:
            path = ("services", field_name, "base_url")
            line = lines.line_for(path) or lines.line_for(("services", field_name)) or lines.line_for(("services",))
            errors.append(
                SpecErrorDetail(
                    message=f"{field_name} base_url is required when enabled.",
                    path=path,
                    line=line,
                    suggestion=f"Set base_url: {default_urls[field_name]}",
                )
            )
    return errors


def _validate_external_tools(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    native_names = {tool.name for tool in spec.tools}

    oauth_presets = {"github", "slack", "google-drive"}
    seen_presets: set[str] = set()
    for idx, preset in enumerate(spec.external_tools.presets):
        preset_path = ("external_tools", "presets", idx, "preset")
        if preset.preset in seen_presets:
            errors.append(
                SpecErrorDetail(
                    message=f"Duplicate external preset '{preset.preset}'.",
                    path=preset_path,
                    line=lines.line_for(preset_path),
                )
            )
        seen_presets.add(preset.preset)

        uses_oauth = (preset.auth_override == "oauth") or (
            preset.auth_override is None and preset.preset in oauth_presets
        )
        if uses_oauth and not spec.agent.flags.hitl:
            auth_path = ("external_tools", "presets", idx, "auth_override")
            errors.append(
                SpecErrorDetail(
                    message=f"Preset '{preset.preset}' uses OAuth; enable agent.flags.hitl for HITL flows.",
                    path=auth_path,
                    line=lines.line_for(auth_path) or lines.line_for(preset_path),
                    suggestion="Set agent.flags.hitl: true or switch auth_override to bearer/none",
                )
            )

    seen_custom: set[str] = set()
    for idx, custom in enumerate(spec.external_tools.custom):
        name_path = ("external_tools", "custom", idx, "name")
        line = lines.line_for(name_path)
        if not _TOOL_NAME_PATTERN.match(custom.name):
            suggested = _suggest_snake_case(custom.name)
            errors.append(
                SpecErrorDetail(
                    message="External tool names must be snake_case (lowercase, digits, underscores).",
                    path=name_path,
                    line=line,
                    suggestion=f"Use '{suggested}' instead" if suggested else None,
                )
            )
        if custom.name in _RESERVED_TOOL_NAMES:
            errors.append(
                SpecErrorDetail(
                    message=f"External tool name '{custom.name}' is reserved; choose a different identifier.",
                    path=name_path,
                    line=line,
                )
            )
        if custom.name in seen_custom or custom.name in native_names:
            errors.append(
                SpecErrorDetail(
                    message=f"Duplicate external tool name '{custom.name}'.",
                    path=name_path,
                    line=line,
                )
            )
        seen_custom.add(custom.name)

        if custom.auth_type == "oauth" and not spec.agent.flags.hitl:
            auth_path = ("external_tools", "custom", idx, "auth_type")
            errors.append(
                SpecErrorDetail(
                    message=f"External tool '{custom.name}' uses OAuth; enable agent.flags.hitl for HITL flows.",
                    path=auth_path,
                    line=lines.line_for(auth_path),
                    suggestion="Set agent.flags.hitl: true or switch auth_type to bearer/none",
                )
            )

    return errors


def _validate_cross_fields(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    if spec.agent.flags.memory and not spec.planner.memory_prompt:
        line = lines.line_for(("planner", "memory_prompt")) or lines.line_for(("planner",)) or lines.line_for(())
        errors.append(
            SpecErrorDetail(
                message="planner.memory_prompt is required when memory is enabled.",
                path=("planner", "memory_prompt"),
                line=line,
                suggestion="Add memory_prompt to planner, or set agent.flags.memory: false",
            )
        )
    if spec.planner.rich_output.enabled:
        allowlist = set(spec.planner.rich_output.allowlist)
        interactive = {"form", "confirm", "select_option"}
        interactive_enabled = not allowlist or bool(allowlist & interactive)
        if interactive_enabled and not spec.agent.flags.hitl:
            path = ("agent", "flags", "hitl")
            errors.append(
                SpecErrorDetail(
                    message="Interactive rich output components require agent.flags.hitl: true.",
                    path=path,
                    line=lines.line_for(path),
                    suggestion=(
                        "Enable agent.flags.hitl or remove interactive components from planner.rich_output.allowlist."
                    ),
                )
            )
    # Background tasks validation
    bg = spec.planner.background_tasks
    if bg.enabled:
        if bg.default_merge_strategy == "HUMAN_GATED" and not spec.agent.flags.hitl:
            path = ("planner", "background_tasks", "default_merge_strategy")
            errors.append(
                SpecErrorDetail(
                    message="HUMAN_GATED merge strategy requires agent.flags.hitl: true.",
                    path=path,
                    line=lines.line_for(path),
                    suggestion="Enable agent.flags.hitl or use APPEND/REPLACE strategy.",
                )
            )
        if bg.spawn_requires_confirmation and not spec.agent.flags.hitl:
            path = ("planner", "background_tasks", "spawn_requires_confirmation")
            errors.append(
                SpecErrorDetail(
                    message="spawn_requires_confirmation requires agent.flags.hitl: true.",
                    path=path,
                    line=lines.line_for(path),
                    suggestion="Enable agent.flags.hitl or disable spawn_requires_confirmation.",
                )
            )
    # Tool background validation
    for idx, tool in enumerate(spec.tools):
        if tool.background and tool.background.enabled:
            if not spec.agent.flags.background_tasks:
                tool_path = ("tools", idx, "background", "enabled")
                errors.append(
                    SpecErrorDetail(
                        message=(
                            f"Tool '{tool.name}' has background enabled but agent.flags.background_tasks is false."
                        ),
                        path=tool_path,
                        line=lines.line_for(tool_path),
                        suggestion="Set agent.flags.background_tasks: true.",
                    )
                )
            if not spec.planner.background_tasks.allow_tool_background:
                tool_path = ("tools", idx, "background", "enabled")
                errors.append(
                    SpecErrorDetail(
                        message=(
                            f"Tool '{tool.name}' has background enabled "
                            "but planner.background_tasks.allow_tool_background is false."
                        ),
                        path=tool_path,
                        line=lines.line_for(tool_path),
                        suggestion="Set planner.background_tasks.allow_tool_background: true.",
                    )
                )
            if tool.background.default_merge_strategy == "HUMAN_GATED" and not spec.agent.flags.hitl:
                tool_path = ("tools", idx, "background", "default_merge_strategy")
                errors.append(
                    SpecErrorDetail(
                        message=(f"Tool '{tool.name}' uses HUMAN_GATED merge but agent.flags.hitl is false."),
                        path=tool_path,
                        line=lines.line_for(tool_path),
                        suggestion="Enable agent.flags.hitl or use APPEND/REPLACE strategy.",
                    )
                )
    return errors


def _semantic_validations(spec: Spec, lines: LineIndex) -> list[SpecErrorDetail]:
    errors: list[SpecErrorDetail] = []
    errors.extend(_validate_tool_names(spec, lines))
    errors.extend(_validate_flows(spec, lines))
    errors.extend(_validate_services(spec, lines))
    errors.extend(_validate_external_tools(spec, lines))
    errors.extend(_validate_cross_fields(spec, lines))
    return errors


def parse_spec(content: str, *, source: str | Path = "<string>") -> Spec:
    """Parse and validate an agent spec from YAML text."""
    source_path = Path(source)
    data, lines = _load_yaml(content, source=source_path)
    if not isinstance(data, dict):
        raise SpecValidationError(
            source_path,
            [SpecErrorDetail(message="Spec root must be a mapping.", line=lines.line_for(()))],
        )
    try:
        spec = Spec.model_validate(data)
    except ValidationError as exc:
        raise SpecValidationError(source_path, _errors_from_pydantic(exc, lines=lines)) from None

    semantic_errors = _semantic_validations(spec, lines)
    if semantic_errors:
        raise SpecValidationError(source_path, semantic_errors)
    return spec


def load_spec(path: str | Path) -> Spec:
    """Load and validate a spec from disk."""
    path = Path(path)
    content = path.read_text()
    return parse_spec(content, source=path)


__all__ = [
    "AgentFlagsSpec",
    "AgentSpec",
    "FlowDependencySpec",
    "FlowNodePolicySpec",
    "FlowNodeSpec",
    "FlowSpec",
    "LLMPrimarySpec",
    "LLMReflectionSpec",
    "LLMSpec",
    "LLMSummarizerSpec",
    "LineIndex",
    "ExternalToolPresetSpec",
    "ExternalToolCustomSpec",
    "ExternalToolsSpec",
    "PlannerBackgroundTasksSpec",
    "PlannerHintsSpec",
    "PlannerShortTermMemoryBudgetSpec",
    "PlannerShortTermMemoryIsolationSpec",
    "PlannerShortTermMemorySpec",
    "PlannerSpec",
    "ServiceConfigSpec",
    "ServiceSpec",
    "Spec",
    "SpecValidationError",
    "ToolBackgroundSpec",
    "ToolSpec",
    "TypeExpression",
    "UnsupportedTypeAnnotation",
    "load_spec",
    "parse_spec",
    "parse_type_annotation",
]
