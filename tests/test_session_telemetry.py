from __future__ import annotations

import asyncio

import pytest

from penguiflow.sessions import StreamingSession, TaskResult, TaskType
from penguiflow.sessions.telemetry import TaskTelemetryEvent, TaskTelemetrySink


class BufferSink(TaskTelemetrySink):
    def __init__(self) -> None:
        self.events: list[TaskTelemetryEvent] = []

    async def emit(self, event: TaskTelemetryEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_session_emits_telemetry_for_background_task() -> None:
    sink = BufferSink()
    session = StreamingSession("telemetry-session", telemetry_sink=sink)

    async def pipeline(_runtime):
        await asyncio.sleep(0.05)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="x")
    # Give it time to complete.
    await asyncio.sleep(0.15)

    types = [event.event_type for event in sink.events if event.task_id == task_id]
    assert "task_spawned" in types
    assert "task_completed" in types

