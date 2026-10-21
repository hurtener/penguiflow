"""Tests for ToolSearchCache tag and namespace filters (Phase 1).

These exercises cover the back-compatible filter args added to
``ToolSearchCache.search`` and ``ToolSearchArgs``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner.tool_search_cache import ToolSearchCache
from penguiflow.planner.tool_search_tool import ToolSearchArgs
from penguiflow.registry import ModelRegistry


class _Args(BaseModel):
    text: str


class _Out(BaseModel):
    text: str


@tool(desc="Send a mail message via Microsoft 365.", tags=["mail", "write"])
async def microsoft_send_mail(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(text=args.text)


@tool(desc="Search Microsoft 365 mail.", tags=["mail", "read"])
async def microsoft_search_mail(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(text=args.text)


@tool(desc="List BigQuery datasets.", tags=["bigquery", "read"])
async def explorer_list_datasets(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(text=args.text)


@tool(desc="Terminate the task.", tags=[], loading_mode=ToolLoadingMode.ALWAYS)
async def finish_tool(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(text=args.text)


def _build_cache(tmp_path: Path) -> ToolSearchCache:
    registry = ModelRegistry()
    registry.register("microsoft_365.send_mail", _Args, _Out)
    registry.register("microsoft_365.search_mail", _Args, _Out)
    registry.register("explorer.list_datasets", _Args, _Out)
    registry.register("finish", _Args, _Out)
    nodes = [
        Node(microsoft_send_mail, name="microsoft_365.send_mail"),
        Node(microsoft_search_mail, name="microsoft_365.search_mail"),
        Node(explorer_list_datasets, name="explorer.list_datasets"),
        Node(finish_tool, name="finish"),
    ]
    specs = build_catalog(nodes, registry)
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)
    return cache


def _search(
    cache: ToolSearchCache,
    query: str,
    *,
    tags: tuple[str, ...] = (),
    namespace: str | None = None,
    limit: int = 10,
    search_type: str = "fts",
) -> list[dict[str, Any]]:
    results, _ = cache.search(
        query,
        search_type=search_type,
        limit=limit,
        include_always_loaded=True,
        allowed_names=None,
        tags=tags,
        namespace=namespace,
    )
    return results


def test_tags_filter_single(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    results = _search(cache, "mail", tags=("mail",))
    assert {item["name"] for item in results} == {
        "microsoft_365.send_mail",
        "microsoft_365.search_mail",
    }


def test_tags_filter_and_match(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    results = _search(cache, "mail", tags=("mail", "write"))
    assert [item["name"] for item in results] == ["microsoft_365.send_mail"]


def test_tags_filter_unknown_tag_returns_empty(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    results = _search(cache, "mail", tags=("mail", "doesnotexist"))
    assert results == []


def test_tags_filter_ignores_auto_expanded_name_tokens(tmp_path: Path) -> None:
    """The declared-tags filter must not match name-token expansions."""
    cache = _build_cache(tmp_path)
    # "send" is a token in microsoft_365.send_mail and gets added to the
    # auto-expanded ``tags`` column for FTS recall. The explicit ``tags``
    # filter must consult only declared tags, so passing tags=("send",)
    # should return nothing — none of the tools declare ``send`` as a tag.
    results = _search(cache, "mail", tags=("send",))
    assert results == []


def test_namespace_filter_dot_prefix(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    results = _search(cache, "mail", namespace="microsoft_365")
    assert {item["name"] for item in results} == {
        "microsoft_365.send_mail",
        "microsoft_365.search_mail",
    }


def test_namespace_filter_does_not_match_underscore_prefix(tmp_path: Path) -> None:
    """Namespace is dot-prefix only; underscore is NOT a separator."""
    cache = _build_cache(tmp_path)
    results = _search(cache, "datasets", namespace="explorer")
    assert {item["name"] for item in results} == {"explorer.list_datasets"}

    # 'explorer_list' should not match 'explorer.list_datasets'.
    results = _search(cache, "datasets", namespace="explorer_list")
    assert results == []


def test_namespace_filter_exact_bare_name(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    # Bare-name tool: namespace="finish" matches via the name==namespace branch.
    results = _search(cache, "finish", namespace="finish", search_type="exact")
    assert [item["name"] for item in results] == ["finish"]


def test_filters_default_is_back_compat(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    baseline, _ = cache.search(
        "mail",
        search_type="fts",
        limit=10,
        include_always_loaded=True,
        allowed_names=None,
    )
    with_defaults, _ = cache.search(
        "mail",
        search_type="fts",
        limit=10,
        include_always_loaded=True,
        allowed_names=None,
        tags=(),
        namespace=None,
    )
    assert baseline == with_defaults


def test_filters_compose_as_and(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    results = _search(
        cache,
        "mail",
        tags=("mail",),
        namespace="microsoft_365",
    )
    assert {item["name"] for item in results} == {
        "microsoft_365.send_mail",
        "microsoft_365.search_mail",
    }

    # Compose AND with a tag the namespace doesn't contain → empty.
    results = _search(
        cache,
        "mail",
        tags=("bigquery",),
        namespace="microsoft_365",
    )
    assert results == []


def test_filters_apply_before_paging(tmp_path: Path) -> None:
    """Limit must yield up to N *post-filter* results, not N raw matches."""
    cache = _build_cache(tmp_path)
    # 4 tools total; restricting to mail tag leaves 2 of them.
    results = _search(cache, "mail", tags=("mail",), limit=5)
    assert len(results) == 2

    # With limit=1 we still get exactly 1 matching tool (not pruned to 0).
    results = _search(cache, "mail", tags=("mail",), limit=1)
    assert len(results) == 1
    assert results[0]["name"] in {"microsoft_365.send_mail", "microsoft_365.search_mail"}


def test_tool_search_args_accepts_new_fields() -> None:
    args = ToolSearchArgs(query="mail", tags=["mail", "write"], namespace="microsoft_365")
    assert args.tags == ["mail", "write"]
    assert args.namespace == "microsoft_365"
    # Defaults remain back-compat.
    args = ToolSearchArgs(query="mail")
    assert args.tags == []
    assert args.namespace is None


def test_schema_migrates_from_v1_to_v2(tmp_path: Path) -> None:
    """A cache file written under schema v1 must be readable after upgrade.

    Sets up a faithful v1 cache (tools table + FTS index + triggers, no
    declared_tags column, schema_version=1) with a row that survives into v2.
    The migration path must add the column, re-populate declared_tags for the
    surviving row, and keep the new tag filter working.
    """

    import sqlite3

    db_path = tmp_path / "tool_cache.db"
    # Simulate a legacy v1 cache: tools table without ``declared_tags`` but
    # otherwise faithful — including the FTS index and triggers that v1 wrote.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tools (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                tags TEXT NOT NULL,
                side_effects TEXT NOT NULL,
                loading_mode TEXT NOT NULL,
                record_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tool_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE tools_fts USING fts5(
                name,
                description,
                tags,
                content='tools',
                content_rowid='rowid',
                tokenize='porter unicode61'
            )
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER tools_ai AFTER INSERT ON tools BEGIN
                INSERT INTO tools_fts(rowid, name, description, tags)
                VALUES (new.rowid, new.name, new.description, new.tags);
            END;
            CREATE TRIGGER tools_ad AFTER DELETE ON tools BEGIN
                INSERT INTO tools_fts(tools_fts, rowid, name, description, tags)
                VALUES('delete', old.rowid, old.name, old.description, old.tags);
            END;
            CREATE TRIGGER tools_au AFTER UPDATE ON tools BEGIN
                INSERT INTO tools_fts(tools_fts, rowid, name, description, tags)
                VALUES('delete', old.rowid, old.name, old.description, old.tags);
                INSERT INTO tools_fts(rowid, name, description, tags)
                VALUES (new.rowid, new.name, new.description, new.tags);
            END;
            """
        )
        # Surviving row (same name as the new sync below).
        conn.execute(
            "INSERT INTO tools (name, description, tags, side_effects, loading_mode, record_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "microsoft_365.send_mail",
                "Legacy desc",
                '["legacy_tag"]',
                "pure",
                "always",
                "legacy_hash",
            ),
        )
        conn.execute(
            "INSERT INTO tool_index_meta (key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )

    cache = ToolSearchCache(cache_dir=str(tmp_path))
    # First sync after schema bump should detect outdated version and force
    # re-upsert so declared_tags is populated for the surviving row.
    registry = ModelRegistry()
    registry.register("microsoft_365.send_mail", _Args, _Out)
    specs = build_catalog(
        [Node(microsoft_send_mail, name="microsoft_365.send_mail")],
        registry,
    )
    cache.sync_tools(specs)

    results, _ = cache.search(
        "mail",
        search_type="fts",
        limit=5,
        include_always_loaded=True,
        allowed_names=None,
        tags=("mail",),
    )
    assert {item["name"] for item in results} == {"microsoft_365.send_mail"}


@pytest.mark.parametrize("search_type", ["fts", "regex", "exact"])
def test_filters_apply_across_search_types(search_type: str, tmp_path: Path) -> None:
    cache = _build_cache(tmp_path)
    query = "microsoft_365.send_mail" if search_type == "exact" else "mail"
    results = _search(
        cache,
        query,
        tags=("mail", "write"),
        namespace="microsoft_365",
        search_type=search_type,
    )
    assert [item["name"] for item in results] == ["microsoft_365.send_mail"]


def test_fts_results_are_ordered_by_relevance(tmp_path: Path) -> None:
    """Issue 128 bug 4 (tool search twin): the best bm25 match ranks first with score 1.0."""
    cache = _build_cache(tmp_path)
    results = _search(cache, "list bigquery datasets or mail")

    assert results[0]["name"] == "explorer.list_datasets"
    assert results[0]["score"] == 1.0
    assert all(item["score"] < 1.0 for item in results[1:])
