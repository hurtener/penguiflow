"""Tests for penguiflow.cli.playground_sse module."""

from __future__ import annotations

import asyncio

import pytest

from penguiflow.cli.playground_sse import (
    EventBroker,
    SSESentinel,
    format_sse,
    stream_queue,
)


class TestFormatSSE:
    def test_formats_event_correctly(self) -> None:
        result = format_sse("message", {"text": "hello"})
        assert result == b'event: message\ndata: {"text":"hello"}\n\n'

    def test_handles_unicode(self) -> None:
        result = format_sse("message", {"text": "héllo 世界"})
        assert "héllo 世界".encode() in result


class TestStreamQueue:
    @pytest.mark.asyncio
    async def test_yields_bytes_from_queue(self) -> None:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        await queue.put(b"frame1")
        await queue.put(b"frame2")
        await queue.put(SSESentinel)

        frames = []
        async for frame in stream_queue(queue):
            frames.append(frame)

        assert frames == [b"frame1", b"frame2"]

    @pytest.mark.asyncio
    async def test_calls_unsubscribe_on_completion(self) -> None:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        await queue.put(SSESentinel)

        unsubscribed = False

        async def unsubscribe() -> None:
            nonlocal unsubscribed
            unsubscribed = True

        async for _ in stream_queue(queue, unsubscribe=unsubscribe):
            pass

        assert unsubscribed

    @pytest.mark.asyncio
    async def test_handles_cancellation_gracefully(self) -> None:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        unsubscribed = False

        async def unsubscribe() -> None:
            nonlocal unsubscribed
            unsubscribed = True

        async def consume() -> list[bytes]:
            frames = []
            async for frame in stream_queue(queue, unsubscribe=unsubscribe):
                frames.append(frame)
            return frames

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # Let it start waiting
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Unsubscribe should still be called
        assert unsubscribed

    @pytest.mark.asyncio
    async def test_ignores_non_bytes_items(self) -> None:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        await queue.put(b"valid")
        await queue.put("not bytes")  # Should be skipped
        await queue.put(123)  # Should be skipped
        await queue.put(b"also valid")
        await queue.put(SSESentinel)

        frames = []
        async for frame in stream_queue(queue):
            frames.append(frame)

        assert frames == [b"valid", b"also valid"]


class TestEventBroker:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self) -> None:
        broker = EventBroker()
        queue, unsubscribe = await broker.subscribe("trace-1")

        broker.publish("trace-1", b"frame1")
        broker.publish("trace-1", b"frame2")

        assert await queue.get() == b"frame1"
        assert await queue.get() == b"frame2"

        await unsubscribe()

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self) -> None:
        broker = EventBroker()
        queue1, unsub1 = await broker.subscribe("trace-1")
        queue2, unsub2 = await broker.subscribe("trace-1")

        broker.publish("trace-1", b"broadcast")

        assert await queue1.get() == b"broadcast"
        assert await queue2.get() == b"broadcast"

        await unsub1()
        await unsub2()

    @pytest.mark.asyncio
    async def test_publish_to_different_traces(self) -> None:
        broker = EventBroker()
        queue1, unsub1 = await broker.subscribe("trace-1")
        queue2, unsub2 = await broker.subscribe("trace-2")

        broker.publish("trace-1", b"for-trace-1")
        broker.publish("trace-2", b"for-trace-2")

        assert await queue1.get() == b"for-trace-1"
        assert await queue2.get() == b"for-trace-2"

        await unsub1()
        await unsub2()

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self) -> None:
        broker = EventBroker()
        queue, unsubscribe = await broker.subscribe("trace-1")

        await unsubscribe()

        # After unsubscribe, publish should not add to queue
        broker.publish("trace-1", b"ignored")

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_unsubscribe_cleans_up_empty_trace(self) -> None:
        broker = EventBroker()
        queue, unsubscribe = await broker.subscribe("trace-1")

        await unsubscribe()

        # Trace should be removed from subscribers
        assert "trace-1" not in broker._subscribers

    @pytest.mark.asyncio
    async def test_close_sends_sentinel_to_all(self) -> None:
        broker = EventBroker()
        queue1, _ = await broker.subscribe("trace-1")
        queue2, _ = await broker.subscribe("trace-2")

        await broker.close()

        assert await queue1.get() is SSESentinel
        assert await queue2.get() is SSESentinel
        assert len(broker._subscribers) == 0

    @pytest.mark.asyncio
    async def test_publish_to_nonexistent_trace(self) -> None:
        broker = EventBroker()
        # Should not raise
        broker.publish("nonexistent", b"data")

    @pytest.mark.asyncio
    async def test_publish_handles_full_queue(self) -> None:
        broker = EventBroker()
        # Create a queue with max size 1
        queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=1)
        broker._subscribers["trace-1"].add(queue)

        # Fill the queue
        await queue.put(b"first")

        # This should not raise even though queue is full
        broker.publish("trace-1", b"second")

        # Only first item should be in queue
        assert await queue.get() == b"first"
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_close_handles_full_queue(self) -> None:
        """Test close() handles full queue gracefully (lines 80-81)."""
        broker = EventBroker()
        # Create a queue with max size 1
        queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=1)
        broker._subscribers["trace-1"].add(queue)

        # Fill the queue
        await queue.put(b"data")

        # close() should not raise even with full queue
        await broker.close()

        # Subscribers should be cleared
        assert len(broker._subscribers) == 0


