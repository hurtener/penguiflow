from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner.tool_get_tool import ToolGetArgs, tool_get
from penguiflow.registry import ModelRegistry


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


@tool(
    desc="Echo the provided text.",
    loading_mode=ToolLoadingMode.DEFERRED,
    examples=[
        {"args": {"text": "hello"}, "description": "Basic usage", "tags": ["minimal"]},
    ],
)
async def echo_tool(args: EchoArgs, ctx: Any) -> EchoOut:
    del ctx
    return EchoOut(text=args.text)


class _DummyPlanner:
    def __init__(self, spec: Any, *, allowed: set[str]) -> None:
        self._execution_specs = [spec]
        self._execution_spec_by_name = {spec.name: spec}
        self._tool_visibility_allowed_names = allowed
        self._tool_aliases: dict[str, str] = {}
        self._events: list[Any] = []

    def _emit_event(self, event: Any) -> None:
        self._events.append(event)

    def _time_source(self) -> float:
        return 0.0


class _DummyCtx:
    def __init__(self, planner: _DummyPlanner) -> None:
        self._planner = planner


@pytest.mark.asyncio
async def test_tool_get_returns_schema_and_examples() -> None:
    registry = ModelRegistry()
    registry.register("echo_tool", EchoArgs, EchoOut)
    specs = build_catalog([Node(echo_tool, name="echo_tool")], registry)
    spec = specs[0]
    planner = _DummyPlanner(spec, allowed={"echo_tool"})
    ctx = _DummyCtx(planner)

    result = await tool_get(ToolGetArgs(names=["echo_tool"]), ctx)
    assert result.returned == 1
    record = result.tools[0]
    assert record.name == "echo_tool"
    assert record.args_schema is not None
    assert record.out_schema is not None
    assert record.examples
    assert record.examples[0]["args"]["text"] == "hello"


@pytest.mark.asyncio
async def test_tool_get_respects_visibility() -> None:
    registry = ModelRegistry()
    registry.register("echo_tool", EchoArgs, EchoOut)
    specs = build_catalog([Node(echo_tool, name="echo_tool")], registry)
    spec = specs[0]
    planner = _DummyPlanner(spec, allowed=set())
    ctx = _DummyCtx(planner)

    with pytest.raises(RuntimeError):
        await tool_get(ToolGetArgs(names=["echo_tool"]), ctx)
