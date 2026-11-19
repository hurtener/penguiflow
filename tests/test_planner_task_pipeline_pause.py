"""Tests for planner task pipeline pause."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import (
    MergeStrategy,
    PlannerTaskPipeline,
    StreamingSession,
    TaskStatus,
    TaskType,
)
from penguiflow.steering import SteeringEvent, SteeringEventType

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext


class PauseArgs(BaseModel):
    """Arguments of the pause tool."""

    prompt: str = "Approve?"


class PauseOut(BaseModel):
    """Output of the pause tool."""

    ok: bool = True


@tool(desc="Trigger a planner pause for approval.", side_effects="pure")
async def pause_tool(args: PauseArgs, ctx: ToolContext) -> PauseOut:
    """Pause the run for user input."""
    await ctx.pause("approval_required", {"prompt": args.prompt})
    return PauseOut(ok=True)


class PauseClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.calls = 0

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                '{"thought":"pause","next_node":'
                '"pause_tool","args":{"prompt":"Confirm?"}}'
            )
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


def _planner_factory() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("pause_tool", PauseArgs, PauseOut)
    catalog = build_catalog([Node(pause_tool, name="pause_tool")], registry)
    return ReactPlanner(
        llm_client=PauseClient(),
        catalog=catalog,
        max_iters=3,
        pause_enabled=True,
    )


@pytest.mark.asyncio
async def test_planner_task_pipeline_pause_resume_via_steering() -> None:
    """Planner task pipeline pause resume via steering."""
    session = StreamingSession("pause-session")
    pipeline = PlannerTaskPipeline(planner_factory=_planner_factory)

    task_id = await session.spawn_task(
        pipeline,
        task_type=TaskType.BACKGROUND,
        query="pause please",
        merge_strategy=MergeStrategy.HUMAN_GATED,
    )

    updates_iter = await session.subscribe(task_ids=[task_id])
    resume_token: str | None = None

    async def _wait_for_checkpoint() -> str:
        async for update in updates_iter:
            if update.update_type.value == "CHECKPOINT":
                token = update.content.get("resume_token")
                if isinstance(token, str):
                    return token
        msg = "no_checkpoint"
        raise RuntimeError(msg)

    resume_token = await asyncio.wait_for(_wait_for_checkpoint(), timeout=1.0)

    accepted = await session.steer(
        SteeringEvent(
            session_id="pause-session",
            task_id=task_id,
            event_type=SteeringEventType.APPROVE,
            payload={"resume_token": resume_token},
            source="user",
        )
    )
    assert accepted is True

    # Wait for completion.
    for _ in range(50):
        task = await session.get_task(task_id)
        if task is not None and task.status in {
            TaskStatus.COMPLETE,
            TaskStatus.FAILED,
        }:
            break
        await asyncio.sleep(0.02)
    task = await session.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETE
