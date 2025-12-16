"""Tests for penguiflow/streaming.py utilities."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.streaming import (
    chunk_to_ws_json,
    emit_stream_events,
    format_sse_event,
)
from penguiflow.types import StreamChunk

# ─── format_sse_event tests ──────────────────────────────────────────────────


def test_format_sse_event_basic():
    """Basic chunk should format as SSE event."""
    chunk = StreamChunk(stream_id="s1", seq=1, text="hello", done=False)
    result = format_sse_event(chunk)

    assert "event: chunk" in result
    assert "id: 1" in result
    assert "data: hello" in result
    assert result.endswith("\n\n")


def test_format_sse_event_done():
    """Done chunk should have event: done."""
    chunk = StreamChunk(stream_id="s1", seq=5, text="final", done=True)
    result = format_sse_event(chunk)

    assert "event: done" in result


def test_format_sse_event_custom_name():
    """Custom event name should be used."""
    chunk = StreamChunk(stream_id="s1", seq=1, text="test", done=False)
    result = format_sse_event(chunk, event_name="custom")

    assert "event: custom" in result


def test_format_sse_event_with_meta():
    """Chunk with meta should include JSON meta data line."""
    chunk = StreamChunk(stream_id="s1", seq=1, text="test", done=False, meta={"key": "value"})
    result = format_sse_event(chunk)

    assert 'data: {"key": "value"}' in result


def test_format_sse_event_with_retry():
    """retry_ms should add retry field."""
    chunk = StreamChunk(stream_id="s1", seq=1, text="test", done=False)
    result = format_sse_event(chunk, retry_ms=5000)

    assert "retry: 5000" in result


# ─── chunk_to_ws_json tests ──────────────────────────────────────────────────


def test_chunk_to_ws_json_basic():
    """Chunk should serialize to JSON."""
    chunk = StreamChunk(stream_id="ws1", seq=3, text="msg", done=False)
    result = chunk_to_ws_json(chunk)

    parsed = json.loads(result)
    assert parsed["stream_id"] == "ws1"
    assert parsed["seq"] == 3
    assert parsed["text"] == "msg"
    assert parsed["done"] is False
    assert parsed["meta"] == {}  # Default is empty dict, not None


def test_chunk_to_ws_json_with_meta():
    """Chunk with meta should include it in JSON."""
    chunk = StreamChunk(stream_id="ws1", seq=1, text="x", done=True, meta={"foo": "bar"})
    result = chunk_to_ws_json(chunk)

    parsed = json.loads(result)
    assert parsed["meta"] == {"foo": "bar"}


def test_chunk_to_ws_json_with_extra():
    """Extra data should be merged into JSON."""
    chunk = StreamChunk(stream_id="ws1", seq=1, text="x", done=False)
    result = chunk_to_ws_json(chunk, extra={"custom": 123})

    parsed = json.loads(result)
    assert parsed["custom"] == 123


# ─── emit_stream_events tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_stream_events_basic():
    """emit_stream_events should emit chunks for each event."""
    events = ["event1", "event2"]

    async def async_events():
        for e in events:
            yield e

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg)

    # Should be called 3 times: 2 events + 1 final done
    assert ctx.emit_chunk.call_count == 3

    # Last call should be done=True with empty text
    last_call = ctx.emit_chunk.call_args_list[-1]
    assert last_call.kwargs["done"] is True
    assert last_call.kwargs["text"] == ""


@pytest.mark.asyncio
async def test_emit_stream_events_with_adapter():
    """emit_stream_events should use custom adapter."""

    async def async_events():
        yield {"content": "test_content", "finished": False}

    def custom_adapter(event):
        return event["content"], event["finished"], {"adapted": True}

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg, adapter=custom_adapter)

    # Check first call used adapter
    first_call = ctx.emit_chunk.call_args_list[0]
    assert first_call.kwargs["text"] == "test_content"
    assert first_call.kwargs["meta"] == {"adapted": True}


@pytest.mark.asyncio
async def test_emit_stream_events_done_in_events():
    """emit_stream_events should not emit extra done if done seen in events."""

    async def async_events():
        yield "final"

    def adapter_with_done(event):
        return str(event), True, {}  # done=True

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg, adapter=adapter_with_done)

    # Only 1 call since done was seen
    assert ctx.emit_chunk.call_count == 1


@pytest.mark.asyncio
async def test_emit_stream_events_with_to():
    """emit_stream_events should pass 'to' parameter."""

    async def async_events():
        yield "event"

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()
    target_node = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg, to=target_node)

    # Check 'to' was passed
    first_call = ctx.emit_chunk.call_args_list[0]
    assert first_call.kwargs["to"] == target_node


@pytest.mark.asyncio
async def test_emit_stream_events_final_meta():
    """emit_stream_events should use final_meta on done chunk."""

    async def async_events():
        yield "event"

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg, final_meta={"final": True})

    # Last call should have final_meta
    last_call = ctx.emit_chunk.call_args_list[-1]
    assert last_call.kwargs["meta"] == {"final": True}


@pytest.mark.asyncio
async def test_emit_stream_events_default_adapter():
    """Default adapter should convert event to string."""

    async def async_events():
        yield 12345  # Non-string event

    ctx = MagicMock()
    ctx.emit_chunk = AsyncMock()
    parent_msg = MagicMock()

    await emit_stream_events(async_events(), ctx, parent_msg)

    # First call should have str(12345)
    first_call = ctx.emit_chunk.call_args_list[0]
    assert first_call.kwargs["text"] == "12345"
    assert first_call.kwargs["done"] is False
    assert first_call.kwargs["meta"] == {}
