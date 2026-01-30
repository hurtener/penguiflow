from __future__ import annotations

from pathlib import Path

from penguiflow.skills import SkillPackConfig
from penguiflow.skills.pack_loader import SkillPackLoader


def test_skill_pack_loader_generates_names(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    yaml_path = pack_dir / "login.skill.yaml"
    yaml_path.write_text(
        "trigger: Log into a site\nsteps:\n  - Open the login page\n  - Enter credentials\n",
        encoding="utf-8",
    )
    jsonl_path = pack_dir / "misc.skill.jsonl"
    jsonl_path.write_text(
        '{"name": "pack.core.explicit", "trigger": "Explicit", "steps": ["Do it"]}\n'
        '{"trigger": "Fallback", "steps": ["Step A"]}\n',
        encoding="utf-8",
    )
    pack = SkillPackConfig(name="core", path=str(pack_dir))
    loader = SkillPackLoader()
    skills = loader.load_pack(pack)
    names = {skill.name for skill in skills}
    assert "pack.core.log_into_a_site" in names
    assert "pack.core.explicit" in names
    assert "pack.core.fallback" in names


def test_skill_pack_loader_reads_markdown_frontmatter(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    md_path = pack_dir / "demo.skill.md"
    md_path.write_text(
        "---\ntrigger: Demo\nsteps:\n  - Step 1\n---\n",
        encoding="utf-8",
    )
    pack = SkillPackConfig(name="core", path=str(pack_dir), format="md")
    skills = SkillPackLoader().load_pack(pack)
    assert skills
    assert skills[0].trigger == "Demo"
    assert skills[0].steps == ["Step 1"]


def test_skill_pack_loader_missing_path_returns_empty(tmp_path: Path) -> None:
    pack = SkillPackConfig(name="core", path=str(tmp_path / "missing"))
    skills = SkillPackLoader().load_pack(pack)
    assert skills == []
