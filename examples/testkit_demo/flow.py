import asyncio

from penguiflow import FinalAnswer, Headers, Message, Node, NodePolicy, create, testkit


async def enrich(message: Message, _ctx) -> Message:
    enriched = message.payload + " ⛸️"
    return message.model_copy(update={"payload": enriched})


async def finalize(message: Message, _ctx) -> Message:
    answer = FinalAnswer(text=message.payload.upper())
    return message.model_copy(update={"payload": answer})


async def main() -> None:
    enrich_node = Node(enrich, name="enrich", policy=NodePolicy(validate="none"))
    final_node = Node(finalize, name="final", policy=NodePolicy(validate="none"))
    flow = create(enrich_node.to(final_node), final_node.to())

    message = Message(payload="hello penguins", headers=Headers(tenant="demo"))

    result = await testkit.run_one(flow, message)
    testkit.assert_node_sequence(message.trace_id, ["enrich", "final"])

    final_answer = result.payload
    if isinstance(final_answer, FinalAnswer):
        print(f"Final answer: {final_answer.text}")
    else:
        print(f"Unexpected payload: {result}")


if __name__ == "__main__":
    asyncio.run(main())
