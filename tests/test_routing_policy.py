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


def test_routing_request_properties() -> None:
    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="my-router", policy=NodePolicy(validate="none"))
    proposed_node = Node(noop, name="target-node", policy=NodePolicy(validate="none"))

    request = RoutingRequest(
        message=Message(payload="p", headers=Headers(tenant="acme")),
        context=cast(Context, object()),
        node=node,
        proposed=(proposed_node,),
        trace_id="trace-123",
    )

    assert request.node_name == "my-router"
    assert request.proposed_names == ("target-node",)


def test_dict_policy_update_and_set_default() -> None:
    policy = DictRoutingPolicy({"key1": "value1"}, default="default-value")

    # Test initial state
    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="router", policy=NodePolicy(validate="none"))
    request = RoutingRequest(
        message=Message(payload="p", headers=Headers(tenant="acme"), trace_id="key1"),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="key1",
    )

    assert policy.select(request) == "value1"

    # Test update_mapping
    policy.update_mapping({"key1": "new-value"})
    assert policy.select(request) == "new-value"

    # Test set_default
    policy.set_default("new-default")
    request_unknown = RoutingRequest(
        message=Message(
            payload="p", headers=Headers(tenant="acme"), trace_id="unknown"
        ),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="unknown",
    )
    assert policy.select(request_unknown) == "new-default"


def test_dict_policy_key_getter_returns_none() -> None:
    policy = DictRoutingPolicy(
        {"key1": "value1"},
        default="default-value",
        key_getter=lambda req: None,
    )

    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="router", policy=NodePolicy(validate="none"))
    request = RoutingRequest(
        message=Message(payload="p", headers=Headers(tenant="acme")),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="any",
    )

    # When key_getter returns None, should use default
    assert policy.select(request) == "default-value"


def test_dict_policy_from_json_invalid() -> None:
    with pytest.raises(TypeError, match="must decode to a mapping"):
        DictRoutingPolicy.from_json('"not a mapping"')


def test_dict_policy_from_json_file(tmp_path) -> None:
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps({"key1": "value1"}))

    policy = DictRoutingPolicy.from_json_file(str(policy_file))

    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="router", policy=NodePolicy(validate="none"))
    request = RoutingRequest(
        message=Message(payload="p", headers=Headers(tenant="acme"), trace_id="key1"),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="key1",
    )

    assert policy.select(request) == "value1"


def test_dict_policy_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(KeyError, match="Environment variable 'MISSING_VAR' not set"):
        DictRoutingPolicy.from_env("MISSING_VAR")


def test_dict_policy_from_env_custom_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOM_POLICY", "key1=value1")

    def custom_loader(raw: str) -> dict:
        # Simple key=value parser
        return {raw.split("=")[0]: raw.split("=")[1]}

    policy = DictRoutingPolicy.from_env("CUSTOM_POLICY", loader=custom_loader)

    async def noop(msg: Message, ctx) -> None:  # pragma: no cover - helper
        return None

    node = Node(noop, name="router", policy=NodePolicy(validate="none"))
    request = RoutingRequest(
        message=Message(payload="p", headers=Headers(tenant="acme"), trace_id="key1"),
        context=cast(Context, object()),
        node=node,
        proposed=(node,),
        trace_id="key1",
    )

    assert policy.select(request) == "value1"


@pytest.mark.asyncio
async def test_policy_callable_without_select_method() -> None:
    """Test that PolicyCallable (function without .select) works."""
    async def left(msg: Message, ctx) -> str:
        return f"left:{msg.payload}"

    async def right(msg: Message, ctx) -> str:
        return f"right:{msg.payload}"

    # Use a plain function as policy (not a class with .select)
    def simple_policy(request: RoutingRequest):
        return ["left"] if request.trace_id == "use-left" else ["right"]

    router = predicate_router(
        "router",
        lambda msg: ["left", "right"],
        policy=simple_policy,
    )
    left_node = Node(left, name="left", policy=NodePolicy(validate="none"))
    right_node = Node(right, name="right", policy=NodePolicy(validate="none"))

    flow = create(
        router.to(left_node, right_node),
        left_node.to(),
        right_node.to(),
    )
    flow.run()

    headers = Headers(tenant="acme")
    msg = Message(payload="hello", headers=headers, trace_id="use-left")

    await flow.emit(msg)
    assert await flow.fetch() == "left:hello"

    await flow.stop()
