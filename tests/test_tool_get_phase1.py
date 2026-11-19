"""Tests for tool get phase1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner.tool_get_tool import ToolGetArgs, tool_get
from penguiflow.registry import ModelRegistry

from .tool_context_doubles import FakeToolContext

if TYPE_CHECKING:
    from penguiflow.catalog import NodeSpec
    from penguiflow.planner.context import ToolContext
    from penguiflow.planner.models import PlannerEvent


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    text: str


@tool(
    desc="Echo the provided text.",
    loading_mode=ToolLoadingMode.DEFERRED,
    examples=[
        {
            "args": {"text": "hello"},
            "description": "Basic usage",
            "tags": ["minimal"],
        },
    ],
)
async def echo_tool(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Echo the input back."""
    return EchoOut(text=args.text)


class _DummyPlanner:
    def __init__(self, spec: NodeSpec, *, allowed: set[str]) -> None:
        self.execution_specs = [spec]
        self.execution_spec_by_name = {spec.name: spec}
        self.tool_visibility_allowed_names = allowed
        self.tool_aliases: dict[str, str] = {}
        self.events: list[Any] = []

    def emit_event(self, event: PlannerEvent) -> None:
        self.events.append(event)

    def time_source(self) -> float:
        return 0.0


class _DummyCtx(FakeToolContext):
    """Tool context bound to ``planner``."""

    def __init__(self, planner: _DummyPlanner) -> None:
        """Bind ``planner`` as the context's planner."""
        super().__init__()
        self.planner = planner


@pytest.mark.asyncio
async def test_tool_get_returns_schema_and_examples() -> None:
    """Tool get returns schema and examples."""
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
    """Tool get respects visibility."""
    registry = ModelRegistry()
    registry.register("echo_tool", EchoArgs, EchoOut)
    specs = build_catalog([Node(echo_tool, name="echo_tool")], registry)
    spec = specs[0]
    planner = _DummyPlanner(spec, allowed=set())
    ctx = _DummyCtx(planner)

    with pytest.raises(RuntimeError):
        await tool_get(ToolGetArgs(names=["echo_tool"]), ctx)
