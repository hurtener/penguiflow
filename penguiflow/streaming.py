"""Utilities for streaming chunk integrations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any

from .types import Message, StreamChunk

if TYPE_CHECKING:  # pragma: no cover - only used for typing
    from .core import PenguiFlow
    from .node import Node


def format_sse_event(
    chunk: StreamChunk,
    *,
    event_name: str | None = None,
    retry_ms: int | None = None,
) -> str:
    """Render a ``StreamChunk`` as an SSE event string."""

    event = event_name or ("done" if chunk.done else "chunk")
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"id: {chunk.seq}")
    lines.append(f"data: {chunk.text}")
    if chunk.meta:
        lines.append(f"data: {json.dumps(chunk.meta, ensure_ascii=False)}")
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    return "\n".join(lines) + "\n\n"


def chunk_to_ws_json(chunk: StreamChunk, *, extra: dict[str, Any] | None = None) -> str:
    """Serialize a ``StreamChunk`` as a JSON WebSocket payload."""

    payload = {
        "stream_id": chunk.stream_id,
        "seq": chunk.seq,
        "text": chunk.text,
        "done": chunk.done,
        "meta": chunk.meta,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


async def stream_flow(
    flow: PenguiFlow,
    parent_msg: Message,
    *,
    to: Node | Sequence[Node] | None = None,
    timeout: float | None = None,
    include_final: bool = False,
) -> AsyncIterator[Any]:
    """Yield streamed payloads from a running flow.

    This helper emits each ``StreamChunk`` produced downstream. When ``include_final``
    is ``True`` the first non-chunk payload encountered after a terminal chunk is also
    yielded before the generator stops. The caller is responsible for stopping the flow
    when finished.
    """

    await flow.emit(parent_msg, to=to)
    done_seen = False

    while True:
        fetch_coro = flow.fetch()
        result = (
            await asyncio.wait_for(fetch_coro, timeout)
            if timeout is not None
            else await fetch_coro
        )

        payload = result.payload if hasattr(result, "payload") else result

        if isinstance(payload, StreamChunk):
            yield payload
            if payload.done:
                done_seen = True
                if not include_final:
                    break
                continue
        else:
            if done_seen or include_final or not isinstance(payload, StreamChunk):
                yield payload
            break


EventAdapter = Callable[[Any], tuple[str, bool, dict[str, Any]]]


async def emit_stream_events(
    source: AsyncIterable[Any],
    ctx,
    parent_msg: Message,
    *,
    adapter: EventAdapter | None = None,
    to: Node | Sequence[Node] | None = None,
    final_meta: dict[str, Any] | None = None,
) -> None:
    """Bridge an async iterable of provider events into ``StreamChunk`` emissions."""

    def default_adapter(event: Any) -> tuple[str, bool, dict[str, Any]]:
        return str(event), False, {}

    adapter_fn = adapter or default_adapter
    done_seen = False

    async for event in source:
        text, done, meta = adapter_fn(event)
        await ctx.emit_chunk(
            parent=parent_msg,
            text=text,
            done=done,
            meta=meta,
            to=to,
        )
        if done:
            done_seen = True

    if not done_seen:
        await ctx.emit_chunk(
            parent=parent_msg,
            text="",
            done=True,
            meta=final_meta or {},
            to=to,
        )


__all__ = [
    "format_sse_event",
    "chunk_to_ws_json",
    "stream_flow",
    "emit_stream_events",
]
