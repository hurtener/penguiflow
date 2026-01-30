from pathlib import Path

from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition


def test_local_store_search_and_rankings(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    alpha = SkillDefinition(
        name="pack.core.alpha",
        trigger="Alpha flow",
        steps=["Step A"],
    )
    beta = SkillDefinition(
        name="pack.core.beta",
        trigger="Beta flow",
        steps=["Step B"],
    )
    store.upsert_pack_skill(alpha, pack_name="core", scope_mode="project", update_existing=True)
    store.upsert_pack_skill(beta, pack_name="core", scope_mode="project", update_existing=True)

    results, effective = store.search(
        "alpha",
        search_type="regex",
        limit=5,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    assert effective == "regex"
    assert any(item["name"] == "pack.core.alpha" for item in results)

    results_exact, effective_exact = store.search(
        "pack.core.beta",
        search_type="exact",
        limit=5,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )
    assert effective_exact == "exact"
    assert any(item["name"] == "pack.core.beta" for item in results_exact)

    store.touch(["pack.core.beta"])
    top = list(
        store.list_top(
            limit=2,
            exclude_names=[],
            scope_clause="",
            scope_params=(),
        )
    )
    recent = list(
        store.list_recent(
            limit=2,
            exclude_names=[],
            scope_clause="",
            scope_params=(),
        )
    )
    assert top[0].name == "pack.core.beta"
    assert any(record.name == "pack.core.beta" for record in recent)
