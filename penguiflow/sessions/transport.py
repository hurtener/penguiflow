"""Transport contracts for bidirectional session connectivity."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from penguiflow.steering import SteeringEvent

from .models import StateUpdate


class Transport(Protocol):
    """Bidirectional wire contract between a `StreamingSession` and a client.

    Implementations bridge `StateUpdate`s flowing out to the client and
    `SteeringEvent`s flowing in from the client, over any concrete transport
    (WebSocket, SSE + POST, in-process queues, etc.).
    """

    async def send(self, update: StateUpdate) -> None:
        """Send a state update to the client.

        Args:
            update: The state update to deliver.
        """
        ...

    async def receive(self) -> SteeringEvent | None:
        """Wait for and return the next steering event from the client.

        Returns:
            The next `SteeringEvent`, or None if the transport has closed and no
            more events will arrive.
        """
        ...

    async def close(self) -> None:
        """Close the transport and release any underlying resources."""
        ...


class SessionConnection:
    """Wires a StreamingSession to a bidirectional transport."""

    def __init__(self, session: StreamingSession, transport: Transport) -> None:
        """Initialize the connection.

        Args:
            session: The streaming session whose updates are forwarded and which
                receives steering events.
            transport: The bidirectional transport to wire the session to.
        """
        self._session = session
        self._transport = transport
        self._tasks: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> SessionConnection:
        """Start forwarding session updates to the transport and steering events back.

        Returns:
            This `SessionConnection`, with background forwarding tasks running.
        """
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
        """Cancel the forwarding tasks and close the transport.

        Args:
            exc_type: Exception type raised in the `with` block, if any.
            exc: Exception instance raised in the `with` block, if any.
            tb: Traceback for the exception, if any.
        """
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._transport.close()


if TYPE_CHECKING:
    from .session import StreamingSession

__all__ = ["SessionConnection", "Transport"]
