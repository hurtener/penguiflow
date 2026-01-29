from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from .models import StreamResponse


def encode_stream_response(event: StreamResponse) -> bytes:
    payload = event.model_dump(by_alias=True, exclude_none=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode()


async def stream_queue(
    queue,
    *,
    unsubscribe: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> AsyncIterator[bytes]:
    try:
        while True:
            event = await queue.get()
            yield encode_stream_response(event)
    finally:
        if unsubscribe is not None:
            await unsubscribe()
