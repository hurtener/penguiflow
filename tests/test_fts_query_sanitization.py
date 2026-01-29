from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner.tool_search_cache import ToolSearchCache
from penguiflow.registry import ModelRegistry
from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition


class _Args(BaseModel):
    text: str


class _Out(BaseModel):
    text: str


@tool(desc="Dummy tool")
async def dummy_tool(args: _Args, ctx):
    del ctx
    return _Out(text=args.text)


def test_tool_search_fts_sanitizes_punctuation(tmp_path: Path) -> None:
    registry = ModelRegistry()
    registry.register("dummy_tool", _Args, _Out)
    specs = build_catalog([Node(dummy_tool, name="dummy_tool")], registry)
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    # Should not raise sqlite fts syntax errors.
    results, effective = cache.search(
        "Hey! I want an SOV comparison please!",
        search_type="fts",
        limit=8,
        include_always_loaded=True,
        allowed_names=None,
    )
    assert effective in {"fts", "regex", "exact"}
    assert isinstance(results, list)


def test_tool_search_fts_matches_name_tokens(tmp_path: Path) -> None:
    class A(BaseModel):
        x: str

    class Out(BaseModel):
        y: str

    @tool(desc="Charting tool", tags=["mcp", "charting"])
    async def charting_tool(args: A, ctx):
        del ctx
        return Out(y=args.x)

    registry = ModelRegistry()
    registry.register("charting.start_chart_analysis", A, Out)
    specs = build_catalog([Node(charting_tool, name="charting.start_chart_analysis")], registry)
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    results, effective = cache.search(
        "start_analysis charting",
        search_type="fts",
        limit=10,
        include_always_loaded=True,
        allowed_names=None,
    )
    assert effective == "fts"
    assert any(item["name"] == "charting.start_chart_analysis" for item in results)


def test_tool_search_fts_no_matches_does_not_force_fallback(tmp_path: Path) -> None:
    registry = ModelRegistry()
    registry.register("dummy_tool", _Args, _Out)
    specs = build_catalog([Node(dummy_tool, name="dummy_tool")], registry)
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    results, effective = cache.search(
        "completely_unrelated_query",
        search_type="fts",
        limit=8,
        include_always_loaded=True,
        allowed_names=None,
    )
    assert effective == "fts"
    assert results == []


def test_skill_search_fts_sanitizes_punctuation(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.ads.sov_comparison",
            trigger="SOV comparison",
            steps=["Step 1"],
        ),
        pack_name="ads",
        scope_mode="project",
        update_existing=True,
    )

    results, effective = store.search(
        "Hey! I want an SOV comparison please!",
        search_type="fts",
        limit=8,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    assert effective in {"fts", "regex", "exact"}
    assert isinstance(results, list)


def test_skill_search_fuzzy_text_matches_with_extra_terms(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.ads.sov_comparison",
            trigger="SOV comparison",
            steps=["Step 1"],
        ),
        pack_name="ads",
        scope_mode="project",
        update_existing=True,
    )

    results, effective = store.search(
        "sov_comparison overlap activity share audience",
        search_type="fts",
        limit=8,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    assert effective in {"fts", "regex", "exact"}
    assert any(item["name"] == "pack.ads.sov_comparison" for item in results)


def test_skill_search_fts_no_matches_does_not_force_fallback(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(
            name="pack.ads.sov_comparison",
            trigger="SOV comparison",
            steps=["Step 1"],
        ),
        pack_name="ads",
        scope_mode="project",
        update_existing=True,
    )
    results, effective = store.search(
        "unrelated_query",
        search_type="fts",
        limit=8,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    assert effective == "fts"
    assert results == []
