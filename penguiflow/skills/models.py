"""Models and configuration for the skills subsystem."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    name: str
    path: str
    format: SkillPackFormat | None = None
    scope_mode: SkillScopeMode = "project"
    enabled: bool = True
    update_existing_pack_skills: bool = True
    pinned_skill_names: list[str] = Field(default_factory=list)


class SkillsDirectoryConfig(BaseModel):
    enabled: bool = True
    max_entries: int = Field(default=30, ge=1, le=200)
    include_fields: list[SkillDirectoryField] = Field(default_factory=_default_directory_fields)
    selection_strategy: Literal["pinned_then_recent", "pinned_then_top"] = "pinned_then_recent"


class SkillsConfig(BaseModel):
    enabled: bool = False
    cache_dir: str = ".penguiflow"
    max_tokens: int = Field(default=2000, ge=200, le=10000)
    summarize: bool = False
    redact_pii: bool = True
    scope_mode: SkillScopeMode = "project"
    skill_packs: list[SkillPackConfig] = Field(default_factory=list)
    directory: SkillsDirectoryConfig = Field(default_factory=SkillsDirectoryConfig)
    fts_fallback_to_regex: bool = True
    top_k: int = Field(default=6, ge=1, le=20)


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
    tools: list[Mapping[str, Any]] | None = None

    @model_validator(mode="after")
    def _validate_fields(self) -> SkillDefinition:
        self.tags = _clean_str_list(self.tags)
        self.preconditions = _clean_str_list(self.preconditions)
        self.failure_modes = _clean_str_list(self.failure_modes)
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


class SkillSearchQuery(BaseModel):
    query: str
    search_type: SkillSearchType = "fts"
    limit: int = Field(default=8, ge=1, le=20)
    task_type: SkillTaskType | None = None


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
    task_type: SkillTaskType | None = None


class SkillListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    task_type: SkillTaskType | None = None
    origin: SkillOrigin | None = None


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


__all__ = [
    "RetrievalResponse",
    "SkillDefinition",
    "SkillDirectoryEntry",
    "SkillDirectoryField",
    "SkillListEntry",
    "SkillListRequest",
    "SkillListResponse",
    "SkillOrigin",
    "SkillPackConfig",
    "SkillPackFormat",
    "SkillPackLoadResult",
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
