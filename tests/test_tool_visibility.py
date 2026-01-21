from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.react_runtime import _tool_visibility_scope
from penguiflow.registry import ModelRegistry


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


@tool(desc="Echo text.")
async def echo(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text)


@tool(desc="Shout text.")
async def shout(args: EchoArgs, ctx: Any) -> EchoOut:
    _ = ctx
    return EchoOut(text=args.text.upper())


@dataclass(frozen=True, slots=True)
class _AllowListVisibility:
    allowed: set[str]

    def visible_tools(self, specs: list[Any], tool_context: dict[str, Any]) -> list[Any]:
        _ = tool_context
        return [spec for spec in specs if getattr(spec, "name", None) in self.allowed]


def test_tool_visibility_scope_filters_catalog_and_restores() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo"), Node(shout, name="shout")], registry)
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)

    original_spec_names = set(planner._spec_by_name)
    original_system_prompt = planner._system_prompt

    policy = _AllowListVisibility(allowed={"echo"})
    with _tool_visibility_scope(planner, tool_visibility=policy, tool_context={"user_id": "u1"}):
        assert set(planner._spec_by_name) == {"echo"}
        assert [record["name"] for record in planner._catalog_records] == ["echo"]
        assert planner._system_prompt != original_system_prompt

    assert set(planner._spec_by_name) == original_spec_names
    assert planner._system_prompt == original_system_prompt

