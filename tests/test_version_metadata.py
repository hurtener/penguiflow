from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "3.7.0"


def test_release_version_metadata_is_aligned() -> None:
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init_text = (ROOT / "penguiflow" / "__init__.py").read_text(encoding="utf-8")
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")

    pyproject_match = re.search(r'^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', pyproject_text, flags=re.MULTILINE)
    init_match = re.search(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', init_text, flags=re.MULTILINE)
    lock_match = re.search(
        r'^\[\[package\]\]\nname\s*=\s*"penguiflow"\nversion\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
        lock_text,
        flags=re.MULTILINE,
    )

    assert pyproject_match and pyproject_match.group(1) == EXPECTED_VERSION
    assert init_match and init_match.group(1) == EXPECTED_VERSION
    assert lock_match and lock_match.group(1) == EXPECTED_VERSION
    assert "<<<<<<<" not in lock_text
    assert ">>>>>>>" not in lock_text
