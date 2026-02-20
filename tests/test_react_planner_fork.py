from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import BackgroundTasksConfig, ReactPlanner, ToolPolicy
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.task_tools import build_task_tool_specs


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


def test_react_planner_fork_filters_tasks_tools() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo"), Node(shout, name="shout")], registry)
    catalog.extend(build_task_tool_specs())

    planner = ReactPlanner(
        llm="stub-llm",
        catalog=catalog,
        background_tasks=BackgroundTasksConfig(enabled=True),
    )
    assert "tasks.spawn" in planner._spec_by_name

    subagent = planner.fork(
        catalog_filter=lambda spec: not str(spec.name).startswith("tasks."),
        background_tasks=BackgroundTasksConfig(enabled=False, include_prompt_guidance=False),
    )
    assert "echo" in subagent._spec_by_name
    assert "tasks.spawn" not in subagent._spec_by_name
    assert subagent._background_tasks.enabled is False


def test_react_planner_fork_default_is_equivalent() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)

    cloned = planner.fork()
    assert set(cloned._spec_by_name) == set(planner._spec_by_name)


def test_react_planner_fork_inherits_auto_seq_flags() -> None:
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
    assert cloned._auto_seq_enabled is True
    assert cloned._auto_seq_execute is True
    assert cloned._auto_seq_read_only_only is False


def test_react_planner_fork_tool_policy_override_filters_tools() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    registry.register("shout", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo"), Node(shout, name="shout")], registry)
    planner = ReactPlanner(llm="stub-llm", catalog=catalog)
    assert set(planner._spec_by_name) == {"echo", "shout"}

    restricted = planner.fork(tool_policy=ToolPolicy(denied_tools={"shout"}))
    assert set(restricted._spec_by_name) == {"echo"}
