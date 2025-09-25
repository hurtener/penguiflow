"""Retry/timeout overhead benchmark for PenguiFlow."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from penguiflow import Headers, Message, Node, NodePolicy, create


def build_flaky_node(failures: int) -> Node:
    attempts: defaultdict[str, int] = defaultdict(int)

    async def flaky(msg: Message, ctx) -> Message:
        key = msg.payload
        attempts[key] += 1
        if attempts[key] <= failures:
            await asyncio.sleep(0.01)
            raise RuntimeError("synthetic failure")
        await asyncio.sleep(0.005)
        return msg

    return Node(
        flaky,
        name="flaky",
        policy=NodePolicy(
            validate="none",
            timeout_s=0.05,
            max_retries=failures,
            backoff_base=0.01,
            backoff_mult=1.5,
        ),
    )


async def main(total_messages: int = 100, failures: int = 1) -> None:
    logging.getLogger("penguiflow.core").setLevel(logging.CRITICAL)
    node = build_flaky_node(failures)
    flow = create(node.to())
    flow.run()

    headers = Headers(tenant="bench")
    start = time.perf_counter()

    for i in range(total_messages):
        message = Message(payload=f"msg-{i}", headers=headers)
        await flow.emit(message)
        await flow.fetch()

    elapsed = time.perf_counter() - start
    await flow.stop()

    rate = total_messages / elapsed if elapsed else float("inf")
    print(
        f"Retry benchmark: {total_messages} msgs with {failures} retries in "
        f"{elapsed:.3f}s -> {rate:.1f} msg/s"
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
