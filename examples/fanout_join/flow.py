"""Fan-out and join example with join_k."""

from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, create, join_k


async def fan(msg: Message, ctx) -> Message:
    return msg


async def work_a(msg: Message, ctx) -> Message:
    return msg.model_copy(update={"payload": msg.payload + "::A"})


async def work_b(msg: Message, ctx) -> Message:
    return msg.model_copy(update={"payload": msg.payload + "::B"})


async def summarize(batch: Message, ctx) -> str:
    return ",".join(batch.payload)


async def main() -> None:
    fan_node = Node(fan, name="fan", policy=NodePolicy(validate="none"))
    worker_a = Node(work_a, name="work_a", policy=NodePolicy(validate="none"))
    worker_b = Node(work_b, name="work_b", policy=NodePolicy(validate="none"))
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

    message = Message(payload="task", headers=Headers(tenant="acme"))
    await flow.emit(message)
    print(await flow.fetch())  # task::A,task::B

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
