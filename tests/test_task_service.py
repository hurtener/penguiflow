from __future__ import annotations

import asyncio

import pytest

from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import InMemorySessionStateStore, SessionManager, TaskStatus
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
    llm_context, _ = session.get_context()
    assert llm_context.get("background_results")

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
