"""Predicate routing example."""

from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, create, predicate_router


async def metrics_sink(msg: Message, ctx) -> str:
    return f"[metrics] {msg.payload}"


async def general_sink(msg: Message, ctx) -> str:
    return f"[general] {msg.payload}"


async def main() -> None:
    router = predicate_router(
        "router",
        lambda msg: ["metrics"] if msg.payload.startswith("metric") else ["general"],
    )
    metrics_node = Node(
        metrics_sink,
        name="metrics",
        policy=NodePolicy(validate="none"),
    )
    general_node = Node(
        general_sink,
        name="general",
        policy=NodePolicy(validate="none"),
    )

    flow = create(
        router.to(metrics_node, general_node),
        metrics_node.to(),
        general_node.to(),
    )
    flow.run()

    await flow.emit(Message(payload="metric-usage", headers=Headers(tenant="acme")))
    print(await flow.fetch())  # [metrics] metric-usage

    await flow.emit(Message(payload="ad-spend", headers=Headers(tenant="acme")))
    print(await flow.fetch())  # [general] ad-spend

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
