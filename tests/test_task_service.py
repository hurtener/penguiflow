"""Tests for task service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import BackgroundTasksConfig, JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import (
    InMemorySessionStateStore,
    SessionManager,
    TaskResult,
    TaskStatus,
)
from penguiflow.sessions.models import MergeStrategy, TaskState
from penguiflow.sessions.task_service import InProcessTaskService
from penguiflow.state.models import TaskStateModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from penguiflow.sessions.models import TaskGroup
    from penguiflow.sessions.session import TaskRuntime


class MockJSONLLMClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        return '{"thought":"ok","next_node":null,"args":{"raw_answer":"done"}}'


def _planner_factory() -> ReactPlanner:
    catalog = build_catalog([], ModelRegistry())
    return ReactPlanner(
        llm_client=MockJSONLLMClient(), catalog=catalog, max_iters=1
    )


class JsonRoundTripStateStore(InMemorySessionStateStore):
    """Session store that round-trips tasks through JSON."""

    @override
    async def save_task(self, state: TaskState) -> None:
        TaskStateModel.from_state(state).model_dump(mode="json")
        await super().save_task(state)


@pytest.mark.asyncio
async def test_inprocess_task_service_spawns_background_task() -> None:
    """Inprocess task service spawns background task."""
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(
        sessions=sessions, planner_factory=_planner_factory
    )

    result = await service.spawn(
        session_id="s1", query="do work", parent_task_id="foreground"
    )
    assert result.session_id == "s1"
    assert result.task_id

    session = await sessions.get_or_create("s1")
    # Wait briefly for background task to finish.
    for _ in range(20):
        task = await session.get_task(result.task_id)
        if task is not None and task.status in {
            TaskStatus.COMPLETE,
            TaskStatus.FAILED,
        }:
            break
        await asyncio.sleep(0.01)
    task = await session.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETE


@pytest.mark.asyncio
async def test_task_service_sanitizes_tool_context() -> None:
    """Inprocess task service sanitizes nonserializable tool context."""
    sessions = SessionManager(state_store=JsonRoundTripStateStore())
    service = InProcessTaskService(
        sessions=sessions, planner_factory=_planner_factory
    )

    session = await sessions.get_or_create("s-json")
    session.update_context(
        tool_context={
            "tenant_id": "tenant-1",
            "task_service": service,
            "custom_runtime": object(),
            "feature_flags": {"background": True},
        }
    )

    result = await service.spawn(
        session_id="s-json", query="do work", parent_task_id="foreground"
    )
    assert result.session_id == "s-json"
    assert result.task_id

    task = await session.get_task(result.task_id)
    assert task is not None
    assert task.context_snapshot.tool_context["tenant_id"] == "tenant-1"
    assert task.context_snapshot.tool_context["feature_flags"] == {
        "background": True
    }
    assert "task_service" not in task.context_snapshot.tool_context
    assert "custom_runtime" not in task.context_snapshot.tool_context


@pytest.mark.asyncio
async def test_inprocess_task_service_idempotency_key_reuses_task() -> None:
    """Inprocess task service idempotency key reuses task."""
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(
        sessions=sessions, planner_factory=_planner_factory
    )

    first = await service.spawn(
        session_id="s2", query="do work", idempotency_key="k1"
    )
    second = await service.spawn(
        session_id="s2", query="do work", idempotency_key="k1"
    )
    assert second.task_id == first.task_id


@pytest.mark.asyncio
async def test_inprocess_task_service_controls_and_patch_flow() -> None:
    """Inprocess task service controls and patch flow."""
    sessions = SessionManager(state_store=InMemorySessionStateStore())
    service = InProcessTaskService(
        sessions=sessions, planner_factory=_planner_factory
    )

    spawned = await service.spawn(session_id="s3", query="work")
    session = await sessions.get_or_create("s3")

    for _ in range(100):
        task = await session.get_task(spawned.task_id)
        if task is not None and task.status in {
            TaskStatus.COMPLETE,
            TaskStatus.FAILED,
        }:
            break
        await asyncio.sleep(0.01)

    summaries = await service.list(session_id="s3")
    assert any(summary.task_id == spawned.task_id for summary in summaries)

    details = await service.get(
        session_id="s3", task_id=spawned.task_id, include_result=True
    )
    assert details is not None
    assert details.has_result is True
    assert details.spawned_from_task_id is not None
    assert details.result_digest

    patches = session.pending_patches
    assert len(patches) == 1
    patch_id = next(iter(patches.keys()))
    ok = await service.apply_patch(
        session_id="s3", patch_id=patch_id, action="apply"
    )
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
    ok = await service.apply_patch(
        session_id="s3", patch_id=patch_id2, action="reject"
    )
    assert ok is True
    assert patch_id2 not in session.pending_patches

    # Prioritize uses steering for priority update.
    ok = await service.prioritize(
        session_id="s3", task_id=spawned2.task_id, priority=10
    )
    assert ok is True

    # Cancel uses session cancel flow.
    spawned3 = await service.spawn(session_id="s3", query="work3")
    ok = await service.cancel(
        session_id="s3", task_id=spawned3.task_id, reason="stop"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_retain_turn_timeout_uses_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain turn timeout uses config."""
    sessions = SessionManager(state_store=InMemorySessionStateStore())

    async def quick_pipeline(_runtime: TaskRuntime) -> TaskResult:
        return TaskResult(payload={"ok": True})

    def tool_job_factory(
        _tool_name: str, _tool_args: object
    ) -> Callable[[TaskRuntime], Awaitable[TaskResult]]:
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

    async def fake_wait(
        group_id: str, *, timeout_s: float | None = None
    ) -> tuple[TaskGroup | None, bool]:
        recorded_timeouts.append(timeout_s)
        group = await session.get_group(group_id=group_id)
        return group, True

    monkeypatch.setattr(session, "wait_for_group_completion", fake_wait)

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
