"""Transport contracts for bidirectional session connectivity."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from penguiflow.steering import SteeringEvent

from .models import StateUpdate


class Transport(Protocol):
    async def send(self, update: StateUpdate) -> None: ...

    async def receive(self) -> SteeringEvent | None: ...

    async def close(self) -> None: ...


class SessionConnection:
    """Wires a StreamingSession to a bidirectional transport."""

    def __init__(self, session: StreamingSession, transport: Transport) -> None:
        self._session = session
        self._transport = transport
        self._tasks: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> SessionConnection:
        updates_iter = await self._session.subscribe()

        async def _forward_updates() -> None:
            async for update in updates_iter:
                await self._transport.send(update)

        async def _receive_steering() -> None:
            while True:
                event = await self._transport.receive()
                if event is None:
                    break
                await self._session.steer(event)

        self._tasks.append(asyncio.create_task(_forward_updates(), name="session:forward_updates"))
        self._tasks.append(asyncio.create_task(_receive_steering(), name="session:receive_steering"))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._transport.close()


if TYPE_CHECKING:
    from .session import StreamingSession

__all__ = ["SessionConnection", "Transport"]
