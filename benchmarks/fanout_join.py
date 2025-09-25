"""Fan-out/join throughput benchmark for PenguiFlow."""

from __future__ import annotations

import asyncio
import time

from penguiflow import Headers, Message, Node, NodePolicy, create, join_k


async def fan(msg: Message, ctx) -> Message:
    return msg


async def work_a(msg: Message, ctx) -> Message:
    return msg.model_copy(update={"payload": msg.payload + "::A"})


async def work_b(msg: Message, ctx) -> Message:
    return msg.model_copy(update={"payload": msg.payload + "::B"})


async def summarize(msg: Message, ctx) -> str:
    return ",".join(msg.payload)


async def main(total_messages: int = 1000) -> None:
    fan_node = Node(fan, name="fan", policy=NodePolicy(validate="none"))
    worker_a = Node(
        work_a,
        name="work_a",
        policy=NodePolicy(validate="none"),
    )
    worker_b = Node(
        work_b,
        name="work_b",
        policy=NodePolicy(validate="none"),
    )
    join_node = join_k("join", 2)
    summarize_node = Node(
        summarize,
        name="summarize",
        policy=NodePolicy(validate="none"),
    )

    flow = create(
        fan_node.to(worker_a, worker_b),
        worker_a.to(join_node),
        worker_b.to(join_node),
        join_node.to(summarize_node),
        summarize_node.to(),
    )
    flow.run()

    start = time.perf_counter()

    headers = Headers(tenant="bench")
    for i in range(total_messages):
        payload = f"msg-{i}"
        message = Message(payload=payload, headers=headers)
        await flow.emit(message)
        await flow.fetch()

    elapsed = time.perf_counter() - start
    await flow.stop()

    msgs_per_sec = total_messages / elapsed if elapsed > 0 else float("inf")
    print(
        f"Fanout/join benchmark: {total_messages} msgs in {elapsed:.3f}s "
        f"-> {msgs_per_sec:.1f} msg/s"
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
