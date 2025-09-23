"""Integration tests for PenguiFlow core runtime (Phase 1)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from penguiflow.core import CycleError, PenguiFlow, create
from penguiflow.node import Node, NodePolicy


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
        async def __call__(self, event: str, payload: dict[str, object]) -> None:
            events.append((event, int(payload.get("attempt", -1))))

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
