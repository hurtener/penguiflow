"""Tests for skill tag and namespace filters (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import (
    SkillDefinition,
    SkillListRequest,
    SkillQuery,
    SkillsConfig,
    SkillSearchQuery,
)
from penguiflow.skills.provider import LocalSkillProvider


def _store(tmp_path: Path) -> LocalSkillStore:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    skills = [
        SkillDefinition(
            name="canvas.microsoft365.mail.search_mail",
            trigger="Search mail",
            tags=["microsoft365", "mail", "read"],
            steps=["Step 1"],
        ),
        SkillDefinition(
            name="canvas.microsoft365.mail.send_mail",
            trigger="Send mail",
            tags=["microsoft365", "mail", "write"],
            steps=["Step 1"],
        ),
        SkillDefinition(
            name="canvas.bigquery.list_datasets",
            trigger="List BigQuery datasets",
            tags=["bigquery", "read"],
            steps=["Step 1"],
        ),
        SkillDefinition(
            name="finish",
            trigger="Finish task",
            tags=[],
            steps=["Step 1"],
            task_type="domain",
        ),
    ]
    for skill in skills:
        store.upsert_pack_skill(
            skill,
            pack_name="testpack",
            scope_mode="project",
            update_existing=True,
        )
    return store


def _search(
    store: LocalSkillStore,
    query: str,
    *,
    tags: tuple[str, ...] = (),
    namespace: str | None = None,
    limit: int = 10,
    search_type: str = "fts",
    task_type: str | None = None,
) -> list[dict]:
    results, _ = store.search(
        query,
        search_type=search_type,  # type: ignore[arg-type]
        limit=limit,
        task_type=task_type,  # type: ignore[arg-type]
        scope_clause="",
        scope_params=(),
        tags=tags,
        namespace=namespace,
    )
    return results


def test_tags_filter_single(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(store, "mail", tags=("microsoft365",))
    assert {item["name"] for item in results} == {
        "canvas.microsoft365.mail.search_mail",
        "canvas.microsoft365.mail.send_mail",
    }


def test_tags_filter_and_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(store, "mail", tags=("microsoft365", "write"))
    assert [item["name"] for item in results] == ["canvas.microsoft365.mail.send_mail"]


def test_tags_filter_unknown_tag_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(store, "mail", tags=("microsoft365", "nonexistent"))
    assert results == []


def test_namespace_filter_dot_prefix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(store, "mail", namespace="canvas.microsoft365")
    assert {item["name"] for item in results} == {
        "canvas.microsoft365.mail.search_mail",
        "canvas.microsoft365.mail.send_mail",
    }


def test_namespace_filter_exact_bare_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(store, "finish", namespace="finish", search_type="exact")
    assert [item["name"] for item in results] == ["finish"]


def test_namespace_filter_no_underscore_separator(tmp_path: Path) -> None:
    """Namespace match is dot-prefix only — underscores are not separators."""
    store = _store(tmp_path)
    results = _search(store, "list", namespace="canvas_bigquery")
    assert results == []


def test_tags_and_namespace_compose_as_and(tmp_path: Path) -> None:
    store = _store(tmp_path)
    results = _search(
        store,
        "mail",
        tags=("write",),
        namespace="canvas.microsoft365",
    )
    assert [item["name"] for item in results] == ["canvas.microsoft365.mail.send_mail"]

    # No skill in canvas.bigquery has the 'mail' tag → empty.
    results = _search(store, "datasets", tags=("mail",), namespace="canvas.bigquery")
    assert results == []


def test_filters_compose_with_task_type(tmp_path: Path) -> None:
    """task_type AND tags AND namespace all compose."""
    store = _store(tmp_path)
    results = _search(store, "finish", task_type="domain", namespace="finish")
    assert [item["name"] for item in results] == ["finish"]

    # task_type='browser' filters out the lone 'domain'-typed skill.
    results = _search(store, "finish", task_type="browser", namespace="finish")
    assert results == []


def test_defaults_preserve_back_compat(tmp_path: Path) -> None:
    store = _store(tmp_path)
    baseline, _ = store.search(
        "mail",
        search_type="fts",
        limit=10,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    with_defaults, _ = store.search(
        "mail",
        search_type="fts",
        limit=10,
        task_type=None,
        scope_clause="",
        scope_params=(),
        tags=(),
        namespace=None,
    )
    assert baseline == with_defaults


def test_filters_apply_before_paging(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # With tag filter, only 2 of 4 skills match.
    results = _search(store, "mail", tags=("microsoft365",), limit=5)
    assert len(results) == 2

    results = _search(store, "mail", tags=("microsoft365",), limit=1)
    assert len(results) == 1
    assert results[0]["name"].startswith("canvas.microsoft365.")


@pytest.mark.parametrize("search_type", ["fts", "regex", "exact"])
def test_filters_apply_across_search_types(search_type: str, tmp_path: Path) -> None:
    store = _store(tmp_path)
    query = "canvas.microsoft365.mail.send_mail" if search_type == "exact" else "mail"
    results = _search(
        store,
        query,
        tags=("microsoft365", "write"),
        namespace="canvas.microsoft365",
        search_type=search_type,
    )
    assert [item["name"] for item in results] == ["canvas.microsoft365.mail.send_mail"]


# -----------------------------------------------------------------------------
# LocalSkillStore.list() filters
# -----------------------------------------------------------------------------


def test_list_applies_tags_and_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records, total = store.list(
        page=1,
        page_size=10,
        task_type=None,
        origin=None,
        scope_clause="",
        scope_params=(),
        tags=("microsoft365",),
    )
    assert total == 2
    assert {record.name for record in records} == {
        "canvas.microsoft365.mail.search_mail",
        "canvas.microsoft365.mail.send_mail",
    }

    records, total = store.list(
        page=1,
        page_size=10,
        task_type=None,
        origin=None,
        scope_clause="",
        scope_params=(),
        namespace="canvas.bigquery",
    )
    assert total == 1
    assert records[0].name == "canvas.bigquery.list_datasets"


def test_list_defaults_preserve_back_compat(tmp_path: Path) -> None:
    store = _store(tmp_path)
    baseline = store.list(
        page=1,
        page_size=10,
        task_type=None,
        origin=None,
        scope_clause="",
        scope_params=(),
    )
    with_defaults = store.list(
        page=1,
        page_size=10,
        task_type=None,
        origin=None,
        scope_clause="",
        scope_params=(),
        tags=(),
        namespace=None,
    )
    assert baseline == with_defaults


# -----------------------------------------------------------------------------
# Pydantic model wiring
# -----------------------------------------------------------------------------


def test_skill_search_query_accepts_new_fields() -> None:
    query = SkillSearchQuery(
        query="mail",
        tags=["microsoft365", "write"],
        namespace="canvas.microsoft365",
    )
    assert query.tags == ["microsoft365", "write"]
    assert query.namespace == "canvas.microsoft365"

    # Defaults preserve back-compat.
    query = SkillSearchQuery(query="mail")
    assert query.tags == []
    assert query.namespace is None


def test_skill_query_accepts_new_fields() -> None:
    query = SkillQuery(task="search", tags=["mail"], namespace="canvas.microsoft365")
    assert query.tags == ["mail"]
    assert query.namespace == "canvas.microsoft365"

    query = SkillQuery(task="search")
    assert query.tags == []
    assert query.namespace is None


def test_skill_list_request_accepts_new_fields() -> None:
    req = SkillListRequest(tags=["microsoft365"], namespace="canvas.microsoft365")
    assert req.tags == ["microsoft365"]
    assert req.namespace == "canvas.microsoft365"


# -----------------------------------------------------------------------------
# LocalSkillProvider end-to-end
# -----------------------------------------------------------------------------


def _provider(tmp_path: Path) -> LocalSkillProvider:
    config = SkillsConfig(enabled=True, cache_dir=str(tmp_path))
    store = _store(tmp_path)
    return LocalSkillProvider(config, store=store)


@pytest.mark.asyncio
async def test_provider_search_forwards_filters(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    response = await provider.search(
        SkillSearchQuery(query="mail", tags=["write"], namespace="canvas.microsoft365"),
        tool_context={},
    )
    assert [skill.name for skill in response.skills] == [
        "canvas.microsoft365.mail.send_mail"
    ]


@pytest.mark.asyncio
async def test_provider_get_relevant_forwards_filters(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    response = await provider.get_relevant(
        SkillQuery(task="mail", tags=["write"], namespace="canvas.microsoft365"),
        tool_context={},
    )
    assert [skill.name for skill in response.skills] == [
        "canvas.microsoft365.mail.send_mail"
    ]


@pytest.mark.asyncio
async def test_provider_list_forwards_filters(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    response = await provider.list(
        SkillListRequest(tags=["microsoft365"]),
        tool_context={},
    )
    assert {entry.name for entry in response.skills} == {
        "canvas.microsoft365.mail.search_mail",
        "canvas.microsoft365.mail.send_mail",
    }
    assert response.total == 2


def test_fts_results_are_ordered_by_relevance_not_name_length(tmp_path: Path) -> None:
    """Issue 128 bug 4: bm25() is negative (more negative = better); scores must reflect that."""
    store = LocalSkillStore(db_path=tmp_path / "relevance.db")
    for skill in (
        SkillDefinition(
            name="ops.restart-failed-database-replica",
            trigger="restart failed database replica after replication failure",
            steps=["Restart the replica."],
        ),
        SkillDefinition(
            name="ops.billing",
            trigger="billing question about a failed payment",
            steps=["Check the invoice."],
        ),
    ):
        store.upsert_pack_skill(skill, pack_name="ops", scope_mode="project", update_existing=True)

    results = _search(store, "restart the failed database replica, not a payment")

    assert [item["name"] for item in results] == ["ops.restart-failed-database-replica", "ops.billing"]
    assert results[0]["score"] == 1.0
    assert results[1]["score"] == 0.0
