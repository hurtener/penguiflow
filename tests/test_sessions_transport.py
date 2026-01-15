from __future__ import annotations

import asyncio

import pytest

from penguiflow.sessions import StreamingSession, TaskResult, TaskType, UpdateType
from penguiflow.sessions.transport import SessionConnection, Transport
from penguiflow.steering import SteeringEvent, SteeringEventType


class DummyTransport(Transport):
    def __init__(self, steering: list[SteeringEvent]) -> None:
        self._steering = list(steering)
        self.sent: list[tuple[str, str]] = []
        self.closed = False
        self._delivered = False

    async def send(self, update):  # type: ignore[no-untyped-def]
        self.sent.append((update.task_id, update.update_type.value))

    async def receive(self):  # type: ignore[no-untyped-def]
        if not self._delivered:
            self._delivered = True
            await asyncio.sleep(0.05)
        else:
            await asyncio.sleep(0)
        if not self._steering:
            return None
        return self._steering.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_session_connection_forwards_updates_and_steering() -> None:
    session = StreamingSession("s-transport")

    async def pipeline(runtime):
        event = await runtime.steering.next()
        return TaskResult(payload={"event": event.event_type.value})

    task_id = "t-transport"
    transport = DummyTransport(
        [
            SteeringEvent(
                session_id="s-transport",
                task_id=task_id,
                event_type=SteeringEventType.INJECT_CONTEXT,
                payload={"text": "hi"},
                source="user",
            )
        ]
    )
    conn = await session.connect(transport)
    assert isinstance(conn, SessionConnection)
    async with conn:
        result = await session.run_task(pipeline, task_type=TaskType.FOREGROUND, query="q", task_id=task_id)
        assert result.payload["event"] == SteeringEventType.INJECT_CONTEXT.value

    assert transport.closed is True
    assert any(update_type == UpdateType.STATUS_CHANGE.value for _, update_type in transport.sent)
