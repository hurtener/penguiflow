from __future__ import annotations

import textwrap

import pytest

from penguiflow.planner.guardrails import load_policy_pack


def test_policy_pack_requires_id_and_version(tmp_path) -> None:
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


def test_policy_pack_rejects_invalid_sections(tmp_path) -> None:
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
