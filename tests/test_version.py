from __future__ import annotations

import tomllib
from pathlib import Path

import penguiflow

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert penguiflow.__version__ == pyproject["project"]["version"]
