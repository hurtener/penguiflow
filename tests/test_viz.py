"""Tests for visualization helpers."""

from __future__ import annotations

import pytest

from penguiflow import Node, NodePolicy, create
from penguiflow.viz import flow_to_dot, flow_to_mermaid


@pytest.mark.asyncio
async def test_visualizers_annotate_loops_and_boundaries() -> None:
    async def controller(msg: str, ctx) -> str:
        return msg

    async def worker(msg: str, ctx) -> str:
        return msg

    controller_node = Node(
        controller,
        name="controller",
        allow_cycle=True,
        policy=NodePolicy(validate="none"),
    )
    worker_node = Node(worker, name="worker", policy=NodePolicy(validate="none"))

    flow = create(controller_node.to(controller_node, worker_node))

    mermaid = flow_to_mermaid(flow)
    dot = flow_to_dot(flow)

    assert mermaid.startswith("graph TD")
    assert "controller" in mermaid
    assert "worker" in mermaid
    assert "|loop|" in mermaid
    assert "|ingress|" in mermaid
    assert "|egress|" in mermaid
    assert "classDef controller_loop" in mermaid

    assert dot.startswith("digraph PenguiFlow")
    assert "label=\"loop\"" in dot
    assert "label=\"ingress\"" in dot
    assert "label=\"egress\"" in dot
