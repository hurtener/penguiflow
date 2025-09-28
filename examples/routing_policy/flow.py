"""Demonstrates config-driven routing policies."""

from __future__ import annotations

import asyncio
from pathlib import Path

from penguiflow import (
    DictRoutingPolicy,
    Headers,
    Message,
    Node,
    NodePolicy,
    PenguiFlow,
    RoutingRequest,
    create,
    predicate_router,
)

POLICY_PATH = Path(__file__).with_name("policy.json")


def tenant_key(request: RoutingRequest) -> str:
    return request.message.headers.tenant


async def marketing(msg: Message, ctx) -> str:
    await asyncio.sleep(0.01)
    return f"marketing handled {msg.payload}"


async def support(msg: Message, ctx) -> str:
    await asyncio.sleep(0.01)
    return f"support handled {msg.payload}"


def build_flow() -> tuple[DictRoutingPolicy, PenguiFlow]:
    policy = DictRoutingPolicy.from_json_file(
        str(POLICY_PATH),
        default="support",
        key_getter=tenant_key,
    )

    router = predicate_router(
        "router",
        lambda msg: ["marketing", "support"],
        policy=policy,
    )
    marketing_node = Node(
        marketing,
        name="marketing",
        policy=NodePolicy(validate="none"),
    )
    support_node = Node(
        support,
        name="support",
        policy=NodePolicy(validate="none"),
    )
    flow = create(
        router.to(marketing_node, support_node),
        marketing_node.to(),
        support_node.to(),
    )
    flow.run()
    return policy, flow


async def main() -> None:
    policy, flow = build_flow()

    async def emit(payload: str, tenant: str) -> str:
        message = Message(payload=payload, headers=Headers(tenant=tenant))
        await flow.emit(message)
        return await flow.fetch()

    print(await emit("launch campaign", tenant="marketing"))
    print(await emit("reset password", tenant="support"))

    policy.update_mapping(
        {"marketing": "marketing", "support": "marketing", "vip": "support"}
    )

    print(await emit("premium issue", tenant="vip"))

    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
