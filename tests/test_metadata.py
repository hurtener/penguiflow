import pytest

from penguiflow import Headers, Message, Node, create
from penguiflow.types import StreamChunk


def test_message_meta_defaults_isolation() -> None:
    headers = Headers(tenant="acme")
    msg1 = Message(payload="one", headers=headers)
    msg2 = Message(payload="two", headers=headers)

    msg1.meta["cost_ms"] = 42

    assert msg1.meta == {"cost_ms": 42}
    assert msg2.meta == {}


@pytest.mark.asyncio
async def test_metadata_roundtrip_and_update() -> None:
    async def annotate(message: Message, _ctx) -> Message:
        message.meta["retrieval_cost"] = 1.23
        return message

    async def summarize(message: Message, _ctx) -> Message:
        assert message.meta["retrieval_cost"] == 1.23
        new_meta = dict(message.meta)
        new_meta["summary_model"] = "penguin-1"
        return message.model_copy(
            update={
                "payload": f"summary::{message.payload}",
                "meta": new_meta,
            }
        )

    captured: list[Message] = []

    async def sink(message: Message, _ctx) -> Message:
        captured.append(message)
        return message

    annotate_node = Node(annotate, name="annotate")
    summarize_node = Node(summarize, name="summarize")
    sink_node = Node(sink, name="sink")

    flow = create(
        annotate_node.to(summarize_node),
        summarize_node.to(sink_node),
    )
    flow.run()

    headers = Headers(tenant="acme")
    message = Message(payload="docs", headers=headers)
    await flow.emit(message)

    result = await flow.fetch()
    await flow.stop()

    assert isinstance(result, Message)
    assert result.payload == "summary::docs"
    assert result.meta == {"retrieval_cost": 1.23, "summary_model": "penguin-1"}
    assert captured and captured[-1].meta == result.meta


@pytest.mark.asyncio
async def test_emit_chunk_preserves_parent_metadata() -> None:
    async def producer(message: Message, ctx) -> None:
        await ctx.emit_chunk(
            parent=message,
            text="chunk",
            done=True,
            meta={"token": 1},
        )

    seen: list[Message] = []

    async def sink(message: Message, _ctx) -> Message:
        seen.append(message)
        return message

    producer_node = Node(producer, name="producer")
    sink_node = Node(sink, name="sink")

    flow = create(producer_node.to(sink_node))
    flow.run()

    headers = Headers(tenant="demo")
    parent = Message(payload="seed", headers=headers, meta={"request_id": "abc"})
    await flow.emit(parent)

    result = await flow.fetch()
    await flow.stop()

    assert isinstance(result, Message)
    assert isinstance(result.payload, StreamChunk)
    assert result.meta == {"request_id": "abc"}
    assert result.payload.meta == {"token": 1}
    assert seen and seen[-1].meta == {"request_id": "abc"}
