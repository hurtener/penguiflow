"""Showcase retries, timeouts, and middleware hooks."""

from __future__ import annotations

import asyncio

from penguiflow import (
    FlowEvent,
    Headers,
    Message,
    Node,
    NodePolicy,
    PenguiFlow,
    create,
)


def build_flow() -> PenguiFlow:
    attempts = {"count": 0}

    async def flaky(msg: Message, ctx) -> Message:
        attempts["count"] += 1
        attempt = attempts["count"]

        if attempt == 1:
            await asyncio.sleep(0.2)  # exceeds timeout -> triggers retry
        elif attempt == 2:
            raise RuntimeError("transient failure")

        return msg.model_copy(update={"payload": f"success on attempt {attempt}"})

    flaky_node = Node(
        flaky,
        name="flaky",
        policy=NodePolicy(
            validate="none",
            timeout_s=0.05,
            max_retries=2,
            backoff_base=0.05,
        ),
    )

    flow = create(flaky_node.to())

    async def middleware(event: FlowEvent) -> None:
        attempt = event.attempt
        latency = event.latency_ms
        print(f"mw:{event.event_type}:attempt={attempt} latency={latency}")

    flow.add_middleware(middleware)
    return flow


async def main() -> None:
    flow = build_flow()
    flow.run()

    message = Message(payload="ping", headers=Headers(tenant="acme"))
    await flow.emit(message)
    result = await flow.fetch()
    print(f"result payload: {result.payload}")

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
