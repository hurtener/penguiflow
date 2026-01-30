from __future__ import annotations

from pathlib import Path

import pytest

from penguiflow.skills import SkillsConfig
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition
from penguiflow.skills.provider import LocalSkillProvider
from penguiflow.skills.tools.skill_get_tool import SkillGetArgs, skill_get
from penguiflow.skills.tools.skill_list_tool import SkillListArgs, skill_list
from penguiflow.skills.tools.skill_search_tool import SkillSearchArgs, skill_search


class _DummyPlanner:
    def __init__(self, provider: LocalSkillProvider) -> None:
        self._skills_provider = provider
        self._execution_spec_by_name = {"tool_search": None}
        self._tool_visibility_allowed_names = {"tool_search"}
        self._events: list[object] = []

    def _emit_event(self, event: object) -> None:
        self._events.append(event)

    def _time_source(self) -> float:
        return 0.0


class _DummyCtx:
    def __init__(self, planner: _DummyPlanner) -> None:
        self._planner = planner
        self.tool_context = {"project_id": "proj"}


@pytest.mark.asyncio
async def test_skill_tools_emit_events(tmp_path: Path) -> None:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path), max_tokens=240, redact_pii=False)
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    provider = LocalSkillProvider(config, store=store)
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.core.demo",
            trigger="Demo",
            steps=["Step 1"],
        ),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )
    planner = _DummyPlanner(provider)
    ctx = _DummyCtx(planner)

    search_response = await skill_search(SkillSearchArgs(query="demo", search_type="regex", limit=5), ctx)
    assert search_response.skills

    list_response = await skill_list(SkillListArgs(page=1, page_size=5), ctx)
    assert list_response.total >= 1

    get_response = await skill_get(
        SkillGetArgs(names=["pack.core.demo"], format="injection", max_tokens=240),
        ctx,
    )
    assert "<skills_context>" in get_response.formatted_context
    assert len(planner._events) >= 3
