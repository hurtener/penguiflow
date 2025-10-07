"""Typed quickstart example for PenguiFlow."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, NodePolicy, create


class TriageIn(BaseModel):
    text: str


class TriageOut(BaseModel):
    text: str
    topic: str


class RetrieveOut(BaseModel):
    topic: str
    docs: list[str]


class PackOut(BaseModel):
    prompt: str


async def triage(msg: TriageIn, ctx) -> TriageOut:
    topic = "metrics" if "metric" in msg.text else "general"
    return TriageOut(text=msg.text, topic=topic)


async def retrieve(msg: TriageOut, ctx) -> RetrieveOut:
    docs = [f"doc_{i}_{msg.topic}" for i in range(2)]
    return RetrieveOut(topic=msg.topic, docs=docs)


async def pack(msg: RetrieveOut, ctx) -> PackOut:
    prompt = f"[{msg.topic}] summarize {len(msg.docs)} docs"
    return PackOut(prompt=prompt)


async def main() -> None:
    triage_node = Node(triage, name="triage", policy=NodePolicy(validate="both"))
    retrieve_node = Node(retrieve, name="retrieve", policy=NodePolicy(validate="both"))
    pack_node = Node(pack, name="pack", policy=NodePolicy(validate="both"))

    registry = ModelRegistry()
    registry.register("triage", TriageIn, TriageOut)
    registry.register("retrieve", TriageOut, RetrieveOut)
    registry.register("pack", RetrieveOut, PackOut)

    flow = create(
        triage_node.to(retrieve_node),
        retrieve_node.to(pack_node),
    )
    flow.run(registry=registry)

    payload = TriageIn(text="show marketing metrics")

    await flow.emit(payload)
    result = await flow.fetch()
    print(result.prompt)

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover - example entrypoint
    asyncio.run(main())
