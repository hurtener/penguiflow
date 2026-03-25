from __future__ import annotations

import re
from pathlib import Path

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


def test_frontend_renderer_registry_matches_backend_components() -> None:
    registry = load_registry()
    renderer_registry_path = (
        Path(__file__).resolve().parents[1]
        / "penguiflow"
        / "cli"
        / "playground_ui"
        / "src"
        / "lib"
        / "renderers"
        / "registry.ts"
    )
    text = renderer_registry_path.read_text(encoding="utf-8")
    frontend_components = set(re.findall(r"^\s{2}([a-z_]+):\s*\{", text, re.MULTILINE))

    assert set(registry.components) == frontend_components - {"mcp_app"}
