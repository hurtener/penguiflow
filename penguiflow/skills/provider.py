"""Skill provider interfaces and local provider implementation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeVar, cast

from .local_store import LocalSkillStore
from .models import (
    RetrievalResponse,
    SkillCapabilityContext,
    SkillDirectoryEntry,
    SkillListEntry,
    SkillListRequest,
    SkillListResponse,
    SkillPackConfig,
    SkillPackLoadResult,
    SkillQuery,
    SkillRecord,
    SkillResultDetailed,
    SkillsConfig,
    SkillScopeMode,
    SkillsDirectoryConfig,
    SkillSearchQuery,
    SkillSearchResponse,
    SkillSearchResult,
    SkillSearchType,
)
from .pack_loader import SkillPackLoader
from .redaction import redact_pii, redact_tool_references


class SkillProvider(Protocol):
    async def get_relevant(
        self,
        query: SkillQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> RetrievalResponse: ...

    async def search(
        self,
        query: SkillSearchQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillSearchResponse: ...

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> list[SkillResultDetailed]: ...

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillListResponse: ...

    async def directory(
        self,
        config: SkillsDirectoryConfig,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> Sequence[SkillDirectoryEntry]: ...

    async def format_for_injection(
        self,
        skills: Sequence[SkillResultDetailed],
        *,
        max_tokens: int,
    ) -> tuple[str, int, int, bool]: ...


SkillProviderFactory = Callable[[SkillsConfig], SkillProvider]


def _estimate_tokens(text: str) -> int:
    return int(len(text) / 4)


def _build_scope_filter(tool_context: Mapping[str, object]) -> tuple[str, list[object]]:
    tenant_id = tool_context.get("tenant_id")
    project_id = tool_context.get("project_id")
    parts: list[str] = ["scope_mode = 'global'"]
    params: list[object] = []
    if tenant_id:
        parts.append("(scope_mode = 'tenant' AND (scope_tenant_id IS NULL OR scope_tenant_id = ?))")
        params.append(str(tenant_id))
    else:
        parts.append("(scope_mode = 'tenant' AND scope_tenant_id IS NULL)")
    if project_id:
        parts.append("(scope_mode = 'project' AND (scope_project_id IS NULL OR scope_project_id = ?))")
        params.append(str(project_id))
    else:
        parts.append("(scope_mode = 'project' AND scope_project_id IS NULL)")
    return " OR ".join(parts), params


def build_skill_capability_context(
    *,
    execution_specs: Mapping[str, object] | None = None,
    all_tool_names: Iterable[str] | None = None,
    allowed_tool_names: Iterable[str] | None = None,
) -> SkillCapabilityContext:
    all_names = set(str(name) for name in (all_tool_names or ()) if str(name).strip())
    if not all_names and execution_specs is not None:
        all_names = {str(name) for name in execution_specs.keys() if str(name).strip()}

    if allowed_tool_names is None:
        allowed_names = set(all_names)
    else:
        allowed_names = {str(name) for name in allowed_tool_names if str(name).strip()}

    allowed_namespaces: set[str] = set()
    allowed_tool_tags: set[str] = set()
    for tool_name in allowed_names:
        if "." in tool_name:
            allowed_namespaces.add(tool_name.split(".", 1)[0])
        else:
            allowed_namespaces.add(tool_name)
        if execution_specs is None:
            continue
        spec = execution_specs.get(tool_name)
        tags = getattr(spec, "tags", ()) if spec is not None else ()
        allowed_tool_tags.update(str(tag) for tag in tags if str(tag).strip())

    return SkillCapabilityContext(
        all_tool_names=all_names,
        allowed_tool_names=allowed_names,
        allowed_namespaces=allowed_namespaces,
        allowed_tool_tags=allowed_tool_tags,
    )


def _tool_redaction_sets(
    capability_context: SkillCapabilityContext | None,
) -> tuple[set[str], bool]:
    if capability_context is None or not capability_context.all_tool_names:
        return set(), False
    disallowed = capability_context.all_tool_names - capability_context.allowed_tool_names
    tool_search_allowed = "tool_search" in capability_context.allowed_tool_names
    return disallowed, tool_search_allowed


def _skill_is_applicable(skill: SkillRecord, capability_context: SkillCapabilityContext | None) -> bool:
    if capability_context is None:
        return True
    if skill.required_tool_names and not set(skill.required_tool_names).issubset(capability_context.allowed_tool_names):
        return False
    if skill.required_namespaces and not set(skill.required_namespaces).issubset(capability_context.allowed_namespaces):
        return False
    if skill.required_tags and not set(skill.required_tags).issubset(capability_context.allowed_tool_tags):
        return False
    return True


def _redact_text(
    text: str | None,
    *,
    redact_pii_enabled: bool,
    disallowed_tools: set[str],
    tool_search_available: bool,
) -> str | None:
    if text is None:
        return None
    value = text
    if redact_pii_enabled:
        value = redact_pii(value)
    if disallowed_tools:
        value = redact_tool_references(
            value,
            disallowed_tools,
            tool_search_available=tool_search_available,
        )
    return value


def _redact_skill(
    skill: SkillRecord,
    *,
    redact_pii_enabled: bool,
    disallowed_tools: set[str],
    tool_search_available: bool,
) -> SkillResultDetailed:
    return SkillResultDetailed(
        name=skill.name,
        title=_redact_text(
            skill.title,
            redact_pii_enabled=redact_pii_enabled,
            disallowed_tools=disallowed_tools,
            tool_search_available=tool_search_available,
        ),
        trigger=_redact_text(
            skill.trigger,
            redact_pii_enabled=redact_pii_enabled,
            disallowed_tools=disallowed_tools,
            tool_search_available=tool_search_available,
        )
        or "",
        steps=[
            _redact_text(
                step,
                redact_pii_enabled=redact_pii_enabled,
                disallowed_tools=disallowed_tools,
                tool_search_available=tool_search_available,
            )
            or ""
            for step in skill.steps
        ],
        preconditions=[
            _redact_text(
                item,
                redact_pii_enabled=redact_pii_enabled,
                disallowed_tools=disallowed_tools,
                tool_search_available=tool_search_available,
            )
            or ""
            for item in skill.preconditions
        ],
        failure_modes=[
            _redact_text(
                item,
                redact_pii_enabled=redact_pii_enabled,
                disallowed_tools=disallowed_tools,
                tool_search_available=tool_search_available,
            )
            or ""
            for item in skill.failure_modes
        ],
        required_tool_names=list(skill.required_tool_names),
        required_namespaces=list(skill.required_namespaces),
        required_tags=list(skill.required_tags),
        task_type=skill.task_type,
    )


def _format_skills_for_injection(
    skills: Sequence[SkillResultDetailed],
    *,
    max_tokens: int,
) -> tuple[str, int, int, bool]:
    if not skills:
        return "", 0, 0, False
    header = ["<skills_context>", "Relevant skills (use skill_get for full details):"]
    footer = ["</skills_context>"]
    lines: list[str] = []
    was_summarized = False

    def _skill_block(
        skill: SkillResultDetailed,
        *,
        include_optional: bool,
        max_steps: int | None,
    ) -> list[str]:
        block = [f"- name: {skill.name}"]
        if skill.title:
            block.append(f"  title: {skill.title}")
        block.append(f"  trigger: {skill.trigger}")
        if skill.required_tool_names:
            block.append(f"  required_tool_names: {', '.join(skill.required_tool_names)}")
        if skill.required_namespaces:
            block.append(f"  required_namespaces: {', '.join(skill.required_namespaces)}")
        if skill.required_tags:
            block.append(f"  required_tags: {', '.join(skill.required_tags)}")
        steps = skill.steps
        if max_steps is not None:
            steps = steps[:max_steps]
        block.append("  steps:")
        for step in steps:
            block.append(f"    - {step}")
        if include_optional:
            if skill.preconditions:
                block.append("  preconditions:")
                for item in skill.preconditions:
                    block.append(f"    - {item}")
            if skill.failure_modes:
                block.append("  failure_modes:")
                for item in skill.failure_modes:
                    block.append(f"    - {item}")
        return block

    for skill in skills:
        attempts = [
            (True, None),
            (False, None),
            (False, min(3, len(skill.steps))),
        ]
        added = False
        for include_optional, max_steps in attempts:
            candidate_lines = (
                header
                + lines
                + _skill_block(
                    skill,
                    include_optional=include_optional,
                    max_steps=max_steps,
                )
                + footer
            )
            candidate_text = "\n".join(candidate_lines)
            if _estimate_tokens(candidate_text) <= max_tokens:
                lines.extend(_skill_block(skill, include_optional=include_optional, max_steps=max_steps))
                if include_optional is False or max_steps is not None:
                    was_summarized = True
                added = True
                break
        if not added:
            was_summarized = True
            break

    text = "\n".join(header + lines + footer) if lines else ""
    raw_tokens = _estimate_tokens("\n".join(header + lines + footer)) if lines else 0
    final_tokens = _estimate_tokens(text) if text else 0
    return text, raw_tokens, final_tokens, was_summarized


_T = TypeVar("_T")


def _dedupe_by_name(items: Sequence[_T], key: Callable[[_T], str]) -> list[_T]:
    seen: set[str] = set()
    deduped: list[_T] = []
    for item in items:
        item_key = key(item)
        if item_key in seen:
            continue
        seen.add(item_key)
        deduped.append(item)
    return deduped


class LocalSkillProvider:
    def __init__(self, config: SkillsConfig, *, store: LocalSkillStore | None = None) -> None:
        self._config = config
        db_path = Path(config.cache_dir) / "skills.db"
        self._store = store or LocalSkillStore(
            db_path=db_path,
            fts_fallback_to_regex=config.fts_fallback_to_regex,
        )
        self._loader = SkillPackLoader()
        self._pinned_names = _collect_pinned_names(config.skill_packs)

    def load_packs(self) -> list[SkillPackLoadResult]:
        results: list[SkillPackLoadResult] = []
        keep_packs: set[tuple[str, SkillScopeMode]] = {
            (pack.name, pack.scope_mode) for pack in self._config.skill_packs if pack.enabled
        }
        for pack in self._config.skill_packs:
            if not pack.enabled:
                continue
            skills = self._loader.load_pack(pack)
            updated = 0
            for skill in skills:
                inserted, changed = self._store.upsert_pack_skill(
                    skill,
                    pack_name=pack.name,
                    scope_mode=pack.scope_mode,
                    update_existing=pack.update_existing_pack_skills,
                )
                if inserted or changed:
                    updated += 1

            pruned = 0
            if pack.prune_missing_pack_skills:
                pruned = self._store.prune_pack_skills(
                    pack_name=pack.name,
                    scope_mode=pack.scope_mode,
                    keep_names=[skill.name for skill in skills if skill.name],
                )
            results.append(
                SkillPackLoadResult(
                    pack_name=pack.name,
                    skill_count=len(skills),
                    updated_count=updated,
                    pruned_count=pruned,
                )
            )

        if self._config.prune_packs_not_in_config:
            removed = self._store.prune_packs_not_in_config(keep_packs=keep_packs)
            for pack_name, scope_mode, removed_count in removed:
                results.append(
                    SkillPackLoadResult(
                        pack_name=f"{pack_name} ({scope_mode})",
                        skill_count=0,
                        updated_count=0,
                        pruned_count=removed_count,
                    )
                )
        return results

    def _applicable_records(
        self,
        records: Sequence[SkillRecord],
        capability_context: SkillCapabilityContext | None,
    ) -> list[SkillRecord]:
        return [record for record in records if _skill_is_applicable(record, capability_context)]

    async def get_relevant(
        self,
        query: SkillQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> RetrievalResponse:
        scope_clause, scope_params = _build_scope_filter(tool_context)
        results, effective = self._store.search(
            query.task,
            search_type=query.search_type,
            limit=query.top_k,
            task_type=query.task_type,
            scope_clause=scope_clause,
            scope_params=scope_params,
        )
        effective = cast(SkillSearchType, effective)
        names = [item["name"] for item in results]
        records = self._store.get_by_name(names, scope_clause=scope_clause, scope_params=scope_params)
        applicable = self._applicable_records(records, capability_context)
        disallowed, tool_search_allowed = _tool_redaction_sets(capability_context)
        detailed = [
            _redact_skill(
                record,
                redact_pii_enabled=self._config.redact_pii,
                disallowed_tools=disallowed,
                tool_search_available=tool_search_allowed,
            )
            for record in applicable
        ]
        if applicable:
            self._store.touch([record.name for record in applicable])
        formatted, raw_tokens, final_tokens, summarized = _format_skills_for_injection(
            detailed,
            max_tokens=self._config.max_tokens,
        )
        return RetrievalResponse(
            skills=detailed,
            formatted_context=formatted,
            query=query.task,
            search_type=effective,
            top_k=query.top_k,
            raw_tokens_est=raw_tokens,
            final_tokens_est=final_tokens,
            was_summarized=summarized,
        )

    async def search(
        self,
        query: SkillSearchQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillSearchResponse:
        scope_clause, scope_params = _build_scope_filter(tool_context)
        results, effective = self._store.search(
            query.query,
            search_type=query.search_type,
            limit=query.limit,
            task_type=query.task_type,
            scope_clause=scope_clause,
            scope_params=scope_params,
        )
        effective = cast(SkillSearchType, effective)
        names = [item["name"] for item in results]
        applicable_names = {
            record.name
            for record in self._applicable_records(
                self._store.get_by_name(names, scope_clause=scope_clause, scope_params=scope_params),
                capability_context,
            )
        }
        disallowed, tool_search_allowed = _tool_redaction_sets(capability_context)
        payload = [
            SkillSearchResult(
                name=item["name"],
                title=_redact_text(
                    item.get("title"),
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                trigger=_redact_text(
                    item.get("trigger"),
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                task_type=item.get("task_type"),
                score=float(item["score"]),
            )
            for item in results
            if item["name"] in applicable_names
        ]
        return SkillSearchResponse(skills=payload, query=query.query, search_type=effective)

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> list[SkillResultDetailed]:
        scope_clause, scope_params = _build_scope_filter(tool_context)
        records = self._store.get_by_name(names, scope_clause=scope_clause, scope_params=scope_params)
        applicable = self._applicable_records(records, capability_context)
        disallowed, tool_search_allowed = _tool_redaction_sets(capability_context)
        detailed = [
            _redact_skill(
                record,
                redact_pii_enabled=self._config.redact_pii,
                disallowed_tools=disallowed,
                tool_search_available=tool_search_allowed,
            )
            for record in applicable
        ]
        if applicable:
            self._store.touch([record.name for record in applicable])
        return detailed

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillListResponse:
        scope_clause, scope_params = _build_scope_filter(tool_context)
        records, total = self._store.list(
            page=req.page,
            page_size=req.page_size,
            task_type=req.task_type,
            origin=req.origin,
            scope_clause=scope_clause,
            scope_params=scope_params,
        )
        if capability_context is not None:
            all_records, _ = self._store.list(
                page=1,
                page_size=max(total, req.page * req.page_size),
                task_type=req.task_type,
                origin=req.origin,
                scope_clause=scope_clause,
                scope_params=scope_params,
            )
            total = len(self._applicable_records(all_records, capability_context))
        applicable = self._applicable_records(records, capability_context)
        disallowed, tool_search_allowed = _tool_redaction_sets(capability_context)
        entries = [
            SkillListEntry(
                name=record.name,
                title=_redact_text(
                    record.title,
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                trigger=_redact_text(
                    record.trigger,
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                task_type=record.task_type,
            )
            for record in applicable
        ]
        return SkillListResponse(skills=entries, page=req.page, page_size=req.page_size, total=int(total))

    async def directory(
        self,
        config: SkillsDirectoryConfig,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> Sequence[SkillDirectoryEntry]:
        if not config.enabled:
            return []
        scope_clause, scope_params = _build_scope_filter(tool_context)
        pinned = [name for name in self._pinned_names if name]
        if pinned:
            pinned_records = self._applicable_records(
                self._store.get_by_name(
                    pinned,
                    scope_clause=scope_clause,
                    scope_params=scope_params,
                ),
                capability_context,
            )
        else:
            pinned_records = []
        remaining = max(config.max_entries - len(pinned_records), 0)
        exclude = {record.name for record in pinned_records}
        if remaining > 0:
            if config.selection_strategy == "pinned_then_top":
                extra = list(
                    self._store.list_top(
                        limit=remaining,
                        exclude_names=exclude,
                        scope_clause=scope_clause,
                        scope_params=scope_params,
                    )
                )
            else:
                extra = list(
                    self._store.list_recent(
                        limit=remaining,
                        exclude_names=exclude,
                        scope_clause=scope_clause,
                        scope_params=scope_params,
                    )
                )
            extra = self._applicable_records(extra, capability_context)
        else:
            extra = []
        records = pinned_records + extra
        disallowed, tool_search_allowed = _tool_redaction_sets(capability_context)
        return [
            SkillDirectoryEntry(
                name=record.name,
                title=_redact_text(
                    record.title,
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                trigger=_redact_text(
                    record.trigger,
                    redact_pii_enabled=self._config.redact_pii,
                    disallowed_tools=disallowed,
                    tool_search_available=tool_search_allowed,
                ),
                task_type=record.task_type,
            )
            for record in records
        ]

    async def format_for_injection(
        self,
        skills: Sequence[SkillResultDetailed],
        *,
        max_tokens: int,
    ) -> tuple[str, int, int, bool]:
        return _format_skills_for_injection(skills, max_tokens=max_tokens)


class CompositeSkillProvider:
    def __init__(self, providers: Sequence[SkillProvider], *, config: SkillsConfig) -> None:
        self._providers = [provider for provider in providers]
        self._config = config

    def _fanout_multiplier(self) -> int:
        return max(len(self._providers), 1)

    async def _collect_provider_entries(
        self,
        provider: SkillProvider,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> tuple[list[SkillListEntry], int]:
        page = 1
        collected: list[SkillListEntry] = []
        total = 0
        while True:
            response = await provider.list(
                req.model_copy(update={"page": page}),
                tool_context=tool_context,
                capability_context=capability_context,
            )
            total = max(total, response.total)
            if not response.skills:
                break
            collected.extend(response.skills)
            if len(collected) >= response.total or len(response.skills) < req.page_size:
                break
            page += 1
        return collected, total

    async def get_relevant(
        self,
        query: SkillQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> RetrievalResponse:
        expanded_query = query.model_copy(update={"top_k": query.top_k * self._fanout_multiplier()})
        responses = [
            await provider.get_relevant(
                expanded_query,
                tool_context=tool_context,
                capability_context=capability_context,
            )
            for provider in self._providers
        ]
        skills = _dedupe_by_name(
            [skill for response in responses for skill in response.skills],
            key=lambda item: item.name,
        )[: query.top_k]
        formatted, raw_tokens, final_tokens, summarized = _format_skills_for_injection(
            skills,
            max_tokens=self._config.max_tokens,
        )
        effective_search_type = (
            responses[0].search_type if responses else cast(SkillSearchType, query.search_type)
        )
        return RetrievalResponse(
            skills=skills,
            formatted_context=formatted,
            query=query.task,
            search_type=effective_search_type,
            top_k=query.top_k,
            raw_tokens_est=raw_tokens,
            final_tokens_est=final_tokens,
            was_summarized=summarized,
        )

    async def search(
        self,
        query: SkillSearchQuery,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillSearchResponse:
        expanded_query = query.model_copy(update={"limit": query.limit * self._fanout_multiplier()})
        responses = [
            await provider.search(
                expanded_query,
                tool_context=tool_context,
                capability_context=capability_context,
            )
            for provider in self._providers
        ]
        skills = _dedupe_by_name(
            [skill for response in responses for skill in response.skills],
            key=lambda item: item.name,
        )[: query.limit]
        effective_search_type = (
            responses[0].search_type if responses else cast(SkillSearchType, query.search_type)
        )
        return SkillSearchResponse(skills=skills, query=query.query, search_type=effective_search_type)

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> list[SkillResultDetailed]:
        results = [
            await provider.get_by_name(
                names,
                tool_context=tool_context,
                capability_context=capability_context,
            )
            for provider in self._providers
        ]
        detailed = _dedupe_by_name(
            [skill for provider_results in results for skill in provider_results],
            key=lambda item: item.name,
        )
        lookup = {item.name: item for item in detailed}
        return [lookup[name] for name in names if name in lookup]

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> SkillListResponse:
        responses = [
            await self._collect_provider_entries(
                provider,
                req,
                tool_context=tool_context,
                capability_context=capability_context,
            )
            for provider in self._providers
        ]
        skills = _dedupe_by_name(
            [skill for provider_entries, _total in responses for skill in provider_entries],
            key=lambda item: item.name,
        )
        offset = max((req.page - 1) * req.page_size, 0)
        paged = skills[offset : offset + req.page_size]
        return SkillListResponse(skills=paged, page=req.page, page_size=req.page_size, total=len(skills))

    async def directory(
        self,
        config: SkillsDirectoryConfig,
        *,
        tool_context: Mapping[str, object],
        capability_context: SkillCapabilityContext | None = None,
    ) -> Sequence[SkillDirectoryEntry]:
        expanded_config = config.model_copy(update={"max_entries": config.max_entries * self._fanout_multiplier()})
        entries = [
            await provider.directory(
                expanded_config,
                tool_context=tool_context,
                capability_context=capability_context,
            )
            for provider in self._providers
        ]
        return _dedupe_by_name(
            [entry for provider_entries in entries for entry in provider_entries],
            key=lambda item: item.name,
        )[: config.max_entries]

    async def format_for_injection(
        self,
        skills: Sequence[SkillResultDetailed],
        *,
        max_tokens: int,
    ) -> tuple[str, int, int, bool]:
        return _format_skills_for_injection(skills, max_tokens=max_tokens)


def _collect_pinned_names(packs: Iterable[SkillPackConfig]) -> set[str]:
    names: set[str] = set()
    for pack in packs:
        if not pack.enabled:
            continue
        names.update({name for name in pack.pinned_skill_names if name})
    return names


__all__ = [
    "CompositeSkillProvider",
    "LocalSkillProvider",
    "SkillProvider",
    "SkillProviderFactory",
    "build_skill_capability_context",
]
