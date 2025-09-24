"""Tests for controller loop behaviour."""

from __future__ import annotations

import asyncio
import time

import pytest

from penguiflow import (
    WM,
    FinalAnswer,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
)


@pytest.mark.asyncio
async def test_controller_loops_until_final_answer() -> None:
    async def controller(msg: Message, ctx) -> Message:
        wm = msg.payload
        if isinstance(wm, WM) and wm.hops >= 2:
            final = FinalAnswer(text=f"done@{wm.hops}")
            return msg.model_copy(update={"payload": final})
        return msg

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node))
    flow.run()

    wm = WM(query="q", budget_hops=4)
    message = Message(payload=wm, headers=Headers(tenant="acme"))

    await flow.emit(message)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "done@2"

    await flow.stop()


@pytest.mark.asyncio
async def test_controller_enforces_hop_budget() -> None:
    async def controller(msg: Message, ctx) -> Message:
        return msg

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node))
    flow.run()

    wm = WM(query="q", budget_hops=1)
    message = Message(payload=wm, headers=Headers(tenant="acme"))

    await flow.emit(message)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "Hop budget exhausted"

    await flow.stop()


@pytest.mark.asyncio
async def test_controller_enforces_deadline() -> None:
    async def controller(msg: Message, ctx) -> Message:
        await asyncio.sleep(0.05)
        return msg

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node))
    flow.run()

    deadline = time.time() + 0.02
    wm = WM(query="q")
    message = Message(payload=wm, headers=Headers(tenant="acme"), deadline_s=deadline)

    await flow.emit(message)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "Deadline exceeded"

    await flow.stop()
