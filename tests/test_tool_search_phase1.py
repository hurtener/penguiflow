"""Tests for tool search phase1."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import NodeSpec, ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    ReactPlanner,
    ToolSearchConfig,
    ToolVisibilityPolicy,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from penguiflow.planner.context import ToolContext
    from penguiflow.planner.models import PlannerEvent


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    text: str


@tool(desc="Always-loaded echo tool.", loading_mode=ToolLoadingMode.ALWAYS)
async def always_tool(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Echo the input; always loaded."""
    return EchoOut(text=args.text)


@tool(desc="Deferred echo tool for testing.")
async def deferred_tool(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Echo the input; loaded on demand."""
    return EchoOut(text=args.text)


class StubClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: Sequence[object]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _build_catalog(
    *, default_loading_mode: ToolLoadingMode | None = None
) -> list[Any]:
    registry = ModelRegistry()
    registry.register("always_tool", EchoArgs, EchoOut)
    registry.register("deferred_tool", EchoArgs, EchoOut)
    nodes = [
        Node(always_tool, name="always_tool"),
        Node(deferred_tool, name="deferred_tool"),
    ]
    return build_catalog(
        nodes, registry, default_loading_mode=default_loading_mode
    )


@pytest.mark.asyncio
async def test_tool_search_returns_deferred_by_default(tmp_path: Path) -> None:
    """Tool search returns deferred by default."""
    responses = [
        {
            "next_node": "tool_search",
            "args": {"query": "deferred", "search_type": "regex", "limit": 5},
        },
        {"next_node": "final_response", "args": {"answer": "done"}},
    ]
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=_build_catalog(default_loading_mode=ToolLoadingMode.DEFERRED),
        max_iters=2,
        tool_search=ToolSearchConfig(
            enabled=True,
            cache_dir=str(tmp_path),
            default_loading_mode=ToolLoadingMode.DEFERRED,
        ),
        event_callback=events.append,
    )

    await planner.run("find the deferred tool")

    results = [
        event
        for event in events
        if event.event_type == "tool_call_result"
        and event.extra.get("tool_name") == "tool_search"
    ]
    assert results
    payload = json.loads(results[-1].extra.get("result_json", "{}"))
    names = {item.get("name") for item in payload.get("tools", [])}
    assert "deferred_tool" in names
    assert "always_tool" not in names


@pytest.mark.asyncio
async def test_deferred_tool_activates_on_first_use(tmp_path: Path) -> None:
    """Deferred tool activates on first use."""
    responses = [
        {"next_node": "deferred_tool", "args": {"text": "hello"}},
        {"next_node": "final_response", "args": {"answer": "ok"}},
    ]
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=_build_catalog(default_loading_mode=ToolLoadingMode.DEFERRED),
        max_iters=2,
        tool_search=ToolSearchConfig(
            enabled=True,
            cache_dir=str(tmp_path / "tool_cache"),
            default_loading_mode=ToolLoadingMode.DEFERRED,
        ),
        event_callback=events.append,
    )

    await planner.run("call the deferred tool")

    activated = [
        event
        for event in events
        if event.event_type == "tool_activated"
        and event.extra.get("tool_name") == "deferred_tool"
    ]
    assert activated


class _HideDeferredTool(ToolVisibilityPolicy):
    @override
    def visible_tools(
        self, specs: Sequence[NodeSpec], tool_context: Mapping[str, Any]
    ) -> Sequence[NodeSpec]:
        return [spec for spec in specs if spec.name != "deferred_tool"]


@pytest.mark.asyncio
async def test_parallel_deferred_tool_activates_on_first_use(
    tmp_path: Path,
) -> None:
    """Parallel deferred tool activates on first use."""
    responses = [
        {
            "next_node": "parallel",
            "args": {
                "steps": [{"node": "deferred_tool", "args": {"text": "hello"}}]
            },
        },
        {"next_node": "final_response", "args": {"answer": "ok"}},
    ]
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=_build_catalog(default_loading_mode=ToolLoadingMode.DEFERRED),
        max_iters=2,
        tool_search=ToolSearchConfig(
            enabled=True,
            cache_dir=str(tmp_path / "tool_cache_parallel"),
            default_loading_mode=ToolLoadingMode.DEFERRED,
        ),
        event_callback=events.append,
    )

    await planner.run(
        "call the deferred tool in parallel", tool_context={"session_id": "s1"}
    )

    activated = [
        event
        for event in events
        if event.event_type == "tool_activated"
        and event.extra.get("tool_name") == "deferred_tool"
    ]
    tool_results = [
        event
        for event in events
        if event.event_type == "tool_call_result"
        and event.extra.get("tool_name") == "deferred_tool"
    ]
    assert activated
    assert tool_results


@pytest.mark.asyncio
async def test_parallel_deferred_tool_activation_respects_visibility_policy(
    tmp_path: Path,
) -> None:
    """Parallel deferred tool activation respects visibility policy."""
    responses = [
        {
            "next_node": "parallel",
            "args": {
                "steps": [{"node": "deferred_tool", "args": {"text": "hello"}}]
            },
        },
        {"next_node": "final_response", "args": {"answer": "ok"}},
    ]
    events: list[PlannerEvent] = []
    planner = ReactPlanner(
        llm_client=StubClient(responses),
        catalog=_build_catalog(default_loading_mode=ToolLoadingMode.DEFERRED),
        max_iters=2,
        tool_search=ToolSearchConfig(
            enabled=True,
            cache_dir=str(tmp_path / "tool_cache_parallel_denied"),
            default_loading_mode=ToolLoadingMode.DEFERRED,
        ),
        event_callback=events.append,
    )

    await planner.run(
        "call the deferred tool in parallel",
        tool_context={"session_id": "s1"},
        tool_visibility=_HideDeferredTool(),
    )

    denied = [
        event
        for event in events
        if event.event_type == "tool_activation_denied"
        and event.extra.get("reason") == "visibility_policy"
    ]
    tool_starts = [
        event
        for event in events
        if event.event_type == "tool_call_start"
        and event.extra.get("tool_name") == "deferred_tool"
    ]
    assert denied
    assert tool_starts == []
