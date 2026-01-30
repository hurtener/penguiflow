from __future__ import annotations

import sqlite3
from pathlib import Path

from penguiflow.skills.local_store import LocalSkillStore
from penguiflow.skills.models import SkillDefinition, SkillPackConfig, SkillsConfig
from penguiflow.skills.provider import LocalSkillProvider


def test_prune_pack_skills_removes_missing_entries(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(name="pack.core.one", trigger="One", steps=["s"]),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )
    store.upsert_pack_skill(
        SkillDefinition(name="pack.core.two", trigger="Two", steps=["s"]),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )

    removed = store.prune_pack_skills(pack_name="core", scope_mode="project", keep_names=["pack.core.one"])
    assert removed == 1
    remaining = store.get_by_name(["pack.core.one", "pack.core.two"], scope_clause="", scope_params=())
    assert [r.name for r in remaining] == ["pack.core.one"]


def test_prune_packs_not_in_config_removes_orphan_pack(tmp_path: Path) -> None:
    # Seed an "old" pack skill in the DB
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(name="pack.old.skill", trigger="Old", steps=["s"]),
        pack_name="old",
        scope_mode="project",
        update_existing=True,
    )

    cfg = SkillsConfig(
        enabled=True,
        cache_dir=str(tmp_path),
        skill_packs=[
            SkillPackConfig(
                name="new",
                path=str(tmp_path / "missing"),
                enabled=False,
            )
        ],
        prune_packs_not_in_config=True,
    )
    provider = LocalSkillProvider(cfg, store=store)
    provider.load_packs()

    remaining = store.search(
        "old",
        search_type="exact",
        limit=10,
        task_type=None,
        scope_clause="",
        scope_params=(),
    )[0]
    assert remaining == []


def test_prune_does_not_delete_learned_skills(tmp_path: Path) -> None:
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    store.upsert_pack_skill(
        SkillDefinition(name="pack.core.keep", trigger="Keep", steps=["s"]),
        pack_name="core",
        scope_mode="project",
        update_existing=True,
    )
    # Flip to learned in-place (simulates future learned skills)
    with sqlite3.connect(tmp_path / "skills.db") as conn:
        conn.execute(
            "UPDATE skills SET origin='learned', origin_ref='learned' WHERE name = ?",
            ("pack.core.keep",),
        )

    removed = store.prune_pack_skills(pack_name="core", scope_mode="project", keep_names=[])
    assert removed == 0
    remaining = store.get_by_name(["pack.core.keep"], scope_clause="", scope_params=())
    assert [r.origin for r in remaining] == ["learned"]
