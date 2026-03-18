from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from penguiflow.planner import ReactPlanner
from penguiflow.skills import (
    CompositeSkillProvider,
    RetrievalResponse,
    SkillDirectoryEntry,
    SkillListEntry,
    SkillListRequest,
    SkillListResponse,
    SkillPackConfig,
    SkillProposalDraft,
    SkillProvider,
    SkillQuery,
    SkillResultDetailed,
    SkillsConfig,
    SkillSearchQuery,
    SkillSearchResponse,
    SkillSearchResult,
)
from penguiflow.skills.provider import build_skill_capability_context
from penguiflow.skills.tools.skill_propose_tool import SkillProposeArgs, skill_propose


class _StaticSkillProvider:
    def __init__(self, skills: list[SkillResultDetailed]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    async def get_relevant(
        self,
        query: SkillQuery,
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> RetrievalResponse:
        del tool_context, capability_context
        skills = list(self._skills.values())[: query.top_k]
        return RetrievalResponse(
            skills=skills,
            formatted_context="\n".join(skill.name for skill in skills),
            query=query.task,
            search_type=query.search_type,
            top_k=query.top_k,
            raw_tokens_est=0,
            final_tokens_est=0,
            was_summarized=False,
        )

    async def search(
        self,
        query: SkillSearchQuery,
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> SkillSearchResponse:
        del tool_context, capability_context
        needle = query.query.lower()
        matches = [
            SkillSearchResult(
                name=skill.name,
                title=skill.title,
                trigger=skill.trigger,
                task_type=skill.task_type,
                score=1.0,
            )
            for skill in self._skills.values()
            if needle in skill.name.lower() or needle in skill.trigger.lower()
        ][: query.limit]
        return SkillSearchResponse(skills=matches, query=query.query, search_type=query.search_type)

    async def get_by_name(
        self,
        names: list[str],
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> list[SkillResultDetailed]:
        del tool_context, capability_context
        return [self._skills[name] for name in names if name in self._skills]

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> SkillListResponse:
        del tool_context, capability_context
        ordered = list(self._skills.values())
        offset = (req.page - 1) * req.page_size
        page = ordered[offset : offset + req.page_size]
        return SkillListResponse(
            skills=[
                SkillListEntry(
                    name=skill.name,
                    title=skill.title,
                    trigger=skill.trigger,
                    task_type=skill.task_type,
                )
                for skill in page
            ],
            page=req.page,
            page_size=req.page_size,
            total=len(ordered),
        )

    async def directory(
        self,
        config: Any,
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> list[SkillDirectoryEntry]:
        del tool_context, capability_context
        return [
            SkillDirectoryEntry(
                name=skill.name,
                title=skill.title,
                trigger=skill.trigger,
                task_type=skill.task_type,
            )
            for skill in list(self._skills.values())[: config.max_entries]
        ]

    async def format_for_injection(
        self,
        skills: list[SkillResultDetailed],
        *,
        max_tokens: int,
    ) -> tuple[str, int, int, bool]:
        del max_tokens
        return "\n".join(skill.name for skill in skills), 0, 0, False


class _PagedSkillProvider(_StaticSkillProvider):
    def __init__(self, prefix: str, count: int) -> None:
        super().__init__(
            [
                _runtime_skill(f"{prefix}.skill_{index}", trigger=f"{prefix} trigger {index}")
                for index in range(count)
            ]
        )
        self._ordered_names = list(self._skills.keys())

    async def list(
        self,
        req: SkillListRequest,
        *,
        tool_context: dict[str, object],
        capability_context: object | None = None,
    ) -> SkillListResponse:
        del tool_context, capability_context
        offset = (req.page - 1) * req.page_size
        page_names = self._ordered_names[offset : offset + req.page_size]
        return SkillListResponse(
            skills=[
                SkillListEntry(
                    name=name,
                    title=self._skills[name].title,
                    trigger=self._skills[name].trigger,
                    task_type=self._skills[name].task_type,
                )
                for name in page_names
            ],
            page=req.page,
            page_size=req.page_size,
            total=len(self._ordered_names),
        )


class _StubClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []
        self.response_formats: list[dict[str, object] | None] = []

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del stream, on_stream_chunk
        self.calls.append(list(messages))
        self.response_formats.append(response_format)
        if not self._responses:
            raise AssertionError("No stub response configured")
        return self._responses.pop(0), 0.25


class _DummyCtx:
    def __init__(self, planner: ReactPlanner) -> None:
        self._planner = planner
        self.tool_context = {"project_id": "proj"}


def _runtime_skill(name: str, *, trigger: str) -> SkillResultDetailed:
    return SkillResultDetailed(
        name=name,
        title=name.split(".")[-1].replace("_", " ").title(),
        trigger=trigger,
        steps=["Step 1", "Step 2"],
        task_type="domain",
    )


def test_react_planner_custom_skills_provider_enables_skill_tools() -> None:
    provider = _StaticSkillProvider([_runtime_skill("runtime.persona.mail_triage", trigger="Triage mail")])
    planner = ReactPlanner(
        llm_client=_StubClient([]),
        catalog=[],
        skills_provider=provider,
    )

    assert planner._skills_provider is provider
    assert planner._skills_config.enabled is True
    assert {"skill_search", "skill_get", "skill_list"}.issubset(planner._spec_by_name)
    assert "skill_propose" not in planner._spec_by_name


def test_react_planner_rejects_invalid_skill_provider_configs() -> None:
    provider = _StaticSkillProvider([])

    with pytest.raises(ValueError, match="mutually exclusive"):
        ReactPlanner(
            llm_client=_StubClient([]),
            catalog=[],
            skills_provider=provider,
            skills_provider_factory=lambda config: provider,
        )

    with pytest.raises(ValueError, match="skills.enabled=True"):
        ReactPlanner(
            llm_client=_StubClient([]),
            catalog=[],
            skills=SkillsConfig(enabled=False),
            skills_provider=provider,
        )


def test_react_planner_skills_provider_factory_survives_fork() -> None:
    created: list[_StaticSkillProvider] = []

    def factory(config: SkillsConfig) -> SkillProvider:
        assert config.enabled is True
        provider = _StaticSkillProvider(
            [_runtime_skill(f"runtime.persona.skill_{len(created)}", trigger="Runtime skill")]
        )
        created.append(provider)
        return provider

    planner = ReactPlanner(
        llm_client=_StubClient([]),
        catalog=[],
        skills_provider_factory=factory,
    )
    child = planner.fork()

    assert len(created) == 2
    assert planner._skills_provider is created[0]
    assert child._skills_provider is created[1]
    assert child._skills_provider is not planner._skills_provider


@pytest.mark.asyncio
async def test_react_planner_composes_local_and_runtime_skills_with_runtime_precedence(tmp_path: Path) -> None:
    pack_root = tmp_path / "skills"
    pack_root.mkdir()
    (pack_root / "mail_triage.skill.yaml").write_text(
        "\n".join(
            [
                "name: pack.demo.mail_triage",
                "title: Pack mail triage",
                "trigger: Pack trigger",
                "task_type: domain",
                "steps:",
                "  - Pack step",
            ]
        ),
        encoding="utf-8",
    )
    runtime_provider = _StaticSkillProvider(
        [
            _runtime_skill("pack.demo.mail_triage", trigger="Runtime override"),
            _runtime_skill("runtime.persona.mail_escalation", trigger="Escalate VIP mail"),
        ]
    )
    planner = ReactPlanner(
        llm_client=_StubClient([]),
        catalog=[],
        skills=SkillsConfig(
            enabled=True,
            cache_dir=str(tmp_path / ".cache"),
            skill_packs=[SkillPackConfig(name="demo", path=str(pack_root))],
        ),
        skills_provider=runtime_provider,
    )

    assert isinstance(planner._skills_provider, CompositeSkillProvider)
    details = await planner._skills_provider.get_by_name(
        ["pack.demo.mail_triage", "runtime.persona.mail_escalation"],
        tool_context={"project_id": "proj"},
        capability_context=build_skill_capability_context(allowed_tool_names=set()),
    )

    assert [item.name for item in details] == ["pack.demo.mail_triage", "runtime.persona.mail_escalation"]
    assert details[0].trigger == "Runtime override"


@pytest.mark.asyncio
async def test_composite_skill_provider_lists_across_provider_pages() -> None:
    provider = CompositeSkillProvider(
        [_PagedSkillProvider("runtime.alpha", 3), _PagedSkillProvider("runtime.beta", 3)],
        config=SkillsConfig(enabled=True),
    )

    response = await provider.list(
        SkillListRequest(page=2, page_size=2),
        tool_context={"project_id": "proj"},
        capability_context=build_skill_capability_context(allowed_tool_names=set()),
    )

    assert response.total == 6
    assert [entry.name for entry in response.skills] == ["runtime.alpha.skill_2", "runtime.beta.skill_0"]


def test_skill_propose_is_disabled_by_default() -> None:
    planner = ReactPlanner(
        llm_client=_StubClient([]),
        catalog=[],
        skills=SkillsConfig(enabled=True),
    )

    assert "skill_propose" not in planner._spec_by_name
    assert "Use skill_propose" not in planner._system_prompt


@pytest.mark.asyncio
async def test_skill_propose_returns_structured_draft_and_records_event() -> None:
    client = _StubClient(
        [
            json.dumps(
                SkillProposalDraft(
                    skill={
                        "name": "persona.mail_triage",
                        "title": "Mail triage",
                        "trigger": "When new support email arrives",
                        "task_type": "domain",
                        "steps": ["Open inbox", "Prioritize urgent threads"],
                        "required_tool_names": ["mail.search"],
                        "required_namespaces": ["mail"],
                        "required_tags": ["email"],
                    },
                    warnings=["Needs human review"],
                    assumptions=["Mail tool is available"],
                ).model_dump(),
                ensure_ascii=False,
            )
        ]
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=[],
        skills=SkillsConfig(enabled=True, proposal={"enabled": True}),
    )

    response = await skill_propose(
        SkillProposeArgs(
            source_material="When new support email arrives, open the inbox and prioritize urgent threads.",
            task_type="domain",
            required_tool_names=["mail.search"],
            required_namespaces=["mail"],
            required_tags=["email"],
        ),
        _DummyCtx(planner),
    )

    assert "skill_propose" in planner._spec_by_name
    assert "It does NOT save or persist skills." in planner._system_prompt
    assert response.draft.skill.required_tool_names == ["mail.search"]
    assert response.draft.warnings == ["Needs human review"]
    assert client.response_formats[0] is not None
    assert client.response_formats[0]["json_schema"]["name"] == "skill_proposal"
    assert any(event.event_type == "skill_propose" for event in planner._event_buffer)


@pytest.mark.asyncio
async def test_skill_propose_surfaces_invalid_draft_payload() -> None:
    planner = ReactPlanner(
        llm_client=_StubClient([json.dumps({"skill": {"name": "broken"}})]),
        catalog=[],
        skills=SkillsConfig(enabled=True, proposal={"enabled": True}),
    )

    with pytest.raises(ValidationError):
        await skill_propose(
            SkillProposeArgs(source_material="Broken draft"),
            _DummyCtx(planner),
        )
