"""Tests for ReAct planner fork."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import BackgroundTasksConfig, ReactPlanner, ToolPolicy
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.task_tools import build_task_tool_specs

if TYPE_CHECKING:
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


def test_react_planner_fork_filters_tasks_tools() -> None:
    """ReAct planner fork filters tasks tools."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog(
        [Node(echo, name="echo"), Node(shout, name="shout")], registry
    )
    catalog.extend(build_task_tool_specs())

    planner = ReactPlanner(
        llm="stub-llm",
        catalog=catalog,
        background_tasks=BackgroundTasksConfig(enabled=True),
    )
    assert "tasks.spawn" in planner.spec_by_name

    subagent = planner.fork(
        catalog_filter=lambda spec: not str(spec.name).startswith("tasks."),
        background_tasks=BackgroundTasksConfig(
            enabled=False, include_prompt_guidance=False
        ),
    )
    assert "echo" in subagent.spec_by_name
    assert "tasks.spawn" not in subagent.spec_by_name
    assert subagent.background_tasks.enabled is False


def test_react_planner_fork_default_is_equivalent() -> None:
    """ReAct planner fork default is equivalent."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)

    cloned = planner.fork()
    assert set(cloned.spec_by_name) == set(planner.spec_by_name)


def test_react_planner_fork_inherits_auto_seq_flags() -> None:
    """ReAct planner fork inherits auto seq flags."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(
        llm="stub-llm",
        catalog=catalog,
        auto_seq_enabled=True,
        auto_seq_execute=True,
        auto_seq_read_only_only=False,
    )

    cloned = planner.fork()
    assert cloned.auto_seq_enabled is True
    assert cloned.auto_seq_execute is True
    assert cloned.auto_seq_read_only_only is False


def test_react_planner_fork_tool_policy_override_filters_tools() -> None:
    """ReAct planner fork tool policy override filters tools."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog(
        [Node(echo, name="echo"), Node(shout, name="shout")], registry
    )
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)
    assert set(planner.spec_by_name) == {"echo", "shout"}

    restricted = planner.fork(tool_policy=ToolPolicy(denied_tools={"shout"}))
    assert set(restricted.spec_by_name) == {"echo"}
