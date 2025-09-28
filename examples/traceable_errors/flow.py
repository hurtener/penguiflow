"""Example showcasing FlowError emission to the Rookery."""

from __future__ import annotations

import asyncio

from penguiflow import (
    FlowError,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
)


async def flaky_node(message: Message, _ctx) -> Message:
    raise RuntimeError("external service unavailable")


async def main() -> None:
    node = Node(
        flaky_node,
        name="flaky",  # keep a readable name for error payloads
        policy=NodePolicy(validate="none", max_retries=1, timeout_s=0.05),
    )

    flow = create(node.to(), emit_errors_to_rookery=True)
    flow.run()

    message = Message(payload="trigger", headers=Headers(tenant="demo"))

    await flow.emit(message)
    result = await flow.fetch()

    if isinstance(result, FlowError):
        payload = result.to_payload()
        print(
            "flow error captured:",
            payload["code"],
            payload.get("message"),
            "trace=", payload.get("trace_id"),
        )
    else:  # pragma: no cover - defensive
        print("unexpected result:", result)

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
