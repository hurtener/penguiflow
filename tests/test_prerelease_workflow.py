from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prerelease_workflow_publishes_only_alpha_and_rc_tags() -> None:
    workflow = ROOT / ".github" / "workflows" / "publish-prerelease.yml"
    text = workflow.read_text(encoding="utf-8")

    assert '"v*.*.*a*"' in text
    assert '"v*.*.*rc*"' in text
    assert r"v\d+\.\d+\.\d+(a\d+|rc\d+)" in text
    assert "pyproject.toml" in text
    assert "pypa/gh-action-pypi-publish@release/v1" in text


def test_package_version_is_phase_5_api_freeze_candidate() -> None:
    pyproject = ROOT / "pyproject.toml"
    init = ROOT / "penguiflow" / "__init__.py"

    assert 'version = "3.7.0a5"' in pyproject.read_text(encoding="utf-8")
    assert '__version__ = "3.7.0a5"' in init.read_text(encoding="utf-8")
