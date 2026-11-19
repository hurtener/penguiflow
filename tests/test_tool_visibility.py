"""Tests for tool visibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import NodeSpec, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import ToolVisibilityPolicy
from penguiflow.planner.react_runtime import _tool_visibility_scope
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from penguiflow.planner.context import ToolContext


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    text: str


@tool(desc="Echo text.")
async def echo(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Echo the input back."""
    return EchoOut(text=args.text)


@tool(desc="Shout text.")
async def shout(args: EchoArgs, _ctx: ToolContext) -> EchoOut:
    """Return the input text upper-cased."""
    return EchoOut(text=args.text.upper())


@dataclass(frozen=True, slots=True)
class _AllowListVisibility(ToolVisibilityPolicy):
    allowed: set[str]

    @override
    def visible_tools(
        self, specs: Sequence[NodeSpec], tool_context: Mapping[str, Any]
    ) -> list[NodeSpec]:
        return [
            spec
            for spec in specs
            if getattr(spec, "name", None) in self.allowed
        ]


def test_tool_visibility_scope_filters_catalog_and_restores() -> None:
    """Tool visibility scope filters catalog and restores."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog(
        [Node(echo, name="echo"), Node(shout, name="shout")], registry
    )
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)

    original_spec_names = set(planner.spec_by_name)
    original_system_prompt = planner.system_prompt

    policy = _AllowListVisibility(allowed={"echo"})
    with _tool_visibility_scope(
        planner, tool_visibility=policy, tool_context={"user_id": "u1"}
    ):
        assert set(planner.spec_by_name) == {"echo"}
        assert [record["name"] for record in planner.catalog_records] == [
            "echo"
        ]
        assert planner.system_prompt != original_system_prompt

    assert set(planner.spec_by_name) == original_spec_names
    assert planner.system_prompt == original_system_prompt
