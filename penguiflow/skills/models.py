"""Models and configuration for the skills subsystem."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SkillTaskType = Literal["browser", "api", "code", "domain", "unknown"]
SkillOrigin = Literal["pack", "learned"]
SkillScopeMode = Literal["project", "tenant", "global"]
SkillPackFormat = Literal["md", "yaml", "json", "jsonl"]
SkillDirectoryField = Literal["name", "title", "trigger", "task_type"]
SkillSearchType = Literal["fts", "regex", "exact"]


def _default_directory_fields() -> list[SkillDirectoryField]:
    return ["name", "title", "trigger"]


def _clean_str_list(value: Sequence[Any] | None) -> list[str]:
    if not value:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return cleaned


def _coerce_steps(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


class SkillPackConfig(BaseModel):
    """Configuration for a single skill pack loaded into the skills subsystem.

    A skill pack is a file or directory of skill definitions (see
    :class:`SkillDefinition`) ingested into the local skill store on load. Each pack is
    tracked by ``name`` so it can be updated or pruned independently of other packs.
    """

    name: str = Field(description="Unique identifier for this pack, used for provenance tracking and pruning.")
    path: str = Field(description="Filesystem path to the pack source (file or directory); interpreted per `format`.")
    format: SkillPackFormat | None = Field(
        default=None,
        description="Serialization format of the pack contents (md/yaml/json/jsonl). None auto-detects from `path`.",
    )
    scope_mode: SkillScopeMode = Field(
        default="project",
        description="Visibility scope applied to skills loaded from this pack: project, tenant, or global.",
    )
    enabled: bool = Field(default=True, description="Whether this pack is actively loaded and kept in the store.")
    update_existing_pack_skills: bool = Field(
        default=True,
        description="Whether to overwrite already-stored skills from this pack with updated pack content on reload.",
    )
    prune_missing_pack_skills: bool = Field(
        default=True,
        description="Whether to delete stored skills from this pack no longer present in the pack source.",
    )
    pinned_skill_names: list[str] = Field(
        default_factory=list,
        description="Skill names from this pack to always surface first in the skills directory listing.",
    )


class SkillsDirectoryConfig(BaseModel):
    """Configuration for the always-available skills directory listing.

    The directory is a lightweight summary of skills (pinned entries plus recent/top
    ones) surfaced to callers without requiring a search query.
    """

    enabled: bool = Field(default=True, description="Whether the skills directory listing is generated at all.")
    max_entries: int = Field(
        default=30, ge=1, le=200, description="Maximum number of entries returned in the directory."
    )
    include_fields: list[SkillDirectoryField] = Field(
        default_factory=_default_directory_fields,
        description="Which skill fields to include per directory entry.",
    )
    selection_strategy: Literal["pinned_then_recent", "pinned_then_top"] = Field(
        default="pinned_then_recent",
        description=(
            "How to fill directory slots beyond pinned skills: by most recently used "
            "(pinned_then_recent) or by highest use count (pinned_then_top)."
        ),
    )


class SkillProposalConfig(BaseModel):
    """Configuration for the skill-proposal (learn-a-skill) feature."""

    enabled: bool = Field(default=False, description="Whether skill proposal drafting is enabled.")


class SkillsConfig(BaseModel):
    """Top-level configuration for the skills subsystem.

    Controls whether skills are enabled, where they are cached, how retrieval results
    are budgeted/redacted, and which packs feed the local skill store.
    """

    enabled: bool = Field(default=False, description="Whether the skills subsystem is active.")
    cache_dir: str = Field(default=".penguiflow", description="Directory used to store the local skills database.")
    max_tokens: int = Field(
        default=2000,
        ge=200,
        le=10000,
        description="Token budget for formatted skill context injected into prompts.",
    )
    summarize: bool = Field(
        default=False, description="Whether to summarize skill content when it exceeds the token budget."
    )
    redact_pii: bool = Field(
        default=True, description="Whether to redact PII from skill text before returning it to callers."
    )
    scope_mode: SkillScopeMode = Field(
        default="project", description="Default visibility scope for skills without an explicit scope."
    )
    skill_packs: list[SkillPackConfig] = Field(
        default_factory=list, description="Skill packs to load into the local store."
    )
    directory: SkillsDirectoryConfig = Field(
        default_factory=SkillsDirectoryConfig,
        description="Configuration for the always-available skills directory listing.",
    )
    proposal: SkillProposalConfig = Field(
        default_factory=SkillProposalConfig,
        description="Configuration for the skill-proposal (learn-a-skill) feature.",
    )
    fts_fallback_to_regex: bool = Field(
        default=True,
        description="Whether to fall back to regex search when full-text search (FTS) is unavailable.",
    )
    top_k: int = Field(default=6, ge=1, le=20, description="Default number of skills returned by relevance retrieval.")

    # If enabled, remove pack-origin skills for packs that are no longer present
    # (or are disabled) in the current config.
    prune_packs_not_in_config: bool = Field(
        default=True,
        description="Whether to remove pack-origin skills for packs no longer present (or disabled) in this config.",
    )


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    title: str | None = None
    description: str | None = None
    trigger: str
    task_type: SkillTaskType = "unknown"
    tags: list[str] = Field(default_factory=list)
    steps: list[str]
    preconditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    required_tool_names: list[str] = Field(default_factory=list)
    required_namespaces: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    tools: list[Mapping[str, Any]] | None = None

    @model_validator(mode="after")
    def _validate_fields(self) -> SkillDefinition:
        self.tags = _clean_str_list(self.tags)
        self.preconditions = _clean_str_list(self.preconditions)
        self.failure_modes = _clean_str_list(self.failure_modes)
        self.required_tool_names = _clean_str_list(self.required_tool_names)
        self.required_namespaces = _clean_str_list(self.required_namespaces)
        self.required_tags = _clean_str_list(self.required_tags)
        self.steps = _coerce_steps(self.steps)
        self.trigger = str(self.trigger).strip() if self.trigger is not None else ""
        if not self.trigger:
            raise ValueError("Skill trigger must be non-empty")
        if not self.steps:
            raise ValueError("Skill steps must be a non-empty list")
        if self.title is not None:
            self.title = self.title.strip() or None
        if self.description is not None:
            self.description = self.description.strip() or None
        if self.name is not None:
            self.name = self.name.strip() or None
        return self

    def extra_payload(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class SkillRecord(BaseModel):
    id: str
    name: str
    title: str | None = None
    description: str | None = None
    trigger: str
    task_type: SkillTaskType = "unknown"
    tags: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    required_tool_names: list[str] = Field(default_factory=list)
    required_namespaces: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    origin: SkillOrigin = "pack"
    origin_ref: str | None = None
    scope_mode: SkillScopeMode = "project"
    scope_tenant_id: str | None = None
    scope_project_id: str | None = None
    content_hash: str
    created_at: int
    updated_at: int
    last_used: int
    use_count: int
    extra: dict[str, Any] = Field(default_factory=dict)


class SkillQuery(BaseModel):
    task: str
    search_type: SkillSearchType = "fts"
    top_k: int = Field(default=6, ge=1, le=20)
    task_type: SkillTaskType | None = None
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional AND filter against the skill's declared tags. Empty list "
            "disables the filter (back-compat default)."
        ),
    )
    namespace: str | None = Field(
        default=None,
        description=(
            "Optional dot-prefix namespace filter. Matches skills whose name "
            "equals the namespace or starts with '<namespace>.'. None disables "
            "the filter."
        ),
    )


class SkillSearchQuery(BaseModel):
    query: str
    search_type: SkillSearchType = "fts"
    limit: int = Field(default=8, ge=1, le=20)
    task_type: SkillTaskType | None = None
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional AND filter against the skill's declared tags. Empty list "
            "disables the filter (back-compat default)."
        ),
    )
    namespace: str | None = Field(
        default=None,
        description=(
            "Optional dot-prefix namespace filter. Matches skills whose name "
            "equals the namespace or starts with '<namespace>.'. None disables "
            "the filter."
        ),
    )


class SkillSearchResult(BaseModel):
    name: str
    title: str | None = None
    trigger: str | None = None
    task_type: SkillTaskType | None = None
    score: float


class SkillSearchResponse(BaseModel):
    skills: list[SkillSearchResult]
    query: str
    search_type: SkillSearchType


class SkillResultDetailed(BaseModel):
    name: str
    title: str | None = None
    trigger: str
    steps: list[str]
    preconditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    required_tool_names: list[str] = Field(default_factory=list)
    required_namespaces: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    task_type: SkillTaskType | None = None


class SkillListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    task_type: SkillTaskType | None = None
    origin: SkillOrigin | None = None
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional AND filter against the skill's declared tags. Empty list "
            "disables the filter (back-compat default)."
        ),
    )
    namespace: str | None = Field(
        default=None,
        description=(
            "Optional dot-prefix namespace filter. Matches skills whose name "
            "equals the namespace or starts with '<namespace>.'. None disables "
            "the filter."
        ),
    )


class SkillListEntry(BaseModel):
    name: str
    title: str | None = None
    trigger: str | None = None
    task_type: SkillTaskType | None = None


class SkillListResponse(BaseModel):
    skills: list[SkillListEntry]
    page: int
    page_size: int
    total: int


class SkillDirectoryEntry(BaseModel):
    name: str
    title: str | None = None
    trigger: str | None = None
    task_type: SkillTaskType | None = None


@dataclass(frozen=True, slots=True)
class SkillCapabilityContext:
    """Snapshot of which tools/namespaces/tags a caller is currently allowed to use.

    Used to filter and redact skills that reference tools the caller cannot invoke
    (e.g. because a tool was disabled or scoped out for this request). Typically built
    via :func:`penguiflow.skills.provider.build_skill_capability_context`.

    Attributes:
        all_tool_names: Every tool name known in the current execution context.
        allowed_tool_names: Subset of `all_tool_names` the caller is permitted to invoke.
        allowed_namespaces: Tool namespaces (prefix before the first '.') derived from
            `allowed_tool_names`.
        allowed_tool_tags: Tags declared on tools in `allowed_tool_names`.
    """

    all_tool_names: set[str] = field(default_factory=set)
    allowed_tool_names: set[str] = field(default_factory=set)
    allowed_namespaces: set[str] = field(default_factory=set)
    allowed_tool_tags: set[str] = field(default_factory=set)


class SkillProposalDraft(BaseModel):
    """A candidate skill produced by the proposal pipeline, pending review.

    Callers typically show the draft to a human (or a higher-level agent) for approval
    before persisting ``skill`` as a real, usable skill.
    """

    skill: SkillDefinition = Field(description="The proposed skill definition, ready to persist if approved.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues detected while drafting the skill (e.g. missing detail).",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made when the source material was ambiguous or incomplete.",
    )


class SkillProposeRequest(BaseModel):
    """Request to draft a new skill from unstructured source material.

    ``source_material`` is the raw text (e.g. a transcript, log, or write-up) that the
    proposal pipeline analyzes to synthesize a :class:`SkillDefinition`.
    """

    source_material: str = Field(description="Raw text analyzed to synthesize the skill draft. Must be non-empty.")
    task_type: SkillTaskType | None = Field(
        default=None, description="Optional hint for the skill's task_type classification."
    )
    title_hint: str | None = Field(default=None, description="Optional suggested title for the drafted skill.")
    trigger_hint: str | None = Field(
        default=None, description="Optional suggested trigger phrase for the drafted skill."
    )
    required_tool_names: list[str] = Field(
        default_factory=list, description="Tool names the drafted skill should declare as required."
    )
    required_namespaces: list[str] = Field(
        default_factory=list, description="Tool namespaces the drafted skill should declare as required."
    )
    required_tags: list[str] = Field(
        default_factory=list, description="Tool tags the drafted skill should declare as required."
    )

    @model_validator(mode="after")
    def _validate_fields(self) -> SkillProposeRequest:
        self.source_material = str(self.source_material).strip()
        if not self.source_material:
            raise ValueError("source_material must be non-empty")
        self.required_tool_names = _clean_str_list(self.required_tool_names)
        self.required_namespaces = _clean_str_list(self.required_namespaces)
        self.required_tags = _clean_str_list(self.required_tags)
        if self.title_hint is not None:
            self.title_hint = self.title_hint.strip() or None
        if self.trigger_hint is not None:
            self.trigger_hint = self.trigger_hint.strip() or None
        return self


class SkillProposeResponse(BaseModel):
    """Response wrapping a drafted skill proposal."""

    draft: SkillProposalDraft = Field(
        description="The drafted skill, warnings, and assumptions produced by the pipeline."
    )


class RetrievalResponse(BaseModel):
    skills: list[SkillResultDetailed]
    formatted_context: str
    query: str
    search_type: SkillSearchType
    top_k: int
    raw_tokens_est: int
    final_tokens_est: int
    was_summarized: bool


@dataclass(frozen=True, slots=True)
class SkillPackLoadResult:
    pack_name: str
    skill_count: int
    updated_count: int
    pruned_count: int = 0


__all__ = [
    "RetrievalResponse",
    "SkillDefinition",
    "SkillDirectoryEntry",
    "SkillDirectoryField",
    "SkillCapabilityContext",
    "SkillListEntry",
    "SkillListRequest",
    "SkillListResponse",
    "SkillOrigin",
    "SkillPackConfig",
    "SkillPackFormat",
    "SkillPackLoadResult",
    "SkillProposalConfig",
    "SkillProposalDraft",
    "SkillProposeRequest",
    "SkillProposeResponse",
    "SkillQuery",
    "SkillRecord",
    "SkillSearchType",
    "SkillScopeMode",
    "SkillSearchQuery",
    "SkillSearchResponse",
    "SkillSearchResult",
    "SkillTaskType",
    "SkillResultDetailed",
    "SkillsConfig",
    "SkillsDirectoryConfig",
]
