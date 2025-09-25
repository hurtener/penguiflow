"""Tests for controller loop behaviour."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from penguiflow import (
    WM,
    FinalAnswer,
    Headers,
    Message,
    Node,
    NodePolicy,
    call_playbook,
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


@pytest.mark.asyncio
async def test_call_playbook_returns_payload_and_preserves_metadata() -> None:
    observed: dict[str, object] = {}

    async def retrieve(msg: Message, ctx) -> Message:
        observed["trace_id"] = msg.trace_id
        observed["headers"] = msg.headers
        return msg.model_copy(update={"payload": msg.payload.upper()})

    retrieve_node = Node(retrieve, name="retrieve", policy=NodePolicy(validate="none"))

    def playbook() -> tuple[Any, Any]:
        flow = create(retrieve_node.to())
        return flow, None

    parent = Message(payload="doc", headers=Headers(tenant="acme"))

    result = await call_playbook(playbook, parent)

    assert result == "DOC"
    assert observed["trace_id"] == parent.trace_id
    assert observed["headers"] == parent.headers


@pytest.mark.asyncio
async def test_call_playbook_cancellation_stops_subflow() -> None:
    started = asyncio.Event()
    blocker = asyncio.Event()
    flow_holder: list[Any] = []

    async def worker(msg: Message, ctx) -> Message:
        started.set()
        await blocker.wait()
        return msg

    def playbook() -> tuple[Any, Any]:
        node = Node(worker, name="worker", policy=NodePolicy(validate="none"))
        flow = create(node.to())
        flow_holder.append(flow)
        return flow, None

    parent = Message(payload="task", headers=Headers(tenant="acme"))

    task = asyncio.create_task(call_playbook(playbook, parent))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0)

    assert flow_holder, "playbook should have produced a flow"
    flow = flow_holder[0]
    assert not flow._running  # noqa: SLF001 test ensures cleanup
    assert not flow._tasks
