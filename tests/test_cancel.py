import asyncio
from collections import Counter

import pytest

from penguiflow import Headers, Message, Node, NodePolicy, create


@pytest.mark.asyncio
async def test_cancel_trace_stops_inflight_run_without_affecting_others() -> None:
    release = asyncio.Event()
    slow_started = asyncio.Event()
    cancelled_flag = asyncio.Event()
    cancel_started = asyncio.Event()
    cancel_finished = asyncio.Event()
    processed: list[str] = []
    cancel_events: Counter[str] = Counter()

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

    async def recorder(event: str, _payload: dict[str, object]) -> None:
        if event == "trace_cancel_start":
            cancel_started.set()
        if event == "trace_cancel_finish":
            cancel_finished.set()
        if event.startswith("trace_cancel"):
            cancel_events[event] += 1

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

    assert await flow.cancel(cancel_msg.trace_id) is False

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
