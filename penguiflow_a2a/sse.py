"""Server-sent events encoding of A2A stream responses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Callable, Coroutine

    from .models import StreamResponse


def encode_stream_response(event: StreamResponse) -> bytes:
    """Encode a stream response as an SSE event."""
    payload = event.model_dump(by_alias=True, exclude_none=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode()


async def stream_queue(
    queue: asyncio.Queue[StreamResponse],
    *,
    unsubscribe: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> AsyncIterator[bytes]:
    """Stream the queue's events as SSE, unsubscribing at the end."""
    try:
        while True:
            event = await queue.get()
            yield encode_stream_response(event)
    finally:
        if unsubscribe is not None:
            await unsubscribe()
