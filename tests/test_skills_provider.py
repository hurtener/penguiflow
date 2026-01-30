from __future__ import annotations

from pathlib import Path

import pytest

from penguiflow.skills import SkillsConfig
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition, SkillListRequest, SkillQuery, SkillSearchQuery
from penguiflow.skills.provider import LocalSkillProvider


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

    search = await provider.search(
        SkillSearchQuery(query="login_basic", search_type="regex", limit=5),
        tool_context={"project_id": "proj"},
        all_tool_names={"browser.open", "tool_search"},
        allowed_tool_names={"tool_search"},
    )
    assert search.skills
    assert 0.0 <= search.skills[0].score <= 1.0

    response = await provider.get_relevant(
        SkillQuery(task="login", top_k=2),
        tool_context={"project_id": "proj"},
        all_tool_names={"browser.open", "tool_search"},
        allowed_tool_names={"tool_search"},
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
        all_tool_names=set(),
        allowed_tool_names=set(),
    )
    assert response.total >= 1
    assert response.skills[0].name.startswith("pack.core")
