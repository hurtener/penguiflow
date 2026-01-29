import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolSearchConfig
from penguiflow.planner.models import PlannerEvent
from penguiflow.registry import ModelRegistry


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


@tool(desc="Always-loaded echo tool.", loading_mode=ToolLoadingMode.ALWAYS)
async def always_tool(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text)


@tool(desc="Deferred echo tool for testing.")
async def deferred_tool(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text)


class StubClient:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


def _build_catalog(*, default_loading_mode: ToolLoadingMode | None = None) -> list[Any]:
    registry = ModelRegistry()
    registry.register("always_tool", EchoArgs, EchoOut)
    registry.register("deferred_tool", EchoArgs, EchoOut)
    nodes = [
        Node(always_tool, name="always_tool"),
        Node(deferred_tool, name="deferred_tool"),
    ]
    return build_catalog(nodes, registry, default_loading_mode=default_loading_mode)


@pytest.mark.asyncio
async def test_tool_search_returns_deferred_by_default(tmp_path: Any) -> None:
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
        if event.event_type == "tool_call_result" and event.extra.get("tool_name") == "tool_search"
    ]
    assert results
    payload = json.loads(results[-1].extra.get("result_json", "{}"))
    names = {item.get("name") for item in payload.get("tools", [])}
    assert "deferred_tool" in names
    assert "always_tool" not in names


@pytest.mark.asyncio
async def test_deferred_tool_activates_on_first_use(tmp_path: Any) -> None:
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
        if event.event_type == "tool_activated" and event.extra.get("tool_name") == "deferred_tool"
    ]
    assert activated
