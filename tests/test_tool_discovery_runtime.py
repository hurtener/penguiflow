from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ToolDirectoryConfig, ToolGroupConfig, ToolHintsConfig, ToolSearchConfig, Trajectory
from penguiflow.planner.llm import build_messages
from penguiflow.planner.react_runtime import _prepare_tool_discovery_context
from penguiflow.planner.tool_search_cache import ToolSearchCache
from penguiflow.registry import ModelRegistry


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
async def charting_start(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(ok=bool(args.query))


@tool(
    desc="Render a component",
    tags=["rich_output", "ui"],
    side_effects="pure",
    loading_mode=ToolLoadingMode.DEFERRED,
)
async def render_component(args: _Args, ctx: Any) -> _Out:
    del ctx
    return _Out(ok=bool(args.query))


class _DummyPlanner:
    def __init__(self, specs: list[Any], cache: ToolSearchCache, config: ToolSearchConfig) -> None:
        self._tool_search_cache = cache
        self._tool_search_config = config
        self._execution_specs = specs
        self._events: list[Any] = []
        self._time_source = lambda: 0.0

        # build_messages expects these counters
        self._finish_repair_history_count = 0
        self._arg_fill_repair_history_count = 0
        self._multi_action_history_count = 0
        self._render_component_failure_history_count = 0
        self._system_prompt = "BASE"
        self._spec_by_name: dict[str, Any] = {}
        self._token_budget = None

    def _emit_event(self, event: Any) -> None:
        self._events.append(event)


@pytest.mark.asyncio
async def test_prepare_tool_discovery_context_generates_hints_and_directory(tmp_path) -> None:
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
    planner = _DummyPlanner(specs, cache, tool_search_cfg)
    trajectory = Trajectory(query="start_analysis charting", llm_context={}, tool_context={})
    allowed_names = {spec.name for spec in specs}

    await _prepare_tool_discovery_context(planner, trajectory, allowed_names=allowed_names)

    assert "tool_hints" in trajectory.metadata
    assert "tools_directory" in trajectory.metadata
    assert "charting.start_chart_analysis" in trajectory.metadata["tool_hints"]
    assert "<tool_directory>" in trajectory.metadata["tools_directory"]
    assert any(getattr(ev, "event_type", None) == "tool_hints_generated" for ev in planner._events)
    assert any(getattr(ev, "event_type", None) == "tool_directory_rendered" for ev in planner._events)


@pytest.mark.asyncio
async def test_prepare_tool_discovery_context_uses_resume_user_input(tmp_path) -> None:
    registry = ModelRegistry()
    registry.register("charting.start_chart_analysis", _Args, _Out)
    specs = build_catalog([Node(charting_start, name="charting.start_chart_analysis")], registry)
    cache = ToolSearchCache(cache_dir=str(tmp_path))
    cache.sync_tools(specs)

    tool_search_cfg = ToolSearchConfig(
        enabled=True,
        cache_dir=str(tmp_path),
        hints=ToolHintsConfig(enabled=True, top_k=3, search_type="fts"),
    )
    planner = _DummyPlanner(specs, cache, tool_search_cfg)
    trajectory = Trajectory(query="original", llm_context={}, tool_context={})
    trajectory.resume_user_input = "start_analysis charting"
    allowed_names = {spec.name for spec in specs}

    await _prepare_tool_discovery_context(planner, trajectory, allowed_names=allowed_names)

    hint_events = [ev for ev in planner._events if getattr(ev, "event_type", None) == "tool_hints_generated"]
    assert hint_events
    assert hint_events[0].extra.get("query") == "start_analysis charting"


@pytest.mark.asyncio
async def test_build_messages_injects_tool_directory_and_hints() -> None:
    cache = ToolSearchCache(cache_dir=":memory:")
    planner = _DummyPlanner([], cache, ToolSearchConfig(enabled=False))
    trajectory = Trajectory(query="q", llm_context={}, tool_context={})
    trajectory.metadata["tool_hints"] = "<tool_hints>\n- x\n</tool_hints>"
    trajectory.metadata["tools_directory"] = "<tool_directory>\n- y\n</tool_directory>"

    messages = await build_messages(planner, trajectory)
    assert messages
    assert messages[0]["role"] == "system"
    assert "<tool_hints>" in messages[0]["content"]
    assert "<tool_directory>" in messages[0]["content"]
