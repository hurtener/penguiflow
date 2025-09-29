from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest

from penguiflow import (
    DictRoutingPolicy,
    Headers,
    Message,
    Node,
    NodePolicy,
    RoutingRequest,
    create,
    predicate_router,
)
from penguiflow.core import Context


@pytest.mark.asyncio
async def test_policy_routes_by_tenant() -> None:
    async def left(msg: Message, ctx) -> str:
        return f"left:{msg.payload}"

    async def right(msg: Message, ctx) -> str:
        return f"right:{msg.payload}"

    def build_flow(policy: DictRoutingPolicy):
        router = predicate_router(
            "router",
            lambda msg: ["left", "right"],
            policy=policy,
        )
        left_node = Node(left, name="left", policy=NodePolicy(validate="none"))
        right_node = Node(right, name="right", policy=NodePolicy(validate="none"))
        flow = create(
            router.to(left_node, right_node),
            left_node.to(),
            right_node.to(),
        )
        flow.run()
        return flow

    policy_a = DictRoutingPolicy(
        {"acme": "left", "umbrella": "right"},
        default="right",
        key_getter=lambda request: request.message.headers.tenant,
    )
    policy_b = DictRoutingPolicy(
        {"acme": "right"},
        default="left",
        key_getter=lambda request: request.message.headers.tenant,
    )

    flow_a = build_flow(policy_a)
    flow_b = build_flow(policy_b)

    headers = Headers(tenant="acme")
    msg = Message(payload="hello", headers=headers)

    await flow_a.emit(msg)
    assert await flow_a.fetch() == "left:hello"

    await flow_b.emit(msg)
    assert await flow_b.fetch() == "right:hello"

    await flow_a.stop()
    await flow_b.stop()


@pytest.mark.asyncio
async def test_policy_can_drop_message() -> None:
    async def sink(msg: Message, ctx) -> str:
        return f"sink:{msg.payload}"

    class DropPolicy:
        async def select(self, request: RoutingRequest):
            if request.trace_id == "drop-me":
                return None
            return request.proposed

    router = predicate_router("router", lambda msg: ["sink"], policy=DropPolicy())
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))
    flow = create(
        router.to(sink_node),
        sink_node.to(),
    )
    flow.run()

    headers = Headers(tenant="acme")
    await flow.emit(Message(payload="keep", headers=headers))
    assert await flow.fetch() == "sink:keep"

    await flow.emit(
        Message(payload="gone", headers=headers, trace_id="drop-me")
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(flow.fetch(), timeout=0.05)

    await flow.stop()


def test_dict_policy_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="router", policy=NodePolicy(validate="none"))
    request = RoutingRequest(
        message=Message(
            payload="p",
            headers=Headers(tenant="acme"),
            trace_id="special",
        ),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="special",
    )

    payload = json.dumps({"special": ["router"]})
    policy = DictRoutingPolicy.from_json(payload)
    assert policy.select(request) == ["router"]

    monkeypatch.setenv("PF_POLICY", payload)
    policy_env = DictRoutingPolicy.from_env("PF_POLICY")
    assert policy_env.select(request) == ["router"]
