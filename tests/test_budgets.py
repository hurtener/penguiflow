import time

import pytest

from penguiflow import (
    WM,
    FinalAnswer,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
)


@pytest.mark.asyncio
async def test_deadline_prevents_node_execution() -> None:
    calls = 0

    async def worker(msg: Message, ctx) -> Message:
        nonlocal calls
        calls += 1
        return msg

    node = Node(worker, name="worker", policy=NodePolicy(validate="none"))
    flow = create(node.to())
    flow.run()

    expired_message = Message(
        payload=WM(query="q"),
        headers=Headers(tenant="acme"),
        deadline_s=time.time() - 0.1,
    )

    await flow.emit(expired_message)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "Deadline exceeded"
    assert calls == 0

    await flow.stop()


@pytest.mark.asyncio
async def test_deadline_finalizes_when_first_node_has_successors() -> None:
    ingress_calls = 0

    async def ingress(msg: Message, ctx) -> Message:
        nonlocal ingress_calls
        ingress_calls += 1
        return msg

    async def downstream(msg: Message, ctx) -> Message:  # pragma: no cover - skipped
        raise AssertionError("downstream should not execute when deadline is expired")

    ingress_node = Node(ingress, name="ingress", policy=NodePolicy(validate="none"))
    downstream_node = Node(
        downstream, name="downstream", policy=NodePolicy(validate="none")
    )

    flow = create(
        ingress_node.to(downstream_node),
        downstream_node.to(),
    )
    flow.run()

    expired = Message(
        payload=WM(query="q"),
        headers=Headers(tenant="acme"),
        deadline_s=time.time() - 1,
    )

    await flow.emit(expired)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "Deadline exceeded"
    assert ingress_calls == 0

    await flow.stop()


@pytest.mark.asyncio
async def test_controller_enforces_token_budget() -> None:
    async def controller(msg: Message, ctx) -> Message:
        wm = msg.payload
        assert isinstance(wm, WM)
        updated = wm.model_copy(update={"tokens_used": wm.tokens_used + 5})
        return msg.model_copy(update={"payload": updated})

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node))
    flow.run()

    wm = WM(query="q", budget_hops=10, budget_tokens=12)
    message = Message(payload=wm, headers=Headers(tenant="acme"))

    await flow.emit(message)
    result = await flow.fetch()

    assert isinstance(result, Message)
    final = result.payload
    assert isinstance(final, FinalAnswer)
    assert final.text == "Token budget exhausted"

    await flow.stop()


@pytest.mark.asyncio
async def test_controller_allows_unbounded_when_budget_disabled() -> None:
    async def controller(msg: Message, ctx) -> Message:
        wm = msg.payload
        assert isinstance(wm, WM)
        updated = wm.model_copy(update={"hops": wm.hops + 1})
        return msg.model_copy(update={"payload": updated})

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )

    flow = create(controller_node.to(controller_node))
    flow.run()

    wm = WM(query="q", budget_hops=None)
    message = Message(payload=wm, headers=Headers(tenant="acme"))

    for expected_hops in range(1, 6):
        await flow.emit(message)
        message = await flow.fetch()
        assert isinstance(message, Message)
        payload = message.payload
        assert isinstance(payload, WM)
        assert payload.hops == expected_hops

    await flow.stop()
