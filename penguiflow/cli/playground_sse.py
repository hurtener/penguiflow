"""SSE helpers for the playground backend."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

SSESentinel = object()


def format_sse(event: str, data: object) -> bytes:
    """Encode an SSE event block."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


async def stream_queue(
    queue: asyncio.Queue[bytes | object],
    unsubscribe: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE frames from an asyncio queue until a sentinel is received."""
    try:
        while True:
            try:
                # Use timeout to allow checking for cancellation periodically
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                if item is SSESentinel:
                    break
                if isinstance(item, bytes):
                    yield item
            except TimeoutError:
                # Continue waiting - this allows cancellation to be processed
                continue
    except asyncio.CancelledError:
        # Graceful shutdown - don't re-raise
        pass
    finally:
        if unsubscribe is not None:
            await unsubscribe()


class EventBroker:
    """Simple in-memory pub/sub used for planner event streaming."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[bytes | object]]] = defaultdict(set)

    async def subscribe(
        self, trace_id: str
    ) -> tuple[asyncio.Queue[bytes | object], Callable[[], Coroutine[Any, Any, None]]]:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._subscribers[trace_id].add(queue)

        async def _unsubscribe() -> None:
            subscribers = self._subscribers.get(trace_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(trace_id, None)

        return queue, _unsubscribe

    def publish(self, trace_id: str, frame: bytes) -> None:
        for queue in list(self._subscribers.get(trace_id, ())):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                continue

    async def close(self) -> None:
        """Signal all subscribers to exit and clear subscriptions."""

        for queues in list(self._subscribers.values()):
            for queue in list(queues):
                try:
                    queue.put_nowait(SSESentinel)
                except asyncio.QueueFull:
                    pass
        self._subscribers.clear()
