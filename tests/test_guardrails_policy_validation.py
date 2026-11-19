"""Tests for guardrails policy validation."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from penguiflow.planner.guardrails import load_policy_pack

if TYPE_CHECKING:
    from pathlib import Path


def test_policy_pack_requires_id_and_version(tmp_path: Path) -> None:
    """Policy pack requires ID and version."""
    content = textwrap.dedent(
        """
        gateway:
          mode: "enforce"
        """
    )
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_policy_pack(path)


def test_policy_pack_rejects_invalid_sections(tmp_path: Path) -> None:
    """Policy pack rejects invalid sections."""
    content = textwrap.dedent(
        """
        policy_pack: "default"
        version: "1.0.0"
        sync_rules: {}
        """
    )
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_policy_pack(path)
