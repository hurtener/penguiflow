"""Streaming support tests for PenguiFlow."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import pytest

from penguiflow import (
    Headers,
    Message,
    Node,
    NodePolicy,
    StreamChunk,
    chunk_to_ws_json,
    create,
    emit_stream_events,
    format_sse_event,
    stream_flow,
)


@pytest.mark.asyncio
async def test_stream_chunks_emit_in_order_and_reset_after_done() -> None:
    sequences: list[list[int]] = []
    completions: list[bool] = []
    buffers: dict[str, list[str]] = defaultdict(list)
    stream_sequences: dict[str, list[int]] = defaultdict(list)

    async def producer(message: Message, ctx) -> None:
        words = str(message.payload).split()
        for idx, word in enumerate(words):
            done = idx == len(words) - 1
            await ctx.emit_chunk(parent=message, text=word, done=done)

    async def sink(message: Message, _ctx) -> str | None:
        chunk = message.payload
        assert isinstance(chunk, StreamChunk)

        buffers[chunk.stream_id].append(chunk.text)
        stream_sequences[chunk.stream_id].append(chunk.seq)

        if chunk.done:
            parts = buffers.pop(chunk.stream_id)
            sequences.append(stream_sequences.pop(chunk.stream_id))
            completions.append(chunk.done)
            return " ".join(parts)
        return None

    producer_node = Node(producer, name="producer", policy=NodePolicy(validate="none"))
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))

    flow = create(producer_node.to(sink_node))
    flow.run()

    headers = Headers(tenant="demo")
    await flow.emit(Message(payload="one two three", headers=headers))
    result_first = await flow.fetch()
    assert result_first == "one two three"

    await flow.emit(Message(payload="hello penguins", headers=headers))
    result_second = await flow.fetch()
    assert result_second == "hello penguins"

    await flow.stop()

    assert sequences == [[0, 1, 2], [0, 1]]
    assert completions == [True, True]


@pytest.mark.asyncio
async def test_emit_chunk_respects_backpressure_when_rookery_full() -> None:
    second_emit_started = asyncio.Event()
    second_emit_finished = asyncio.Event()

    async def producer(message: Message, ctx) -> None:
        await ctx.emit_chunk(parent=message, text="chunk-0")
        second_emit_started.set()
        await ctx.emit_chunk(parent=message, text="chunk-1", done=True)
        second_emit_finished.set()

    async def passthrough(message: Message, ctx) -> Message:
        chunk = message.payload
        assert isinstance(chunk, StreamChunk)
        return message

    producer_node = Node(producer, name="producer", policy=NodePolicy(validate="none"))
    passthrough_node = Node(
        passthrough,
        name="passthrough",
        policy=NodePolicy(validate="none"),
    )

    flow = create(
        producer_node.to(passthrough_node),
        queue_maxsize=1,
    )
    flow.run()

    headers = Headers(tenant="demo")
    message = Message(payload="stream", headers=headers)

    emit_task = asyncio.create_task(flow.emit(message))
    await emit_task

    await second_emit_started.wait()
    await asyncio.sleep(0)
    assert not second_emit_finished.is_set(), "second chunk should block until fetch"

    first_chunk = await flow.fetch()
    assert isinstance(first_chunk, Message)
    assert isinstance(first_chunk.payload, StreamChunk)
    assert first_chunk.payload.seq == 0

    await second_emit_finished.wait()

    second_chunk = await flow.fetch()
    assert isinstance(second_chunk, Message)
    assert isinstance(second_chunk.payload, StreamChunk)
    assert second_chunk.payload.seq == 1
    assert second_chunk.payload.done is True

    await flow.stop()


@pytest.mark.asyncio
async def test_flow_emit_chunk_convenience_wrapper() -> None:
    collected: list[StreamChunk] = []

    async def sink(message: Message, _ctx) -> str | None:
        chunk = message.payload
        assert isinstance(chunk, StreamChunk)
        collected.append(chunk)
        if chunk.done:
            return chunk.text
        return None

    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))
    flow = create(sink_node.to())
    flow.run()

    parent = Message(payload="seed", headers=Headers(tenant="demo"))

    chunk1 = await flow.emit_chunk(parent=parent, text="part-1 ")
    chunk2 = await flow.emit_chunk(parent=parent, text="part-2", done=True)

    result = await flow.fetch()
    assert result == "part-2"

    chunk3 = await flow.emit_chunk(parent=parent, text="part-3", done=True)
    result_second = await flow.fetch()
    assert result_second == "part-3"

    await flow.stop()

    assert [c.seq for c in collected] == [0, 1, 0]
    assert chunk1.seq == 0
    assert chunk2.done is True
    assert chunk3.seq == 0


def test_format_sse_event_and_chunk_to_ws_json() -> None:
    chunk = StreamChunk(
        stream_id="abc",
        seq=2,
        text="token",
        done=True,
        meta={"foo": "bar"},
    )

    sse = format_sse_event(chunk, retry_ms=1500)
    assert "event: done" in sse
    assert "id: 2" in sse
    assert "data: token" in sse
    assert "retry: 1500" in sse
    assert json.dumps({"foo": "bar"}, ensure_ascii=False) in sse

    ws_payload = chunk_to_ws_json(chunk, extra={"channel": "chat"})
    parsed = json.loads(ws_payload)
    assert parsed["stream_id"] == "abc"
    assert parsed["seq"] == 2
    assert parsed["done"] is True
    assert parsed["meta"] == {"foo": "bar"}
    assert parsed["channel"] == "chat"


@pytest.mark.asyncio
async def test_emit_stream_events_with_adapter() -> None:
    outputs: list[StreamChunk] = []
    done_event = asyncio.Event()

    async def event_source() -> AsyncIterator[dict[str, Any]]:
        for idx, token in enumerate(["alpha", "beta"]):
            yield {"text": token, "done": idx == 1, "meta": {"idx": idx}}

    async def producer(message: Message, ctx) -> None:
        await emit_stream_events(
            event_source(),
            ctx,
            message,
            adapter=lambda event: (
                event["text"],
                event.get("done", False),
                event.get("meta", {}),
            ),
        )

    async def sink(message: Message, _ctx) -> None:
        payload = message.payload
        assert isinstance(payload, StreamChunk)
        outputs.append(payload)
        if payload.done:
            done_event.set()

    producer_node = Node(producer, name="producer", policy=NodePolicy(validate="none"))
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))
    flow = create(producer_node.to(sink_node))
    flow.run()

    await flow.emit(Message(payload="prompt", headers=Headers(tenant="demo")))

    await asyncio.wait_for(done_event.wait(), timeout=0.5)

    await flow.stop()

    assert [chunk.text for chunk in outputs] == ["alpha", "beta"]
    assert [chunk.meta for chunk in outputs] == [{"idx": 0}, {"idx": 1}]
    assert outputs[-1].done is True


@pytest.mark.asyncio
async def test_stream_flow_yields_chunks_and_final() -> None:
    collected: list[StreamChunk | str] = []

    async def producer(message: Message, ctx) -> None:
        words = str(message.payload).split()
        for idx, word in enumerate(words):
            # emit_chunk already sends to the default destination (Rookery in this case)
            await ctx.emit_chunk(
                parent=message,
                text=word,
                done=idx == len(words) - 1,
            )
        # After all chunks, emit a final non-chunk message
        if words:
            await ctx.emit(Message(
                payload="final",
                headers=message.headers,
                trace_id=message.trace_id,
            ))

    producer_node = Node(producer, name="producer", policy=NodePolicy(validate="none"))
    flow = create(producer_node.to())  # Producer goes directly to Rookery
    flow.run()

    parent = Message(payload="hello penguins", headers=Headers(tenant="demo"))

    async for item in stream_flow(flow, parent, include_final=True):
        collected.append(item)

    await flow.stop()

    assert [chunk.text for chunk in collected if isinstance(chunk, StreamChunk)] == [
        "hello",
        "penguins",
    ]
    assert collected[-1] == "final"
