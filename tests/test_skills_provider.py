from __future__ import annotations

from pathlib import Path

import pytest

from penguiflow.skills import SkillsConfig
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition, SkillListRequest, SkillQuery, SkillSearchQuery
from penguiflow.skills.provider import LocalSkillProvider, build_skill_capability_context


@pytest.mark.asyncio
async def test_provider_search_and_get_relevant(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), max_tokens=240, redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    skill = SkillDefinition(
        name="pack.core.browser_auth.login_basic",
        trigger="Log into a site",
        steps=["Use browser.open to navigate", "Enter credentials"],
    )
    store.upsert_pack_skill(skill, pack_name="core", scope_mode="project", update_existing=True)

    capability_context = build_skill_capability_context(
        all_tool_names={"browser.open", "tool_search"},
        allowed_tool_names={"tool_search"},
    )
    search = await provider.search(
        SkillSearchQuery(query="login_basic", search_type="regex", limit=5),
        tool_context={"project_id": "proj"},
        capability_context=capability_context,
    )
    assert search.skills
    assert 0.0 <= search.skills[0].score <= 1.0

    response = await provider.get_relevant(
        SkillQuery(task="login", top_k=2),
        tool_context={"project_id": "proj"},
        capability_context=capability_context,
    )
    assert "<skills_context>" in response.formatted_context
    assert "browser.open" not in response.formatted_context
    assert "tool_search" in response.formatted_context
    assert response.final_tokens_est <= config.max_tokens


@pytest.mark.asyncio
async def test_provider_list_returns_entries(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    skill = SkillDefinition(
        name="pack.core.api.pagination.cursor_loop",
        trigger="Handle cursor pagination",
        steps=["Call endpoint", "Follow cursor"],
    )
    store.upsert_pack_skill(skill, pack_name="core", scope_mode="project", update_existing=True)
    response = await provider.list(
        SkillListRequest(page=1, page_size=10),
        tool_context={"project_id": "proj"},
        capability_context=build_skill_capability_context(all_tool_names=set(), allowed_tool_names=set()),
    )
    assert response.total >= 1
    assert response.skills[0].name.startswith("pack.core")


@pytest.mark.asyncio
async def test_provider_filters_by_skill_applicability(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.core.mail_triage",
            trigger="Triage email inbox",
            steps=["Open inbox", "Summarize urgent threads"],
            required_tool_names=["mail.search"],
            required_namespaces=["mail"],
            required_tags=["email"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )

    blocked = build_skill_capability_context(
        execution_specs={
            "mail.search": type("Spec", (), {"tags": ("email",)})(),
            "tool_search": type("Spec", (), {"tags": ()})(),
        },
        allowed_tool_names={"tool_search"},
    )
    visible = build_skill_capability_context(
        execution_specs={
            "mail.search": type("Spec", (), {"tags": ("email",)})(),
            "tool_search": type("Spec", (), {"tags": ()})(),
        },
        allowed_tool_names={"mail.search", "tool_search"},
    )

    blocked_search = await provider.search(
        SkillSearchQuery(query="email", search_type="regex", limit=5),
        tool_context={"project_id": "proj"},
        capability_context=blocked,
    )
    visible_search = await provider.search(
        SkillSearchQuery(query="email", search_type="regex", limit=5),
        tool_context={"project_id": "proj"},
        capability_context=visible,
    )
    assert blocked_search.skills == []
    assert [item.name for item in visible_search.skills] == ["pack.core.mail_triage"]


@pytest.mark.asyncio
async def test_provider_hides_inapplicable_skills_from_get_relevant_get_by_name_and_directory(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.core.mail_triage",
            title="Mail triage",
            trigger="Triage the email inbox",
            steps=["Open the mailbox", "Summarize urgent threads"],
            required_tool_names=["mail.search"],
            required_namespaces=["mail"],
            required_tags=["email"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )

    execution_specs = {
        "mail.search": type("Spec", (), {"tags": ("email",)})(),
        "tool_search": type("Spec", (), {"tags": ()})(),
    }
    blocked = build_skill_capability_context(
        execution_specs=execution_specs,
        allowed_tool_names=set(),
    )
    allowed = build_skill_capability_context(
        execution_specs=execution_specs,
        allowed_tool_names={"mail.search"},
    )

    blocked_relevant = await provider.get_relevant(
        SkillQuery(task="triage email", top_k=2),
        tool_context={"project_id": "proj"},
        capability_context=blocked,
    )
    allowed_relevant = await provider.get_relevant(
        SkillQuery(task="triage email", top_k=2),
        tool_context={"project_id": "proj"},
        capability_context=allowed,
    )
    blocked_get = await provider.get_by_name(
        ["pack.core.mail_triage"],
        tool_context={"project_id": "proj"},
        capability_context=blocked,
    )
    blocked_directory = await provider.directory(
        config.directory,
        tool_context={"project_id": "proj"},
        capability_context=blocked,
    )
    allowed_directory = await provider.directory(
        config.directory,
        tool_context={"project_id": "proj"},
        capability_context=allowed,
    )

    assert blocked_relevant.skills == []
    assert blocked_relevant.formatted_context == ""
    assert [skill.name for skill in allowed_relevant.skills] == ["pack.core.mail_triage"]
    assert blocked_get == []
    assert blocked_directory == []
    assert [entry.name for entry in allowed_directory] == ["pack.core.mail_triage"]


@pytest.mark.asyncio
async def test_provider_list_total_reflects_applicability_even_when_page_is_full(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.core.mail_triage",
            trigger="Triage the email inbox",
            steps=["Open the mailbox", "Summarize urgent threads"],
            required_tool_names=["mail.search"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )

    response = await provider.list(
        SkillListRequest(page=1, page_size=10),
        tool_context={"project_id": "proj"},
        capability_context=build_skill_capability_context(
            execution_specs={"mail.search": type("Spec", (), {"tags": ()})()},
            allowed_tool_names=set(),
        ),
    )

    assert response.skills == []
    assert response.total == 0


def test_build_skill_capability_context_preserves_explicit_empty_allowlist() -> None:
    context = build_skill_capability_context(
        execution_specs={
            "mail.search": type("Spec", (), {"tags": ("email",)})(),
            "browser.open": type("Spec", (), {"tags": ("browser",)})(),
        },
        allowed_tool_names=set(),
    )
    assert context.all_tool_names == {"mail.search", "browser.open"}
    assert context.allowed_tool_names == set()
    assert context.allowed_namespaces == set()
    assert context.allowed_tool_tags == set()


def test_build_skill_capability_context_maps_bare_tools_to_core_namespace() -> None:
    context = build_skill_capability_context(
        execution_specs={
            "tool_search": type("Spec", (), {"tags": ()})(),
            "skill_get": type("Spec", (), {"tags": ()})(),
        },
        allowed_tool_names={"tool_search", "skill_get"},
    )

    assert context.allowed_namespaces == {"core"}


@pytest.mark.asyncio
async def test_provider_get_relevant_fetches_past_inapplicable_top_k(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    store.upsert_pack_skill(
        SkillDefinition(
            name="alpha.inapplicable.one",
            trigger="Triage email alpha",
            steps=["Open inbox"],
            required_tool_names=["mail.search"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )
    store.upsert_pack_skill(
        SkillDefinition(
            name="alpha.inapplicable.two",
            trigger="Triage email alpha",
            steps=["Open inbox again"],
            required_tool_names=["mail.search"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )
    store.upsert_pack_skill(
        SkillDefinition(
            name="alpha.applicable.three",
            trigger="Triage email alpha",
            steps=["Use the built-in mailbox summary flow"],
            required_tool_names=["tool_search"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )

    capability_context = build_skill_capability_context(
        execution_specs={
            "mail.search": type("Spec", (), {"tags": ("email",)})(),
            "tool_search": type("Spec", (), {"tags": ()})(),
        },
        allowed_tool_names={"tool_search"},
    )

    response = await provider.get_relevant(
        SkillQuery(task="triage email alpha", top_k=1, search_type="regex"),
        tool_context={"project_id": "proj"},
        capability_context=capability_context,
    )

    assert [skill.name for skill in response.skills] == ["alpha.applicable.three"]


@pytest.mark.asyncio
async def test_provider_list_paginates_after_applicability_filtering(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    for name in ("alpha.inapplicable.one", "alpha.inapplicable.two", "beta.applicable.one", "beta.applicable.two"):
        required_tool_names = ["tool_search"] if name.startswith("beta") else ["mail.search"]
        store.upsert_pack_skill(
            SkillDefinition(
                name=name,
                trigger=f"Handle {name}",
                steps=["Run the flow"],
                required_tool_names=required_tool_names,
            ),
            pack_name="core",
            scope_mode="project",
            update_existing=True,
        )

    capability_context = build_skill_capability_context(
        execution_specs={
            "mail.search": type("Spec", (), {"tags": ("email",)})(),
            "tool_search": type("Spec", (), {"tags": ()})(),
        },
        allowed_tool_names={"tool_search"},
    )

    page_one = await provider.list(
        SkillListRequest(page=1, page_size=2),
        tool_context={"project_id": "proj"},
        capability_context=capability_context,
    )
    page_two = await provider.list(
        SkillListRequest(page=2, page_size=1),
        tool_context={"project_id": "proj"},
        capability_context=capability_context,
    )

    assert [entry.name for entry in page_one.skills] == ["beta.applicable.one", "beta.applicable.two"]
    assert page_one.total == 2
    assert [entry.name for entry in page_two.skills] == ["beta.applicable.two"]
