from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import StreamingSession, TaskType, UpdateType
from penguiflow.sessions.tool_jobs import ToolJobContext, build_tool_job_pipeline


class Args(BaseModel):
    x: int


class Out(BaseModel):
    answer: str


@tool(desc="Tool for job mode.")
async def job_tool(args: Args, ctx):  # type: ignore[no-untyped-def]
    _ = ctx
    return {"answer": f"ok:{args.x}"}


@pytest.mark.asyncio
async def test_tool_job_context_pause_not_supported() -> None:
    ctx = ToolJobContext(llm_context={"k": "v"}, tool_context={"x": 1}, artifacts=None)  # type: ignore[arg-type]
    assert ctx.llm_context["k"] == "v"
    assert ctx.tool_context["x"] == 1
    with pytest.raises(RuntimeError):
        await ctx.pause("user_confirmation")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_build_tool_job_pipeline_runs_tool_and_emits_tool_call() -> None:
    registry = ModelRegistry()
    registry.register("job_tool", Args, Out)
    spec = build_catalog([Node(job_tool, name="job_tool")], registry)[0]

    session = StreamingSession("s-tooljob")
    pipeline = build_tool_job_pipeline(spec=spec, args_payload={"x": 3})
    task_id = "t-job"

    result = await session.run_task(pipeline, task_type=TaskType.BACKGROUND, task_id=task_id, query=None)
    assert result.payload["answer"] == "ok:3"
    assert result.context_patch is not None
    assert "ok:3" in (result.context_patch.digest[0] if result.context_patch.digest else "")

    for _ in range(50):
        updates = await session.list_updates(task_id=task_id)
        if any(update.update_type == UpdateType.TOOL_CALL for update in updates):
            break
        await asyncio.sleep(0.01)
    updates = await session.list_updates(task_id=task_id)
    assert any(update.update_type == UpdateType.TOOL_CALL for update in updates)

