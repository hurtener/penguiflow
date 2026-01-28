from __future__ import annotations

import asyncio

import pytest

from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import BackgroundTasksConfig
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import InMemorySessionStateStore, SessionManager, TaskResult, TaskStatus
from penguiflow.sessions.models import MergeStrategy
from penguiflow.sessions.task_service import InProcessTaskService


class MockJSONLLMClient:
    async def complete(  # type: ignore[no-untyped-def]
        self,
        *,
        messages,
        response_format=None,
        stream=False,
        on_stream_chunk=None,
    ):
        _ = messages, response_format, stream, on_stream_chunk
        return '{"thought":"ok","next_node":null,"args":{"raw_answer":"done"}}'


def _planner_factory() -> ReactPlanner:
    catalog = build_catalog([], ModelRegistry())
    return ReactPlanner(llm_client=MockJSONLLMClient(), catalog=catalog, max_iters=1)


@pytest.mark.asyncio
async def test_inprocess_task_service_spawns_background_task() -> None:
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(sessions=sessions, planner_factory=_planner_factory)

    result = await service.spawn(session_id="s1", query="do work", parent_task_id="foreground")
    assert result.session_id == "s1"
    assert result.task_id

    session = await sessions.get_or_create("s1")
    # Wait briefly for background task to finish.
    for _ in range(20):
        task = await session.get_task(result.task_id)
        if task is not None and task.status in {TaskStatus.COMPLETE, TaskStatus.FAILED}:
            break
        await asyncio.sleep(0.01)
    task = await session.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETE


@pytest.mark.asyncio
async def test_inprocess_task_service_idempotency_key_reuses_task() -> None:
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(sessions=sessions, planner_factory=_planner_factory)

    first = await service.spawn(session_id="s2", query="do work", idempotency_key="k1")
    second = await service.spawn(session_id="s2", query="do work", idempotency_key="k1")
    assert second.task_id == first.task_id


@pytest.mark.asyncio
async def test_inprocess_task_service_controls_and_patch_flow() -> None:
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(sessions=sessions, planner_factory=_planner_factory)

    spawned = await service.spawn(session_id="s3", query="work")
    session = await sessions.get_or_create("s3")

    for _ in range(100):
        task = await session.get_task(spawned.task_id)
        if task is not None and task.status in {TaskStatus.COMPLETE, TaskStatus.FAILED}:
            break
        await asyncio.sleep(0.01)

    summaries = await service.list(session_id="s3")
    assert any(summary.task_id == spawned.task_id for summary in summaries)

    details = await service.get(session_id="s3", task_id=spawned.task_id, include_result=True)
    assert details is not None
    assert details.has_result is True
    assert details.spawned_from_task_id is not None
    assert details.result_digest

    patches = session.pending_patches
    assert len(patches) == 1
    patch_id = next(iter(patches.keys()))
    ok = await service.apply_patch(session_id="s3", patch_id=patch_id, action="apply")
    assert ok is True
    background = session.get_background_results()
    assert spawned.task_id in background
    llm_context, _ = session.get_context()
    assert "background_results" not in llm_context

    cleaned = await service.acknowledge_background(
        session_id="s3",
        task_ids=[spawned.task_id],
    )
    assert cleaned == 1
    background = session.get_background_results()
    assert spawned.task_id not in background
    llm_context, _ = session.get_context()
    assert "background_results" not in llm_context

    # Reject is routed through steering.
    spawned2 = await service.spawn(session_id="s3", query="work2")
    for _ in range(100):
        patches = session.pending_patches
        if patches:
            break
        await asyncio.sleep(0.01)
    patch_id2 = next(iter(session.pending_patches.keys()))
    ok = await service.apply_patch(session_id="s3", patch_id=patch_id2, action="reject")
    assert ok is True
    assert patch_id2 not in session.pending_patches

    # Prioritize uses steering for priority update.
    ok = await service.prioritize(session_id="s3", task_id=spawned2.task_id, priority=10)
    assert ok is True

    # Cancel uses session cancel flow.
    spawned3 = await service.spawn(session_id="s3", query="work3")
    ok = await service.cancel(session_id="s3", task_id=spawned3.task_id, reason="stop")
    assert ok is True


@pytest.mark.asyncio
async def test_retain_turn_timeout_uses_config() -> None:
    sessions = SessionManager(state_store=InMemorySessionStateStore())

    async def quick_pipeline(runtime):  # type: ignore[no-untyped-def]
        _ = runtime
        return TaskResult(payload={"ok": True})

    def tool_job_factory(tool_name, tool_args):  # type: ignore[no-untyped-def]
        _ = tool_name, tool_args
        return quick_pipeline

    background_cfg = BackgroundTasksConfig(retain_turn_timeout_s=0.01)
    service = InProcessTaskService(
        sessions=sessions,
        planner_factory=None,
        tool_job_factory=tool_job_factory,
        background_config=background_cfg,
    )

    session = await sessions.get_or_create("s-timeout")
    recorded_timeouts: list[float | None] = []

    async def fake_wait(group_id, *, timeout_s=None):  # type: ignore[no-untyped-def]
        recorded_timeouts.append(timeout_s)
        group = await session.get_group(group_id=group_id)
        return group, True

    session.wait_for_group_completion = fake_wait  # type: ignore[method-assign]

    result = await asyncio.wait_for(
        service.spawn_tool_job(
            session_id="s-timeout",
            tool_name="slow",
            tool_args={},
            group="timeout-group",
            group_sealed=True,
            retain_turn=True,
            group_merge_strategy=MergeStrategy.APPEND,
        ),
        timeout=0.3,
    )

    assert result.retained is True
    assert result.group_completion is not None
    assert result.group_completion.timed_out is True
    assert recorded_timeouts
    assert all(timeout == 0.01 for timeout in recorded_timeouts)
