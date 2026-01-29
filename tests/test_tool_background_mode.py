from __future__ import annotations

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import BackgroundTasksConfig, ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.models import MergeStrategy


class DummyTaskService:
    def __init__(self) -> None:
        self.job_calls: list[tuple[str, str, MergeStrategy, bool]] = []
        self.subagent_calls: list[tuple[str, str, MergeStrategy, bool]] = []

    async def spawn_tool_job(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args,
        parent_task_id=None,
        priority=0,
        merge_strategy=MergeStrategy.HUMAN_GATED,
        propagate_on_cancel="cascade",
        notify_on_complete=True,
        task_id=None,
        context_tool_context=None,
    ):
        _ = tool_args, parent_task_id, priority, propagate_on_cancel, task_id, context_tool_context
        self.job_calls.append((session_id, tool_name, merge_strategy, notify_on_complete))

        class _Result:
            task_id = "bg_job"

            class _Status:
                value = "PENDING"

            status = _Status()

        return _Result()

    async def spawn(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id: str,
        query: str,
        parent_task_id=None,
        priority=0,
        merge_strategy=MergeStrategy.HUMAN_GATED,
        propagate_on_cancel="cascade",
        notify_on_complete=True,
        context_depth="full",
        task_id=None,
        idempotency_key=None,
        context_tool_context=None,
    ):
        _ = (
            query,
            parent_task_id,
            priority,
            propagate_on_cancel,
            context_depth,
            task_id,
            idempotency_key,
            context_tool_context,
        )
        self.subagent_calls.append((session_id, "subagent", merge_strategy, notify_on_complete))

        class _Result:
            task_id = "bg_sub"

            class _Status:
                value = "PENDING"

            status = _Status()

        return _Result()


class Args(BaseModel):
    x: int


class Out(BaseModel):
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
async def slow_tool(args: Args, ctx):  # type: ignore[no-untyped-def]
    _ = args, ctx
    raise AssertionError("slow_tool should not execute inline when background is enabled")


class ScriptedClient:
    def __init__(self, tool_name: str) -> None:
        self.calls = 0
        self._tool_name = tool_name

    async def complete(  # type: ignore[no-untyped-def]
        self,
        *,
        messages,
        response_format=None,
        stream=False,
        on_stream_chunk=None,
    ):
        _ = messages, response_format, stream, on_stream_chunk
        self.calls += 1
        if self.calls == 1:
            return '{"thought":"spawn","next_node":"' + self._tool_name + '","args":{"x":1}}'
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


@pytest.mark.asyncio
async def test_tool_background_spawns_job_instead_of_executing() -> None:
    registry = ModelRegistry()
    registry.register("slow_tool", Args, Out)
    nodes = [Node(slow_tool, name="slow_tool")]
    catalog = build_catalog(nodes, registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient("slow_tool"),
        catalog=catalog,
        max_iters=2,
        background_tasks=BackgroundTasksConfig(enabled=True, allow_tool_background=True, include_prompt_guidance=False),
    )
    service = DummyTaskService()
    result = await planner.run(
        "hi",
        tool_context={"session_id": "s1", "task_id": "t_foreground", "task_service": service},
    )
    assert result.payload["raw_answer"] == "ok"
    assert service.job_calls == [("s1", "slow_tool", MergeStrategy.REPLACE, False)]


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
async def slow_subagent_tool(args: Args, ctx):  # type: ignore[no-untyped-def]
    _ = args, ctx
    raise AssertionError("slow_subagent_tool should not execute inline when background is enabled")


@pytest.mark.asyncio
async def test_tool_background_subagent_mode_calls_spawn() -> None:
    registry = ModelRegistry()
    registry.register("slow_subagent_tool", Args, Out)
    nodes = [Node(slow_subagent_tool, name="slow_subagent_tool")]
    catalog = build_catalog(nodes, registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient("slow_subagent_tool"),
        catalog=catalog,
        max_iters=2,
        background_tasks=BackgroundTasksConfig(enabled=True, allow_tool_background=True, include_prompt_guidance=False),
    )
    service = DummyTaskService()
    result = await planner.run(
        "hi",
        tool_context={"session_id": "s1", "task_id": "t_foreground", "task_service": service},
    )
    assert result.payload["raw_answer"] == "ok"
    assert service.subagent_calls == [("s1", "subagent", MergeStrategy.APPEND, True)]
