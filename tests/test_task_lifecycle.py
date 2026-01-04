from __future__ import annotations

import asyncio

import pytest

from penguiflow.sessions import SessionLimits, StreamingSession, TaskResult, TaskStatus, TaskType
from penguiflow.steering import SteeringEvent, SteeringEventType


@pytest.mark.asyncio
async def test_session_pause_resume_updates_status() -> None:
    session = StreamingSession("session-lifecycle")

    async def pipeline(_runtime):
        await asyncio.sleep(0.2)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="pause")
    await asyncio.sleep(0.05)

    await session.steer(
        SteeringEvent(
            session_id="session-lifecycle",
            task_id=task_id,
            event_type=SteeringEventType.PAUSE,
            payload={},
            source="user",
        )
    )
    task = await session.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.PAUSED

    await session.steer(
        SteeringEvent(
            session_id="session-lifecycle",
            task_id=task_id,
            event_type=SteeringEventType.RESUME,
            payload={},
            source="user",
        )
    )
    task = await session.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_session_cancel_task_updates_status() -> None:
    session = StreamingSession("session-cancel")

    async def pipeline(_runtime):
        await asyncio.sleep(1.0)
        return TaskResult(payload={"answer": "late"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="cancel")
    await asyncio.sleep(0.05)
    await session.cancel_task(task_id, reason="stop")
    await asyncio.sleep(0.05)
    task = await session.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_session_limits_enforced() -> None:
    limits = SessionLimits(max_tasks_per_session=1)
    session = StreamingSession("session-limits", limits=limits)

    async def pipeline(_runtime):
        await asyncio.sleep(0.05)
        return TaskResult(payload={"answer": "ok"})

    await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="a")
    with pytest.raises(RuntimeError):
        await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="b")


@pytest.mark.asyncio
async def test_session_timeout_enforced() -> None:
    limits = SessionLimits(max_task_runtime_s=0.05)
    session = StreamingSession("session-timeout", limits=limits)

    async def pipeline(_runtime):
        await asyncio.sleep(0.2)
        return TaskResult(payload={"answer": "ok"})

    with pytest.raises(asyncio.TimeoutError):
        await session.run_task(pipeline, task_type=TaskType.FOREGROUND, query="timeout")


@pytest.mark.asyncio
async def test_steering_redirect_delivered_to_task() -> None:
    session = StreamingSession("session-redirect")

    async def pipeline(runtime):
        event = await runtime.steering.next()
        return TaskResult(
            payload={
                "event_type": event.event_type.value,
                "instruction": event.payload.get("instruction"),
            }
        )

    async def send_redirect() -> None:
        await asyncio.sleep(0.05)
        await session.steer(
            SteeringEvent(
                session_id="session-redirect",
                task_id=task_id,
                event_type=SteeringEventType.REDIRECT,
                payload={"instruction": "new direction"},
                source="user",
            )
        )

    task_id = "task-redirect"
    sender = asyncio.create_task(send_redirect())
    result = await session.run_task(pipeline, task_type=TaskType.FOREGROUND, query="original", task_id=task_id)
    await sender
    assert result.payload["event_type"] == SteeringEventType.REDIRECT.value
    assert result.payload["instruction"] == "new direction"


@pytest.mark.asyncio
async def test_steering_prioritize_updates_priority() -> None:
    session = StreamingSession("session-priority")

    async def pipeline(_runtime):
        await asyncio.sleep(0.2)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="priority", priority=0)
    await asyncio.sleep(0.05)
    await session.steer(
        SteeringEvent(
            session_id="session-priority",
            task_id=task_id,
            event_type=SteeringEventType.PRIORITIZE,
            payload={"priority": 5},
            source="user",
        )
    )
    task = await session.get_task(task_id)
    assert task is not None
    assert task.priority == 5


@pytest.mark.asyncio
async def test_background_context_snapshot_isolated() -> None:
    session = StreamingSession("session-isolation")
    nested = {"value": 1}
    session.update_context(llm_context={"nested": nested})

    async def pipeline(_runtime):
        await asyncio.sleep(0.1)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="snapshot")
    nested["value"] = 2
    task = await session.get_task(task_id)
    assert task is not None
    assert task.context_snapshot.llm_context["nested"]["value"] == 1
