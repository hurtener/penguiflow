from __future__ import annotations

from penguiflow.rich_output.prompting import generate_component_system_prompt
from penguiflow.rich_output.registry import get_registry


def test_prompt_includes_components() -> None:
    registry = get_registry()
    prompt = generate_component_system_prompt(registry)
    assert "render_component" in prompt
    assert "`echarts`" in prompt
    assert "`form`" in prompt


def test_prompt_respects_allowlist() -> None:
    registry = get_registry()
    prompt = generate_component_system_prompt(registry, allowlist=["markdown"], include_examples=False)
    assert "`markdown`" in prompt
    assert "`echarts`" not in prompt
