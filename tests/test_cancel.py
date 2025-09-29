import asyncio
from collections import Counter
from typing import Any

import pytest

from penguiflow import Headers, Message, Node, NodePolicy, create
from penguiflow.metrics import FlowEvent


@pytest.mark.asyncio
async def test_cancel_trace_stops_inflight_run_without_affecting_others() -> None:
    release = asyncio.Event()
    slow_started = asyncio.Event()
    cancelled_flag = asyncio.Event()
    cancel_started = asyncio.Event()
    cancel_finished = asyncio.Event()
    processed: list[str] = []
    cancel_events: Counter[str] = Counter()
    payloads: dict[str, dict[str, object]] = {}

    async def slow(message: Message, _ctx) -> Message:
        if message.payload == "cancel-me":
            slow_started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled_flag.set()
                raise
        return message

    async def sink(message: Message, _ctx) -> str:
        processed.append(str(message.payload))
        return str(message.payload)

    slow_node = Node(slow, name="slow", policy=NodePolicy(validate="none"))
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))

    flow = create(slow_node.to(sink_node))

    async def recorder(event: FlowEvent) -> None:
        payload = event.to_payload()
        if event.event_type == "trace_cancel_start":
            cancel_started.set()
        if event.event_type == "trace_cancel_finish":
            cancel_finished.set()
        if event.event_type.startswith("trace_cancel"):
            cancel_events[event.event_type] += 1
            payloads[event.event_type] = payload

    flow.add_middleware(recorder)
    flow.run()

    headers = Headers(tenant="demo")
    cancel_msg = Message(payload="cancel-me", headers=headers)
    other_msg = Message(payload="other", headers=headers)

    await flow.emit(cancel_msg)
    await slow_started.wait()
    await flow.emit(other_msg)

    assert await flow.cancel(cancel_msg.trace_id) is True

    await cancel_started.wait()
    await cancelled_flag.wait()

    result = await flow.fetch()
    assert result == "other"

    await cancel_finished.wait()

    assert processed == ["other"]
    assert cancel_events == Counter(
        {"trace_cancel_start": 1, "trace_cancel_finish": 1}
    )

    start_payload = payloads["trace_cancel_start"]
    finish_payload = payloads["trace_cancel_finish"]

    assert start_payload["trace_pending"] >= 1
    assert start_payload["trace_inflight"] >= 1
    assert start_payload["trace_cancelled"] is True
    assert start_payload["q_depth_out"] >= 0
    assert start_payload["outgoing"] == 1

    assert finish_payload["trace_pending"] == 0
    assert finish_payload["trace_inflight"] == 0
    assert finish_payload["trace_cancelled"] is False

    assert await flow.cancel(cancel_msg.trace_id) is False

    await flow.stop()


@pytest.mark.asyncio
async def test_cancel_propagates_to_subflow() -> None:
    started = asyncio.Event()
    sub_cancelled = asyncio.Event()
    release = asyncio.Event()
    processed: list[str] = []

    async def sub_worker(message: Message, _ctx) -> Message:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            sub_cancelled.set()
            raise
        return message

    def build_subflow() -> tuple[Any, Any]:
        node = Node(sub_worker, name="sub", policy=NodePolicy(validate="none"))
        return create(node.to()), None

    async def controller(message: Message, ctx) -> Message:
        await ctx.call_playbook(build_subflow, message)
        return message

    async def sink(message: Message, _ctx) -> str:
        processed.append(str(message.payload))
        return str(message.payload)

    controller_node = Node(
        controller,
        name="controller",
        policy=NodePolicy(validate="none"),
    )
    sink_node = Node(
        sink,
        name="sink",
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(sink_node))
    flow.run()

    cancel_msg = Message(payload="cancel-me", headers=Headers(tenant="demo"))
    safe_msg = Message(payload="safe", headers=Headers(tenant="demo"))

    await flow.emit(cancel_msg)
    await started.wait()

    assert await flow.cancel(cancel_msg.trace_id) is True
    await sub_cancelled.wait()

    release.set()

    await flow.emit(safe_msg)
    result = await flow.fetch()
    assert result == "safe"

    assert processed == ["safe"]

    await flow.stop()


@pytest.mark.asyncio
async def test_cancel_unknown_trace_returns_false() -> None:
    async def passthrough(message: Message, _ctx) -> Message:
        return message

    node = Node(passthrough, name="pass", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    assert await flow.cancel("missing") is False

    await flow.stop()
