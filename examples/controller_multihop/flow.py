"""Controller loop example."""

from __future__ import annotations

import asyncio
import time

from penguiflow import WM, FinalAnswer, Headers, Message, Node, NodePolicy, create


async def controller(msg: Message, ctx) -> Message:
    wm = msg.payload
    assert isinstance(wm, WM)

    if wm.hops >= 3:
        final = FinalAnswer(text=f"answer after {wm.hops} hops: {wm.facts[-1]}")
        return msg.model_copy(update={"payload": final})

    token_cost = 5
    updated_wm = wm.model_copy(
        update={
            "facts": wm.facts + [f"fact-{wm.hops}"],
            "tokens_used": wm.tokens_used + token_cost,
        }
    )
    return msg.model_copy(update={"payload": updated_wm})


async def main() -> None:
    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )
    flow = create(controller_node.to(controller_node))
    flow.run()

    wm = WM(query="latest metrics", budget_hops=5, budget_tokens=12)
    message = Message(
        payload=wm,
        headers=Headers(tenant="acme"),
        deadline_s=time.time() + 5,
    )

    await flow.emit(message)
    final_msg = await flow.fetch()
    print(final_msg.payload.text)

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
