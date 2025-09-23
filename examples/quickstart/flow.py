"""Simple pass-through example for PenguiFlow."""

from __future__ import annotations

import asyncio

from penguiflow import Node, create


async def shout(msg: str, ctx) -> str:
    return msg.upper()


async def main() -> None:
    shout_node = Node(shout, name="shout")
    flow = create(shout_node.to())
    flow.run()

    await flow.emit("hello penguins")
    result = await flow.fetch()
    print(result)

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover - example entrypoint
    asyncio.run(main())
