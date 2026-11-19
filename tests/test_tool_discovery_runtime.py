"""Tests for tool discovery runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import NodeSpec, ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    ReactPlanner,
    ToolDirectoryConfig,
    ToolGroupConfig,
    ToolHintsConfig,
    ToolSearchConfig,
    Trajectory,
)
from penguiflow.planner.llm import build_messages
from penguiflow.planner.models import JSONLLMClient
from penguiflow.planner.react_runtime import _prepare_tool_discovery_context
from penguiflow.planner.tool_search_cache import ToolSearchCache
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from penguiflow.planner.context import ToolContext
    from penguiflow.planner.models import PlannerEvent


class _Args(BaseModel):
    query: str


class _Out(BaseModel):
    ok: bool


@tool(
    desc="Start chart analysis",
    tags=["mcp", "charting"],
    side_effects="external",
    loading_mode=ToolLoadingMode.DEFERRED,
)
async def charting_start(args: _Args, _ctx: ToolContext) -> _Out:
    """Start a chart analysis."""
    return _Out(ok=bool(args.query))


@tool(
    desc="Render a component",
    tags=["rich_output", "ui"],
    side_effects="pure",
    loading_mode=ToolLoadingMode.DEFERRED,
)
async def render_component(args: _Args, _ctx: ToolContext) -> _Out:
    """Render a component."""
    return _Out(ok=bool(args.query))


class _UnusedLLM(JSONLLMClient):
    """LLM client for planners whose model is never called."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        msg = "the LLM is not called in these tests"
        raise AssertionError(msg)


def _planner(
    specs: list[NodeSpec], cache: ToolSearchCache, config: ToolSearchConfig
) -> tuple[ReactPlanner, list[PlannerEvent]]:
    """Build a planner over ``specs`` using ``cache``; return its events."""
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=_UnusedLLM(),
        catalog=specs,
        tool_search=config,
        event_callback=events.append,
    )
    planner.tool_search_cache = cache
    return planner, events


@pytest.mark.asyncio
async def test_prepare_tool_discovery_context_generates_hints_and_directory(
    tmp_path: Path,
) -> None:
    """Prepare tool discovery context generates hints and directory."""
    registry = ModelRegistry()
    registry.register("charting.start_chart_analysis", _Args, _Out)
    registry.register("render_component", _Args, _Out)
    specs = build_catalog(
        [
            Node(charting_start, name="charting.start_chart_analysis"),
            Node(render_component, name="render_component"),
        ],
        registry,
    )
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    tool_search_cfg = ToolSearchConfig(
        enabled=True,
        cache_dir=str(tmp_path),
        hints=ToolHintsConfig(enabled=True, top_k=5, search_type="fts"),
        directory=ToolDirectoryConfig(
            enabled=True,
            include_default_groups=True,
            include_tool_counts=True,
            max_groups=10,
            max_tools_per_group=5,
            groups=[
                ToolGroupConfig(
                    name="charting",
                    match_namespaces=["charting"],
                    trigger="Charting tools",
                ),
            ],
        ),
    )
    planner, events = _planner(specs, cache, tool_search_cfg)
    trajectory = Trajectory(
        query="start_analysis charting", llm_context={}, tool_context={}
    )
    allowed_names = {spec.name for spec in specs}

    await _prepare_tool_discovery_context(
        planner, trajectory, allowed_names=allowed_names
    )

    assert "tool_hints" in trajectory.metadata
    assert "tools_directory" in trajectory.metadata
    assert "charting.start_chart_analysis" in trajectory.metadata["tool_hints"]
    assert "<tool_directory>" in trajectory.metadata["tools_directory"]
    assert any(
        getattr(ev, "event_type", None) == "tool_hints_generated"
        for ev in events
    )
    assert any(
        getattr(ev, "event_type", None) == "tool_directory_rendered"
        for ev in events
    )


@pytest.mark.asyncio
async def test_prepare_tool_discovery_context_uses_resume_user_input(
    tmp_path: Path,
) -> None:
    """Prepare tool discovery context uses resume user input."""
    registry = ModelRegistry()
    registry.register("charting.start_chart_analysis", _Args, _Out)
    specs = build_catalog(
        [Node(charting_start, name="charting.start_chart_analysis")], registry
    )
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    tool_search_cfg = ToolSearchConfig(
        enabled=True,
        cache_dir=str(tmp_path),
        hints=ToolHintsConfig(enabled=True, top_k=3, search_type="fts"),
    )
    planner, events = _planner(specs, cache, tool_search_cfg)
    trajectory = Trajectory(query="original", llm_context={}, tool_context={})
    trajectory.resume_user_input = "start_analysis charting"
    allowed_names = {spec.name for spec in specs}

    await _prepare_tool_discovery_context(
        planner, trajectory, allowed_names=allowed_names
    )

    hint_events = [
        ev
        for ev in events
        if getattr(ev, "event_type", None) == "tool_hints_generated"
    ]
    assert hint_events
    assert hint_events[0].extra.get("query") == "start_analysis charting"


@pytest.mark.asyncio
async def test_build_messages_injects_tool_directory_and_hints() -> None:
    """Build messages injects tool directory and hints."""
    cache = ToolSearchCache(cache_dir=":memory:")
    planner, _events = _planner([], cache, ToolSearchConfig(enabled=False))
    trajectory = Trajectory(query="q", llm_context={}, tool_context={})
    trajectory.metadata["tool_hints"] = "<tool_hints>\n- x\n</tool_hints>"
    trajectory.metadata["tools_directory"] = (
        "<tool_directory>\n- y\n</tool_directory>"
    )

    messages = await build_messages(planner, trajectory)
    assert messages
    assert messages[0]["role"] == "system"
    assert "<tool_hints>" in messages[0]["content"]
    assert "<tool_directory>" in messages[0]["content"]
