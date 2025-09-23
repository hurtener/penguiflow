"""Integration tests for PenguiFlow core runtime (Phase 1)."""

from __future__ import annotations

import asyncio

import pytest

from penguiflow.core import CycleError, PenguiFlow, create
from penguiflow.node import Node


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
