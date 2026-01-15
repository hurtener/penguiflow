from __future__ import annotations

import asyncio

import pytest

from penguiflow.sessions import StreamingSession, TaskResult, TaskType, UpdateType
from penguiflow.steering import SteeringEvent, SteeringEventType


@pytest.mark.asyncio
async def test_session_run_task_emits_result() -> None:
    session = StreamingSession("session-1")

    async def pipeline(runtime):
        runtime.emit_update(UpdateType.PROGRESS, {"label": "start"})
        return TaskResult(payload={"answer": "ok"}, digest=["ok"])

    updates: list[str] = []

    updates_iter = await session.subscribe()

    async def collect() -> None:
        async for update in updates_iter:
            updates.append(update.update_type.value)
            if update.update_type == UpdateType.RESULT:
                break

    collector = asyncio.create_task(collect())
    result = await session.run_task(pipeline, task_type=TaskType.FOREGROUND, query="hi")
    await collector

    assert result.payload == {"answer": "ok"}
    assert "RESULT" in updates


@pytest.mark.asyncio
async def test_control_policy_requires_confirmation() -> None:
    session = StreamingSession("session-2")

    async def pipeline(_runtime):
        await asyncio.sleep(0.01)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="hello")
    event = SteeringEvent(
        session_id="session-2",
        task_id=task_id,
        event_type=SteeringEventType.CANCEL,
        payload={},
        source="agent",
    )
    accepted = await session.steer(event)
    assert accepted is False
