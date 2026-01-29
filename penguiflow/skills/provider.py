"""Skill provider interfaces and local provider implementation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from .local_store import LocalSkillStore
from .models import (
    RetrievalResponse,
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
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> RetrievalResponse: ...

    async def search(
        self,
        query: SkillSearchQuery,
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> SkillSearchResponse: ...

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[SkillResultDetailed]: ...

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> SkillListResponse: ...


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


def _tool_redaction_sets(
    *,
    all_tool_names: Iterable[str] | None,
    allowed_tool_names: Iterable[str] | None,
) -> tuple[set[str], bool]:
    if not all_tool_names:
        return set(), False
    allowed = set(allowed_tool_names or [])
    disallowed = {name for name in all_tool_names if name not in allowed}
    tool_search_allowed = "tool_search" in allowed
    return disallowed, tool_search_allowed


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

    async def get_relevant(
        self,
        query: SkillQuery,
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
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
        disallowed, tool_search_allowed = _tool_redaction_sets(
            all_tool_names=all_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
        detailed = [
            _redact_skill(
                record,
                redact_pii_enabled=self._config.redact_pii,
                disallowed_tools=disallowed,
                tool_search_available=tool_search_allowed,
            )
            for record in records
        ]
        if names:
            self._store.touch(names)
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
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
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
        disallowed, tool_search_allowed = _tool_redaction_sets(
            all_tool_names=all_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
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
        ]
        return SkillSearchResponse(skills=payload, query=query.query, search_type=effective)

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[SkillResultDetailed]:
        scope_clause, scope_params = _build_scope_filter(tool_context)
        records = self._store.get_by_name(names, scope_clause=scope_clause, scope_params=scope_params)
        disallowed, tool_search_allowed = _tool_redaction_sets(
            all_tool_names=all_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
        detailed = [
            _redact_skill(
                record,
                redact_pii_enabled=self._config.redact_pii,
                disallowed_tools=disallowed,
                tool_search_available=tool_search_allowed,
            )
            for record in records
        ]
        if records:
            self._store.touch([record.name for record in records])
        return detailed

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
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
        disallowed, tool_search_allowed = _tool_redaction_sets(
            all_tool_names=all_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
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
            for record in records
        ]
        return SkillListResponse(skills=entries, page=req.page, page_size=req.page_size, total=total)

    async def directory(
        self,
        config: SkillsDirectoryConfig,
        *,
        tool_context: Mapping[str, object],
        all_tool_names: Iterable[str] | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> Sequence[SkillDirectoryEntry]:
        if not config.enabled:
            return []
        scope_clause, scope_params = _build_scope_filter(tool_context)
        pinned = [name for name in self._pinned_names if name]
        if pinned:
            pinned_records = self._store.get_by_name(
                pinned,
                scope_clause=scope_clause,
                scope_params=scope_params,
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
        else:
            extra = []
        records = pinned_records + extra
        disallowed, tool_search_allowed = _tool_redaction_sets(
            all_tool_names=all_tool_names,
            allowed_tool_names=allowed_tool_names,
        )
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


def _collect_pinned_names(packs: Iterable[SkillPackConfig]) -> set[str]:
    names: set[str] = set()
    for pack in packs:
        if not pack.enabled:
            continue
        names.update({name for name in pack.pinned_skill_names if name})
    return names


__all__ = ["LocalSkillProvider", "SkillProvider"]
