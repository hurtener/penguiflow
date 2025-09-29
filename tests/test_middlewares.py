"""Tests for middleware functionality."""

from __future__ import annotations

import asyncio

import pytest

from penguiflow import Node, NodePolicy, create
from penguiflow.metrics import FlowEvent


class TrackingMiddleware:
    """Test middleware that tracks all events."""

    def __init__(self) -> None:
        self.events: list[FlowEvent] = []
        self.call_count = 0

    async def __call__(self, event: FlowEvent) -> None:
        self.call_count += 1
        self.events.append(event)


class ErrorMiddleware:
    """Test middleware that raises errors."""

    def __init__(self, error_on_event: str | None = None) -> None:
        self.error_on_event = error_on_event

    async def __call__(self, event: FlowEvent) -> None:
        if self.error_on_event and event.event_type == self.error_on_event:
            raise RuntimeError(f"Intentional error on {event.event_type}")


class SlowMiddleware:
    """Test middleware that introduces delays."""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay = delay

    async def __call__(self, _event: FlowEvent) -> None:
        await asyncio.sleep(self.delay)


@pytest.mark.asyncio
async def test_middleware_receives_all_node_events() -> None:
    """Middleware should receive start, success, and other node events."""
    tracker = TrackingMiddleware()

    async def simple_node(msg: str, _ctx) -> str:
        return msg.upper()

    node = Node(simple_node, name="simple", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.add_middleware(tracker)
    flow.run()

    await flow.emit("test")
    result = await flow.fetch()
    await flow.stop()

    assert result == "TEST"
    assert tracker.call_count > 0

    # Check that we received expected events
    event_types = {evt.event_type for evt in tracker.events}
    assert "node_start" in event_types
    assert "node_success" in event_types

    # Verify payload structure
    for event in tracker.events:
        if event.event_type in {"node_start", "node_success"}:
            payload = event.to_payload()
            assert payload["node_name"] == "simple"
            trace_id = payload.get("trace_id")
            if trace_id is not None:
                assert isinstance(trace_id, str)
            assert payload["q_depth_total"] == (
                payload["q_depth_in"] + payload["q_depth_out"]
            )


@pytest.mark.asyncio
async def test_middleware_exception_does_not_crash_flow() -> None:
    """Flow should continue even if middleware raises an exception."""
    error_middleware = ErrorMiddleware(error_on_event="node_start")

    async def worker(msg: str, _ctx) -> str:
        return msg + "_processed"

    node = Node(worker, name="worker", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.add_middleware(error_middleware)
    flow.run()

    # Flow should still work despite middleware error
    await flow.emit("test")
    result = await flow.fetch()
    await flow.stop()

    assert result == "test_processed"


@pytest.mark.asyncio
async def test_multiple_middleware_chain_execution() -> None:
    """Multiple middleware should be called in order."""
    tracker1 = TrackingMiddleware()
    tracker2 = TrackingMiddleware()

    async def processor(msg: str, _ctx) -> str:
        return msg

    node = Node(processor, name="proc", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.add_middleware(tracker1)
    flow.add_middleware(tracker2)
    flow.run()

    await flow.emit("data")
    await flow.fetch()
    await flow.stop()

    # Both middleware should have received events
    assert tracker1.call_count > 0
    assert tracker2.call_count > 0
    assert tracker1.call_count == tracker2.call_count


@pytest.mark.asyncio
async def test_middleware_with_retry_events() -> None:
    """Middleware should receive retry events when node fails and retries."""
    tracker = TrackingMiddleware()
    attempt_count = 0

    async def flaky_node(_msg: str, _ctx) -> str:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            raise ValueError("First attempt fails")
        return "success"

    node = Node(
        flaky_node,
        name="flaky",
        policy=NodePolicy(validate="none", max_retries=2)
    )
    flow = create(node.to())
    flow.add_middleware(tracker)
    flow.run()

    await flow.emit("test")
    result = await flow.fetch()
    await flow.stop()

    assert result == "success"

    # Check for retry events
    event_types = [evt.event_type for evt in tracker.events]
    assert "node_error" in event_types
    assert "node_retry" in event_types
    assert "node_success" in event_types


@pytest.mark.asyncio
async def test_middleware_performance_impact_minimal() -> None:
    """Slow middleware should not block node execution significantly."""
    slow_middleware = SlowMiddleware(delay=0.05)

    async def fast_node(msg: str, _ctx) -> str:
        return msg

    node = Node(fast_node, name="fast", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.add_middleware(slow_middleware)
    flow.run()

    start = asyncio.get_event_loop().time()
    await flow.emit("test")
    result = await flow.fetch()
    await flow.stop()
    duration = asyncio.get_event_loop().time() - start

    assert result == "test"
    # Even with slow middleware, flow should complete reasonably fast
    # (middleware is async so shouldn't block main execution)
    assert duration < 1.0  # Should be well under 1 second
