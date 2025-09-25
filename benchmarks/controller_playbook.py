"""Controller + playbook latency benchmark."""

from __future__ import annotations

import asyncio
import time

from penguiflow import (
    Headers,
    Message,
    Node,
    NodePolicy,
    call_playbook,
    create,
)


def build_playbook():
    async def retrieve(msg: Message, ctx) -> Message:
        results = [f"doc-{msg.payload}-{i}" for i in range(3)]
        return msg.model_copy(update={"payload": results})

    async def compress(msg: Message, ctx) -> Message:
        summary = ",".join(msg.payload)
        return msg.model_copy(update={"payload": summary})

    retrieve_node = Node(
        retrieve,
        name="pb_retrieve",
        policy=NodePolicy(validate="none"),
    )
    compress_node = Node(
        compress,
        name="pb_compress",
        policy=NodePolicy(validate="none"),
    )

    flow = create(
        retrieve_node.to(compress_node),
        compress_node.to(),
    )
    return flow, None


async def controller(msg: Message, ctx) -> Message:
    summary = await call_playbook(build_playbook, msg)
    return msg.model_copy(update={"payload": summary})


async def main(total_hops: int = 200) -> None:
    controller_node = Node(
        controller,
        name="controller",
        policy=NodePolicy(validate="none"),
    )
    flow = create(controller_node.to())
    flow.run()

    headers = Headers(tenant="bench")

    start = time.perf_counter()
    for i in range(total_hops):
        message = Message(payload=f"query-{i}", headers=headers)
        await flow.emit(message)
        await flow.fetch()
    elapsed = time.perf_counter() - start

    await flow.stop()

    rate = total_hops / elapsed if elapsed else float("inf")
    print(
        f"Controller/playbook benchmark: {total_hops} hops in {elapsed:.3f}s "
        f"-> {rate:.1f} hop/s"
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
