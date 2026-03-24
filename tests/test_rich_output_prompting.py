from __future__ import annotations

from penguiflow.rich_output.prompting import generate_component_system_prompt
from penguiflow.rich_output.registry import get_registry


def test_prompt_includes_components() -> None:
    registry = get_registry()
    prompt = generate_component_system_prompt(registry)
    assert "render_component" in prompt
    assert "build_chart_echarts" in prompt
    assert "build_grid" in prompt
    assert "render_report" in prompt
    assert "render_grid" in prompt
    assert "render_tabs" in prompt
    assert "render_accordion" in prompt
    assert 'next_node="parallel"' in prompt
    assert "artifact_ref" in prompt
    assert "`echarts`" in prompt
    assert "`form`" in prompt


def test_prompt_respects_allowlist() -> None:
    registry = get_registry()
    prompt = generate_component_system_prompt(registry, allowlist=["markdown"], include_examples=False)
    assert "`markdown`" in prompt
    assert "`echarts`" not in prompt
    assert "build_chart_echarts" not in prompt
    assert 'next_node="parallel"' not in prompt
    assert "When no typed builder tools are available in this runtime" in prompt
