from __future__ import annotations

from penguiflow.rich_output.registry import get_registry, load_registry


def test_registry_loads_components() -> None:
    registry = get_registry()
    assert registry.version
    assert "echarts" in registry.components
    assert "form" in registry.components
    assert registry.get("markdown") is not None


def test_registry_allowlist_filters() -> None:
    registry = load_registry()
    allowed = registry.allowlist({"markdown"})
    assert list(allowed.keys()) == ["markdown"]
