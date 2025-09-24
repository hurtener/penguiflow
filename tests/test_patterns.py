"""Tests for orchestration patterns."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from penguiflow.core import create
from penguiflow.node import Node, NodePolicy
from penguiflow.patterns import (
    join_k,
    map_concurrent,
    predicate_router,
    union_router,
)
from penguiflow.types import Headers, Message


@pytest.mark.asyncio
async def test_map_concurrent_respects_max_concurrency() -> None:
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(x: int) -> int:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return x * x

    results = await map_concurrent(range(4), worker, max_concurrency=2)
    assert results == [0, 1, 4, 9]
    assert peak <= 2


@pytest.mark.asyncio
async def test_predicate_router_routes_by_name() -> None:
    async def left(msg: Message, ctx) -> str:
        return f"left:{msg.payload}"

    async def right(msg: Message, ctx) -> str:
        return f"right:{msg.payload}"

    router = predicate_router(
        "router",
        lambda msg: ["left"] if msg.payload == "L" else ["right"],
    )
    left_node = Node(left, name="left", policy=NodePolicy(validate="none"))
    right_node = Node(right, name="right", policy=NodePolicy(validate="none"))

    flow = create(
        router.to(left_node, right_node),
        left_node.to(),
        right_node.to(),
    )
    flow.run()

    msg_left = Message(payload="L", headers=Headers(tenant="acme"))
    await flow.emit(msg_left)
    assert await flow.fetch() == "left:L"

    msg_right = Message(payload="R", headers=Headers(tenant="acme"))
    await flow.emit(msg_right)
    assert await flow.fetch() == "right:R"

    await flow.stop()


class Foo(BaseModel):
    kind: Literal["foo"] = Field(default="foo")
    value: int


class Bar(BaseModel):
    kind: Literal["bar"] = Field(default="bar")
    value: str


UnionModel = Annotated[Foo | Bar, Field(discriminator="kind")]


@pytest.mark.asyncio
async def test_union_router_routes_to_variant_node() -> None:
    async def handle_foo(msg: Foo, ctx) -> str:
        return f"foo:{msg.value}"

    async def handle_bar(msg: Bar, ctx) -> str:
        return f"bar:{msg.value}"

    router = union_router("router", UnionModel)
    foo_node = Node(handle_foo, name="foo", policy=NodePolicy(validate="none"))
    bar_node = Node(handle_bar, name="bar", policy=NodePolicy(validate="none"))

    flow = create(
        router.to(foo_node, bar_node),
        foo_node.to(),
        bar_node.to(),
    )
    flow.run()

    await flow.emit(Foo(value=3))
    assert await flow.fetch() == "foo:3"

    await flow.emit(Bar(value="hi"))
    assert await flow.fetch() == "bar:hi"

    await flow.stop()


@pytest.mark.asyncio
async def test_join_k_emits_after_k_messages() -> None:
    joined: list[Message] = []

    async def sink(msg: Message, ctx) -> Message:
        joined.append(msg)
        return msg

    join_node = join_k("join", 2)
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))

    flow = create(
        join_node.to(sink_node),
        sink_node.to(),
    )
    flow.run()

    headers = Headers(tenant="acme")
    msg1 = Message(payload="one", headers=headers, trace_id="trace")
    msg2 = Message(payload="two", headers=headers, trace_id="trace")

    await flow.emit(msg1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(flow.fetch(), timeout=0.05)

    await flow.emit(msg2)
    aggregated = await flow.fetch()
    assert isinstance(aggregated, Message)
    assert aggregated.payload == ["one", "two"]
    assert aggregated.trace_id == "trace"

    await flow.stop()

    assert len(joined) == 1
    assert joined[0].payload == ["one", "two"]
