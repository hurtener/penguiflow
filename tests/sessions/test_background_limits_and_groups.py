"""Issue 128: BackgroundTasksConfig limits reach the session; group cap, timeout and cancel."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from penguiflow.planner.models import BackgroundTasksConfig
from penguiflow.sessions import (
    InProcessTaskService,
    SessionLimits,
    SessionManager,
    StreamingSession,
    TaskResult,
    TaskStatus,
    TaskType,
)
from penguiflow.sessions.telemetry import TaskTelemetryEvent
from penguiflow.steering import SteeringEvent, SteeringEventType


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.events: list[TaskTelemetryEvent] = []

    async def emit(self, event: TaskTelemetryEvent) -> None:
        self.events.append(event)


def _blocking_pipeline(release: asyncio.Event, running: list[int] | None = None) -> Any:
    async def pipeline(_runtime: Any) -> TaskResult:
        if running is not None:
            running.append(1)
        try:
            await release.wait()
        finally:
            if running is not None:
                running.pop()
        return TaskResult(payload={"answer": "ok"})

    return pipeline


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Bug 5 / item 9: limits from BackgroundTasksConfig
# ---------------------------------------------------------------------------


def test_explicit_config_limits_override_a_copy_of_session_limits() -> None:
    shared = SessionLimits()
    session = StreamingSession("s-limits", limits=shared)

    session.configure_background_tasks(
        BackgroundTasksConfig(
            max_concurrent_tasks=1,
            max_tasks_per_session=2,
            task_timeout_s=60,
            max_pending_steering=1,
        )
    )

    assert session.limits.max_concurrent_tasks == 1
    assert session.limits.max_tasks_per_session == 2
    assert session.limits.max_task_runtime_s == 60.0
    assert session.limits.max_pending_user_messages == 1
    # SessionManager shares one SessionLimits object across sessions: it must not change.
    assert shared == SessionLimits()


def test_unset_config_fields_keep_session_limits() -> None:
    session = StreamingSession("s-defaults")

    session.configure_background_tasks(BackgroundTasksConfig(enabled=True))

    assert session.limits == SessionLimits()


def test_overrides_count_as_explicit() -> None:
    session = StreamingSession("s-overrides")

    session.configure_background_tasks(BackgroundTasksConfig(), max_tasks_per_session=1)

    assert session.limits.max_tasks_per_session == 1


@pytest.mark.asyncio
async def test_configured_concurrency_limit_is_enforced() -> None:
    session = StreamingSession("s-concurrency")
    session.configure_background_tasks(BackgroundTasksConfig(max_concurrent_tasks=1))
    release = asyncio.Event()
    running: list[int] = []
    peak = 0

    for _ in range(3):
        await session.spawn_task(_blocking_pipeline(release, running), task_type=TaskType.BACKGROUND)
    for _ in range(10):
        await asyncio.sleep(0)
        peak = max(peak, len(running))
    release.set()
    await _settle()

    assert peak == 1
    await session.close()


@pytest.mark.asyncio
async def test_configured_task_limit_is_enforced() -> None:
    session = StreamingSession("s-task-limit")
    session.configure_background_tasks(BackgroundTasksConfig(max_tasks_per_session=1))
    release = asyncio.Event()

    await session.spawn_task(_blocking_pipeline(release), task_type=TaskType.BACKGROUND)
    with pytest.raises(RuntimeError, match="task_limit_exceeded"):
        await session.spawn_task(_blocking_pipeline(release), task_type=TaskType.BACKGROUND)

    release.set()
    await session.close()


@pytest.mark.asyncio
async def test_configured_steering_cap_rejects_extra_user_messages() -> None:
    async def push_user_messages(session: StreamingSession, task_id: str, count: int) -> list[bool]:
        results = []
        for i in range(count):
            results.append(
                await session.steer(
                    SteeringEvent(
                        session_id=session.session_id,
                        task_id=task_id,
                        event_type=SteeringEventType.USER_MESSAGE,
                        payload={"text": f"m{i}"},
                        source="user",
                    )
                )
            )
        return results

    release = asyncio.Event()

    default_session = StreamingSession("s-steer-default")
    task_id = await default_session.spawn_task(_blocking_pipeline(release), task_type=TaskType.BACKGROUND)
    assert await push_user_messages(default_session, task_id, 3) == [True, True, False]

    capped = StreamingSession("s-steer-capped")
    capped.configure_background_tasks(BackgroundTasksConfig(max_pending_steering=1))
    task_id = await capped.spawn_task(_blocking_pipeline(release), task_type=TaskType.BACKGROUND)
    assert await push_user_messages(capped, task_id, 2) == [True, False]

    release.set()
    await default_session.close()
    await capped.close()


# ---------------------------------------------------------------------------
# Item 9: max_tasks_per_group / group_timeout_s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_auto_seals_at_explicit_cap() -> None:
    session = StreamingSession("s-group-cap")
    session.configure_background_tasks(BackgroundTasksConfig(max_tasks_per_group=2))
    group = await session.resolve_or_create_group(group_name="g", turn_id="t1")

    await session.add_task_to_group(group.group_id, "task-1")
    assert group.status == "open"
    await session.add_task_to_group(group.group_id, "task-2")
    assert group.status == "sealed"


@pytest.mark.asyncio
async def test_group_has_no_cap_when_unset() -> None:
    session = StreamingSession("s-group-nocap")
    session.configure_background_tasks(BackgroundTasksConfig(enabled=True))
    group = await session.resolve_or_create_group(group_name="g", turn_id="t1")

    for i in range(25):
        await session.add_task_to_group(group.group_id, f"task-{i}")

    assert group.status == "open"
    await session.ensure_group_capacity(group.group_id)


@pytest.mark.asyncio
async def test_spawn_into_full_group_raises_without_creating_a_task() -> None:
    release = asyncio.Event()
    manager = SessionManager()
    service = InProcessTaskService(
        sessions=manager,
        planner_factory=None,
        tool_job_factory=lambda _name, _args: _blocking_pipeline(release),
        background_config=BackgroundTasksConfig(max_tasks_per_group=1),
    )
    session = await manager.get_or_create("s-group-full")
    # The group already holds one task (joined before the cap applied) and is still open.
    group = await session.resolve_or_create_group(group_name="g", turn_id="t1")
    await session.add_task_to_group(group.group_id, "existing")
    tasks_before = await session.list_tasks()

    with pytest.raises(RuntimeError, match="group_task_limit_exceeded"):
        await service.spawn_tool_job(
            session_id="s-group-full",
            tool_name="demo",
            tool_args={},
            group_id=group.group_id,
        )

    assert len(await session.list_tasks()) == len(tasks_before)
    release.set()
    await session.close()


@pytest.mark.asyncio
async def test_group_timeout_cancels_pending_tasks_and_signals_completion() -> None:
    telemetry = _RecordingTelemetry()
    session = StreamingSession("s-group-timeout", telemetry_sink=telemetry)
    session.configure_background_tasks(BackgroundTasksConfig(group_timeout_s=0.05))
    group = await session.resolve_or_create_group(group_name="g", turn_id="t1")
    release = asyncio.Event()
    task_id = await session.spawn_task(
        _blocking_pipeline(release), task_type=TaskType.BACKGROUND, group_id=group.group_id
    )
    await session.add_task_to_group(group.group_id, task_id)

    await session.seal_group(group.group_id)
    finished, timed_out = await session.wait_for_group_completion(group.group_id, timeout_s=2.0)

    assert timed_out is False
    assert finished is not None and finished.status == "failed"
    assert finished.report_queued is True
    task = await session.get_task(task_id)
    assert task is not None and task.status == TaskStatus.CANCELLED
    await _settle()
    assert [e.event_type for e in telemetry.events].count("task_group_failed") == 1
    await session.close()


@pytest.mark.asyncio
async def test_group_timeout_watchdog_is_cancelled_on_completion_and_close() -> None:
    session = StreamingSession("s-group-watchdog")
    session.configure_background_tasks(BackgroundTasksConfig(group_timeout_s=30.0))

    done_group = await session.resolve_or_create_group(group_name="done", turn_id="t1")
    release = asyncio.Event()
    task_id = await session.spawn_task(
        _blocking_pipeline(release), task_type=TaskType.BACKGROUND, group_id=done_group.group_id
    )
    await session.add_task_to_group(done_group.group_id, task_id)
    await session.seal_group(done_group.group_id)
    watchdog = session._group_timeouts[done_group.group_id]
    release.set()
    await session.wait_for_group_completion(done_group.group_id, timeout_s=2.0)
    await _settle()
    assert done_group.group_id not in session._group_timeouts
    assert watchdog.cancelled()

    open_group = await session.resolve_or_create_group(group_name="open", turn_id="t1")
    blocked = asyncio.Event()
    task_id = await session.spawn_task(
        _blocking_pipeline(blocked), task_type=TaskType.BACKGROUND, group_id=open_group.group_id
    )
    await session.add_task_to_group(open_group.group_id, task_id)
    await session.seal_group(open_group.group_id)
    watchdog = session._group_timeouts[open_group.group_id]

    await session.close()
    await _settle()
    assert session._group_timeouts == {}
    assert watchdog.cancelled()


# ---------------------------------------------------------------------------
# Item 12: cancel_group signals completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("propagate", [True, False])
async def test_cancel_group_wakes_waiters_and_reports_once(propagate: bool) -> None:
    telemetry = _RecordingTelemetry()
    session = StreamingSession(f"s-cancel-{propagate}", telemetry_sink=telemetry)
    reports: list[str] = []

    async def fake_enqueue(group: Any) -> None:
        reports.append(group.group_id)

    session._enqueue_group_report = fake_enqueue  # type: ignore[method-assign]
    group = await session.resolve_or_create_group(group_name="g", turn_id="t1")
    release = asyncio.Event()
    task_id = await session.spawn_task(
        _blocking_pipeline(release), task_type=TaskType.BACKGROUND, group_id=group.group_id
    )
    await session.add_task_to_group(group.group_id, task_id)
    await session.seal_group(group.group_id)

    waiter = asyncio.create_task(session.wait_for_group_completion(group.group_id, timeout_s=2.0))
    await _settle()
    assert await session.cancel_group(group.group_id, reason="user", propagate=propagate)

    finished, timed_out = await asyncio.wait_for(waiter, timeout=1.0)
    assert timed_out is False
    assert finished is not None and finished.status == "failed"

    release.set()  # with propagate=False the task finishes on its own afterwards
    await _settle()
    assert reports == [group.group_id]
    assert [e.event_type for e in telemetry.events].count("task_group_failed") == 1
    await session.close()
