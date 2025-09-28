"""Metadata propagation demo for PenguiFlow."""

from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, create


async def annotate_cost(message: Message, _ctx) -> Message:
    """Add retrieval cost metadata without mutating payload."""

    message.meta["retrieval_cost_ms"] = 87
    return message


async def summarize(message: Message, _ctx) -> Message:
    """Read metadata and add summarizer details."""

    cost = message.meta.get("retrieval_cost_ms", 0)
    new_meta = dict(message.meta)
    new_meta["summary_model"] = "penguin-x1"
    new_meta["summary_tokens"] = 128
    return message.model_copy(
        update={
            "payload": f"Summary(cost={cost}ms): {message.payload}",
            "meta": new_meta,
        }
    )


async def sink(message: Message, _ctx) -> Message:
    """Forward the final message to Rookery."""

    return message


async def main() -> None:
    annotate_node = Node(annotate_cost, name="annotate_cost")
    summarize_node = Node(summarize, name="summarize")
    sink_node = Node(sink, name="sink")

    flow = create(
        annotate_node.to(summarize_node),
        summarize_node.to(sink_node),
    )
    flow.run()

    headers = Headers(tenant="acme", topic="metadata-demo")
    message = Message(payload="documents about penguins", headers=headers)

    await flow.emit(message)
    result = await flow.fetch()
    await flow.stop()

    print("Payload:", result.payload)
    print("Metadata:", result.meta)


if __name__ == "__main__":
    asyncio.run(main())
