from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore, ScopedArtifacts
from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import StreamingSession, TaskType, UpdateType
from penguiflow.sessions.tool_jobs import ToolJobContext, _extract_artifacts_from_observation, build_tool_job_pipeline


class Args(BaseModel):
    x: int


class Out(BaseModel):
    answer: str


class ArtifactOut(BaseModel):
    report: str = Field(json_schema_extra={"artifact": True})


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


class OutWithArtifact(BaseModel):
    answer: str
    chart_artifacts: dict | None = Field(default=None, json_schema_extra={"artifact": True})


@tool(desc="Tool for job mode with artifacts.")
async def job_tool_with_artifact(args: Args, ctx):  # type: ignore[no-untyped-def]
    _ = ctx
    return {
        "answer": f"ok:{args.x}",
        "chart_artifacts": {"type": "echarts", "config": {"title": {"text": "Job chart"}}},
    }


@pytest.mark.asyncio
async def test_build_tool_job_pipeline_collects_artifact_fields() -> None:
    registry = ModelRegistry()
    registry.register("job_tool_with_artifact", Args, OutWithArtifact)
    spec = build_catalog([Node(job_tool_with_artifact, name="job_tool_with_artifact")], registry)[0]

    session = StreamingSession("s-tooljob-artifacts")
    pipeline = build_tool_job_pipeline(spec=spec, args_payload={"x": 3})
    task_id = "t-job-artifacts"

    result = await session.run_task(pipeline, task_type=TaskType.BACKGROUND, task_id=task_id, query=None)
    assert result.payload["answer"] == "ok:3"
    assert result.context_patch is not None
    assert result.context_patch.artifacts
    assert result.context_patch.artifacts[0]["node"] == "job_tool_with_artifact"
    assert result.context_patch.artifacts[0]["field"] == "chart_artifacts"
    stub = result.context_patch.artifacts[0]["artifact"]
    assert stub["type"] == "echarts"
    assert "config" not in stub
    assert stub["artifact"]["id"]
    assert result.artifacts == result.context_patch.artifacts


async def test_extract_artifacts_from_observation_propagates_full_scope() -> None:
    """_extract_artifacts_from_observation stores artifacts with the full scope."""
    store = InMemoryArtifactStore()
    scope = ArtifactScope(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
    )
    scoped = ScopedArtifacts(
        store,
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="sess-1",
        trace_id="trace-1",
    )
    observation = {"report": "some report data"}

    result = await _extract_artifacts_from_observation(
        node_name="test_node",
        out_model=ArtifactOut,
        observation=observation,
        artifacts=scoped,
    )

    assert len(result) == 1
    # Verify the stored artifact has the correct scope by listing from the store
    refs = await store.list(scope=scope)
    assert len(refs) >= 1
    ref = refs[0]
    assert ref.scope is not None
    assert ref.scope.session_id == "sess-1"
    assert ref.scope.tenant_id == "tenant-1"
    assert ref.scope.user_id == "user-1"
    assert ref.scope.trace_id == "trace-1"
