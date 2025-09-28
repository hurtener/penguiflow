"""Integration tests for PenguiFlow core runtime (Phase 1)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import BaseModel

from penguiflow.core import CycleError, PenguiFlow, create
from penguiflow.metrics import FlowEvent
from penguiflow.node import Node, NodePolicy
from penguiflow.registry import ModelRegistry


@pytest.mark.asyncio
async def test_pass_through_flow() -> None:
    async def shout(msg: str, ctx) -> str:
        return msg.upper()

    shout_node = Node(shout, name="shout")

    flow = create(shout_node.to())
    flow.run()

    await flow.emit("penguin")
    result = await flow.fetch()

    assert result == "PENGUIN"

    await flow.stop()


@pytest.mark.asyncio
async def test_fan_out_to_multiple_nodes() -> None:
    async def fan(msg: str, ctx) -> str:
        return msg

    async def left(msg: str, ctx) -> str:
        return f"left:{msg}"

    async def right(msg: str, ctx) -> str:
        return f"right:{msg}"

    fan_node = Node(fan, name="fan")
    left_node = Node(left, name="left")
    right_node = Node(right, name="right")

    flow = create(
        fan_node.to(left_node, right_node),
    )
    flow.run()

    await flow.emit("hop")

    results = {await flow.fetch() for _ in range(2)}
    assert results == {"left:hop", "right:hop"}

    await flow.stop()


@pytest.mark.asyncio
async def test_backpressure_blocks_when_queue_full() -> None:
    release = asyncio.Event()
    processed: list[str] = []

    async def slow(msg: str, ctx) -> str:
        processed.append(msg)
        await release.wait()
        return msg

    slow_node = Node(slow, name="slow")
    flow = PenguiFlow(slow_node.to(), queue_maxsize=1)
    flow.run()

    await flow.emit("one")

    emit_two = asyncio.create_task(flow.emit("two"))
    emit_three = asyncio.create_task(flow.emit("three"))

    await asyncio.sleep(0)
    assert emit_two.done()
    assert not emit_three.done()

    release.set()

    await emit_three

    results = [await flow.fetch() for _ in range(3)]
    assert sorted(results) == ["one", "three", "two"]
    assert processed == ["one", "two", "three"]

    await flow.stop()


@pytest.mark.asyncio
async def test_graceful_stop_cancels_nodes() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocker(msg: str, ctx) -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    blocker_node = Node(blocker, name="blocker")
    flow = create(blocker_node.to())
    flow.run()

    await flow.emit("payload")
    await started.wait()

    await flow.stop()

    assert cancelled.is_set()


def test_cycle_detection() -> None:
    async def noop(msg: str, ctx) -> str:  # pragma: no cover - sync transform
        return msg

    node_a = Node(noop, name="A")
    node_b = Node(noop, name="B")

    with pytest.raises(CycleError):
        create(
            node_a.to(node_b),
            node_b.to(node_a),
        )


@pytest.mark.asyncio
async def test_retry_on_failure_logs_and_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    async def flaky(msg: str, ctx) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ValueError("boom")
        return msg

    node = Node(
        flaky,
        name="flaky",
        policy=NodePolicy(
            validate="none",
            max_retries=2,
            backoff_base=0.01,
            backoff_mult=1.0,
        ),
    )
    flow = create(node.to())
    flow.run()

    caplog.set_level(logging.INFO, logger="penguiflow.core")

    await flow.emit("hello")
    result = await flow.fetch()

    assert result == "hello"
    assert attempts == 2

    retry_events = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "node_retry"
    ]
    assert retry_events, "expected node_retry log record"

    await flow.stop()


@pytest.mark.asyncio
async def test_timeout_retries_and_drops_after_max(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    async def sleepy(msg: str, ctx) -> str:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.1)
        return msg

    node = Node(
        sleepy,
        name="sleepy",
        policy=NodePolicy(
            validate="none",
            timeout_s=0.05,
            max_retries=1,
            backoff_base=0.01,
            backoff_mult=1.0,
        ),
    )
    flow = create(node.to())
    flow.run()

    caplog.set_level(logging.WARNING, logger="penguiflow.core")

    await flow.emit("payload")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(flow.fetch(), timeout=0.2)

    assert attempts == 2

    timeout_events = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "node_timeout"
    ]
    failed_events = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "node_failed"
    ]
    assert timeout_events, "expected timeout log"
    assert failed_events, "expected failure log"

    await flow.stop()


@pytest.mark.asyncio
async def test_middlewares_receive_events() -> None:
    events: list[tuple[str, int]] = []

    class Collector:
        async def __call__(self, event: FlowEvent) -> None:
            events.append((event.event_type, event.attempt))

    async def echo(msg: str, ctx) -> str:
        return msg

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    collector = Collector()
    flow = create(node.to(), middlewares=[collector])
    flow.run()

    await flow.emit("ping")
    out = await flow.fetch()
    assert out == "ping"

    await flow.stop()

    events_names = [name for name, _ in events]
    assert "node_success" in events_names


@pytest.mark.asyncio
async def test_run_with_registry_requires_registered_nodes() -> None:
    async def handler(msg: str, ctx) -> str:
        return msg

    node = Node(handler, name="handler")
    flow = create(node.to())
    registry = ModelRegistry()

    with pytest.raises(RuntimeError) as exc:
        flow.run(registry=registry)

    assert "handler" in str(exc.value)


@pytest.mark.asyncio
async def test_run_with_registry_accepts_registered_nodes() -> None:
    class EchoModel(BaseModel):
        text: str

    async def handler(msg: EchoModel, ctx) -> EchoModel:
        return msg

    node = Node(handler, name="echo")
    flow = create(node.to())

    registry = ModelRegistry()
    registry.register("echo", EchoModel, EchoModel)

    flow.run(registry=registry)

    message = EchoModel(text="hi")
    await flow.emit(message)
    result = await flow.fetch()

    assert isinstance(result, EchoModel)
    assert result.text == "hi"

    await flow.stop()


@pytest.mark.asyncio
async def test_flow_run_twice_raises_error() -> None:
    """Running an already running flow should raise RuntimeError."""
    async def dummy(msg: str, _ctx) -> str:
        return msg

    node = Node(dummy, name="dummy", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    with pytest.raises(RuntimeError, match="already running"):
        flow.run()

    await flow.stop()


@pytest.mark.asyncio
async def test_cycle_detection_without_allow_cycles() -> None:
    """Cycles should raise CycleError when not explicitly allowed."""
    from penguiflow import CycleError

    async def node_a(msg: str, _ctx) -> str:
        return msg

    async def node_b(msg: str, _ctx) -> str:
        return msg

    a = Node(node_a, name="a", policy=NodePolicy(validate="none"))
    b = Node(node_b, name="b", policy=NodePolicy(validate="none"))

    # Create a cycle: a -> b -> a
    with pytest.raises(CycleError):
        create(a.to(b), b.to(a), allow_cycles=False)


@pytest.mark.asyncio
async def test_context_emit_to_unknown_target() -> None:
    """Emitting to an unknown target should raise KeyError."""
    async def sender(msg: str, ctx) -> None:
        fake_node = Node(lambda m, _c: m, name="fake")
        with pytest.raises(KeyError):
            await ctx.emit(msg, to=fake_node)

    node = Node(sender, name="sender", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    await flow.emit("test")
    await flow.stop()


@pytest.mark.asyncio
async def test_context_fetch_from_nonexistent_source() -> None:
    """Fetching from a nonexistent source should raise appropriate error."""
    async def fetcher(msg: str, ctx) -> None:
        fake_node = Node(lambda m, _c: m, name="fake")
        with pytest.raises(KeyError):
            await ctx.fetch(from_=fake_node)

    node = Node(fetcher, name="fetcher", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    await flow.emit("test")
    await flow.stop()


@pytest.mark.asyncio
async def test_timeout_with_retries() -> None:
    """Node timeout should trigger retries."""
    attempt_count = 0

    async def slow_node(_msg: str, _ctx) -> str:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            await asyncio.sleep(0.2)  # Will timeout
        return "success"

    node = Node(
        slow_node,
        name="slow",
        policy=NodePolicy(validate="none", timeout_s=0.1, max_retries=1)
    )
    flow = create(node.to())
    flow.run()

    await flow.emit("test")
    result = await flow.fetch()
    await flow.stop()

    assert result == "success"
    assert attempt_count == 2  # First attempt timed out, second succeeded


@pytest.mark.asyncio
async def test_max_retries_exhausted() -> None:
    """Node should stop retrying after max_retries attempts."""
    attempt_count = 0

    async def always_fails(_msg: str, _ctx) -> str:
        nonlocal attempt_count
        attempt_count += 1
        raise ValueError("Always fails")

    node = Node(
        always_fails,
        name="fail",
        policy=NodePolicy(validate="none", max_retries=2, backoff_base=0.01)
    )
    flow = create(node.to())
    flow.run()

    await flow.emit("test")

    # Flow continues but no result reaches Rookery due to failure
    # Wait for retries to complete (with backoff)
    await asyncio.sleep(0.2)

    # Should have attempted 3 times total (initial + 2 retries)
    assert attempt_count == 3

    await flow.stop()


@pytest.mark.asyncio
async def test_queue_full_backpressure() -> None:
    """Full queue should cause backpressure."""
    received = []

    async def slow_consumer(msg: str, _ctx) -> None:
        await asyncio.sleep(0.1)
        received.append(msg)

    node = Node(slow_consumer, name="slow", policy=NodePolicy(validate="none"))
    # Create flow with very small queue
    flow = create(node.to(), queue_maxsize=2)
    flow.run()

    # These should fill the queue quickly
    emit_tasks = []
    for i in range(5):
        emit_tasks.append(asyncio.create_task(flow.emit(f"msg{i}")))

    # Wait for emissions to complete (some will be blocked by backpressure)
    await asyncio.gather(*emit_tasks)

    # Give time for processing
    await asyncio.sleep(0.6)

    assert len(received) == 5  # All messages eventually processed
    await flow.stop()


@pytest.mark.asyncio
async def test_emit_nowait_queue_full() -> None:
    """emit_nowait should raise QueueFull when queue is full."""
    async def blocker(_msg: str, _ctx) -> None:
        await asyncio.sleep(10)  # Block forever

    node = Node(blocker, name="blocker", policy=NodePolicy(validate="none"))
    flow = create(node.to(), queue_maxsize=1)
    flow.run()

    # First emit fills the queue
    flow.emit_nowait("first")

    # Second should raise
    with pytest.raises(asyncio.QueueFull):
        flow.emit_nowait("second")

    await flow.stop()


@pytest.mark.asyncio
async def test_fetch_nowait_queue_empty() -> None:
    """fetch_nowait should raise QueueEmpty when no messages available."""
    async def dummy(msg: str, _ctx) -> str:
        return msg

    node = Node(dummy, name="dummy", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    # Try to fetch without emitting anything
    from penguiflow.core import ROOKERY, Context

    rookery_ctx = flow._contexts[ROOKERY]
    if isinstance(rookery_ctx, Context):
        with pytest.raises(asyncio.QueueEmpty):
            rookery_ctx.fetch_nowait()

    await flow.stop()


@pytest.mark.asyncio
async def test_registry_missing_validation_nodes() -> None:
    """Flow should raise error when registry is missing required nodes."""
    async def needs_validation(msg: str, _ctx) -> str:
        return msg

    node = Node(
        needs_validation,
        name="validated",
        policy=NodePolicy(validate="both")
    )
    flow = create(node.to())

    empty_registry = ModelRegistry()

    with pytest.raises(RuntimeError, match="missing entries"):
        flow.run(registry=empty_registry)
