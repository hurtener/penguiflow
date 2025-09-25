"""Demonstrate the map_concurrent helper inside a node."""

from __future__ import annotations

import asyncio
from typing import Any

from penguiflow import Headers, Message, Node, NodePolicy, create, map_concurrent


async def seed(msg: Message, ctx) -> Message:
    """Attach a batch of document ids to the message payload."""

    docs = [f"doc-{i}" for i in range(1, 6)]
    return msg.model_copy(update={"payload": {"query": msg.payload, "docs": docs}})


async def score(msg: Message, ctx) -> Message:
    """Score each document concurrently with a bounded semaphore."""

    docs = msg.payload["docs"]

    async def worker(doc_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"doc_id": doc_id, "score": len(doc_id) / 10}

    scored = await map_concurrent(docs, worker, max_concurrency=2)
    return msg.model_copy(update={"payload": {**msg.payload, "scores": scored}})


async def summarize(msg: Message, ctx) -> str:
    top = max(msg.payload["scores"], key=lambda item: item["score"])
    return f"top doc: {top['doc_id']} score={top['score']:.2f}"


async def main() -> None:
    seed_node = Node(seed, name="seed", policy=NodePolicy(validate="none"))
    score_node = Node(score, name="score", policy=NodePolicy(validate="none"))
    summary_node = Node(summarize, name="summary", policy=NodePolicy(validate="none"))

    flow = create(
        seed_node.to(score_node),
        score_node.to(summary_node),
        summary_node.to(),
    )
    flow.run()

    message = Message(payload="antarctic krill", headers=Headers(tenant="acme"))
    await flow.emit(message)
    print(await flow.fetch())

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
