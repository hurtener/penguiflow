"""Tests for visualization helpers."""

from __future__ import annotations

import pytest

from penguiflow import Node, NodePolicy, create
from penguiflow.viz import flow_to_mermaid


@pytest.mark.asyncio
async def test_flow_to_mermaid_renders_edges() -> None:
    async def fan(msg: str, ctx) -> str:
        return msg

    async def sink(msg: str, ctx) -> str:
        return msg

    fan_node = Node(fan, name="fan", policy=NodePolicy(validate="none"))
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))

    flow = create(fan_node.to(sink_node))

    mermaid = flow_to_mermaid(flow)

    assert mermaid.startswith("graph TD")
    assert "fan" in mermaid
    assert "sink" in mermaid
    assert "-->" in mermaid
