"""Tests for tool background mode."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Never

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import BackgroundTasksConfig, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.models import MergeStrategy, TaskStatus
from penguiflow.sessions.task_service import TaskSpawnResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext
    from penguiflow.sessions.task_service import ContextDepth


class DummyTaskService:
    """Task service that records spawn requests."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.job_calls: list[tuple[str, str, MergeStrategy, bool]] = []
        self.subagent_calls: list[tuple[str, str, MergeStrategy, bool]] = []
        # The remaining spawn options of each call, in call order.
        self.spawn_options: list[dict[str, object]] = []

    async def spawn_tool_job(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: object,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        task_id: str | None = None,
        context_tool_context: dict[str, Any] | None = None,
    ) -> TaskSpawnResult:
        """Record a tool-job spawn and return a pending task."""
        self.spawn_options.append(
            {
                "tool_args": tool_args,
                "parent_task_id": parent_task_id,
                "priority": priority,
                "propagate_on_cancel": propagate_on_cancel,
                "task_id": task_id,
                "context_tool_context": context_tool_context,
            }
        )
        self.job_calls.append(
            (session_id, tool_name, merge_strategy, notify_on_complete)
        )
        return TaskSpawnResult(
            task_id="bg_job", session_id=session_id, status=TaskStatus.PENDING
        )

    async def spawn(
        self,
        *,
        session_id: str,
        query: str,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        context_depth: ContextDepth = "full",
        task_id: str | None = None,
        idempotency_key: str | None = None,
        context_tool_context: dict[str, Any] | None = None,
    ) -> TaskSpawnResult:
        """Record a subagent spawn and return a pending task."""
        self.spawn_options.append(
            {
                "query": query,
                "parent_task_id": parent_task_id,
                "priority": priority,
                "propagate_on_cancel": propagate_on_cancel,
                "context_depth": context_depth,
                "task_id": task_id,
                "idempotency_key": idempotency_key,
                "context_tool_context": context_tool_context,
            }
        )
        self.subagent_calls.append(
            (session_id, "subagent", merge_strategy, notify_on_complete)
        )
        return TaskSpawnResult(
            task_id="bg_sub", session_id=session_id, status=TaskStatus.PENDING
        )


class Args(BaseModel):
    """Tool arguments."""

    x: int


class Out(BaseModel):
    """Tool output."""

    ok: bool


@tool(
    desc="Slow tool that runs in background.",
    extra={
        "background": {
            "enabled": True,
            "mode": "job",
            "default_merge_strategy": "replace",
            "notify_on_complete": False,
        }
    },
)
async def slow_tool(_args: Args, _ctx: ToolContext) -> Never:
    """Sleep briefly, then succeed."""
    msg = "slow_tool should not execute inline when background is enabled"
    raise AssertionError(msg)


class ScriptedClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, tool_name: str) -> None:
        """Store ``tool_name``."""
        self.calls = 0
        self._tool_name = tool_name

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        self.calls += 1
        if self.calls == 1:
            return (
                '{"thought":"spawn","next_node":"'
                + self._tool_name
                + '","args":{"x":1}}'
            )
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


@pytest.mark.asyncio
async def test_tool_background_spawns_job_instead_of_executing() -> None:
    """Tool background spawns job instead of executing."""
    registry = ModelRegistry()
    registry.register("slow_tool", Args, Out)
    nodes = [Node(slow_tool, name="slow_tool")]
    catalog = build_catalog(nodes, registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient("slow_tool"),
        catalog=catalog,
        max_iters=2,
        background_tasks=BackgroundTasksConfig(
            enabled=True,
            allow_tool_background=True,
            include_prompt_guidance=False,
        ),
    )
    service = DummyTaskService()
    result = await planner.run(
        "hi",
        tool_context={
            "session_id": "s1",
            "task_id": "t_foreground",
            "task_service": service,
        },
    )
    assert result.payload["raw_answer"] == "ok"
    assert service.job_calls == [
        ("s1", "slow_tool", MergeStrategy.REPLACE, False)
    ]


@tool(
    desc="Slow tool that runs in background via subagent.",
    extra={
        "background": {
            "enabled": True,
            "mode": "subagent",
            "default_merge_strategy": "append",
            "notify_on_complete": True,
        }
    },
)
async def slow_subagent_tool(_args: Args, _ctx: ToolContext) -> Never:
    """Sleep briefly as a subagent would, then succeed."""
    msg = (
        "slow_subagent_tool should not execute inline when background is "
        "enabled"
    )
    raise AssertionError(msg)


@pytest.mark.asyncio
async def test_tool_background_subagent_mode_calls_spawn() -> None:
    """Tool background subagent mode calls spawn."""
    registry = ModelRegistry()
    registry.register("slow_subagent_tool", Args, Out)
    nodes = [Node(slow_subagent_tool, name="slow_subagent_tool")]
    catalog = build_catalog(nodes, registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient("slow_subagent_tool"),
        catalog=catalog,
        max_iters=2,
        background_tasks=BackgroundTasksConfig(
            enabled=True,
            allow_tool_background=True,
            include_prompt_guidance=False,
        ),
    )
    service = DummyTaskService()
    result = await planner.run(
        "hi",
        tool_context={
            "session_id": "s1",
            "task_id": "t_foreground",
            "task_service": service,
        },
    )
    assert result.payload["raw_answer"] == "ok"
    assert service.subagent_calls == [
        ("s1", "subagent", MergeStrategy.APPEND, True)
    ]
