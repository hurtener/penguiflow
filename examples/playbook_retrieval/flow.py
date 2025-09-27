"""Playbook-driven retrieval and compression example."""

from __future__ import annotations

import asyncio
from typing import Any

from penguiflow import Headers, Message, Node, NodePolicy, create


def build_retrieval_playbook() -> tuple[Any, Any]:
    async def retrieve(msg: Message, ctx) -> Message:
        query = msg.payload["query"]
        docs = [f"{query}-doc-{i}" for i in range(1, 4)]
        return msg.model_copy(update={"payload": docs})

    async def rerank(msg: Message, ctx) -> Message:
        reranked = sorted(msg.payload, key=len)
        return msg.model_copy(update={"payload": reranked})

    async def compress(msg: Message, ctx) -> Message:
        summary = f"{msg.payload[0]} :: compressed"
        return msg.model_copy(update={"payload": summary})

    retrieve_node = Node(retrieve, name="retrieve", policy=NodePolicy(validate="none"))
    rerank_node = Node(rerank, name="rerank", policy=NodePolicy(validate="none"))
    compress_node = Node(compress, name="compress", policy=NodePolicy(validate="none"))

    flow = create(
        retrieve_node.to(rerank_node),
        rerank_node.to(compress_node),
        compress_node.to(),
    )
    return flow, None


async def controller(msg: Message, ctx) -> Message:
    request = msg.model_copy(update={"payload": {"query": msg.payload}})
    summary = await ctx.call_playbook(build_retrieval_playbook, request)
    return msg.model_copy(update={"payload": summary})


async def main() -> None:
    controller_node = Node(
        controller,
        name="controller",
        policy=NodePolicy(validate="none"),
    )
    flow = create(controller_node.to())
    flow.run()

    message = Message(payload="antarctic krill", headers=Headers(tenant="acme"))
    await flow.emit(message)
    final = await flow.fetch()
    print(final.payload)

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
