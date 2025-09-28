"""Visualization helpers for PenguiFlow graphs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .core import Endpoint
from .node import Node

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from .core import PenguiFlow

__all__ = ["flow_to_mermaid", "flow_to_dot"]


@dataclass
class _VisualNode:
    identifier: str
    label: str
    classes: list[str]


@dataclass
class _VisualEdge:
    source: str
    target: str
    label: str | None


def flow_to_mermaid(flow: PenguiFlow, *, direction: str = "TD") -> str:
    """Render the flow graph as a Mermaid diagram string.

    Parameters
    ----------
    flow:
        The :class:`PenguiFlow` instance to visualize.
    direction:
        Mermaid graph direction (``"TD"``, ``"LR"``, etc.). Defaults to top-down.
    """

    nodes, edges = _collect_graph(flow)

    lines: list[str] = [f"graph {direction}"]
    class_defs = {
        "endpoint": "fill:#e0f2fe,stroke:#0369a1,stroke-width:1px",
        "controller_loop": "fill:#fef3c7,stroke:#b45309,stroke-width:1px",
    }

    used_definitions: set[str] = set()

    for node in nodes:
        label = _escape_label(node.label)
        lines.append(f"    {node.identifier}[\"{label}\"]")
        for class_name in node.classes:
            used_definitions.add(class_name)

    for class_name in sorted(used_definitions):
        style = class_defs.get(class_name)
        if style:
            lines.append(f"    classDef {class_name} {style}")

    for node in nodes:
        if node.classes:
            classes = " ".join(node.classes)
            lines.append(f"    class {node.identifier} {classes}")

    for edge in edges:
        label = f"|{edge.label}|" if edge.label else ""
        lines.append(f"    {edge.source} -->{label} {edge.target}")

    return "\n".join(lines)


def flow_to_dot(flow: PenguiFlow, *, rankdir: str = "TB") -> str:
    """Render the flow graph as a Graphviz DOT string.

    Parameters
    ----------
    flow:
        The :class:`PenguiFlow` instance to visualize.
    rankdir:
        Graph orientation (``"TB"``, ``"LR"``, etc.). Defaults to top-bottom.
    """

    nodes, edges = _collect_graph(flow)

    lines: list[str] = ["digraph PenguiFlow {", f"    rankdir={rankdir}"]
    lines.append("    node [shape=box, style=rounded]")

    for node in nodes:
        attributes: list[str] = [f'label="{node.label}"']
        if "endpoint" in node.classes:
            attributes.append('shape=oval')
            attributes.append('style="filled"')
            attributes.append('fillcolor="#e0f2fe"')
        elif "controller_loop" in node.classes:
            attributes.append('style="rounded,filled"')
            attributes.append('fillcolor="#fef3c7"')
        attr_str = ", ".join(attributes)
        lines.append(f"    {node.identifier} [{attr_str}]")

    for edge in edges:
        if edge.label:
            edge_label = _escape_label(edge.label)
            lines.append(
                f"    {edge.source} -> {edge.target} [label=\"{edge_label}\"]"
            )
        else:
            lines.append(f"    {edge.source} -> {edge.target}")

    lines.append("}")
    return "\n".join(lines)


def _collect_graph(flow: PenguiFlow) -> tuple[list[_VisualNode], list[_VisualEdge]]:
    nodes: dict[object, _VisualNode] = {}
    edges: list[_VisualEdge] = []
    used_ids: set[str] = set()
    loop_sources: set[object] = set()

    def ensure_node(entity: object) -> _VisualNode:
        node = nodes.get(entity)
        if node is not None:
            return node
        label = _display_label(entity)
        identifier = _unique_id(label, used_ids)
        used_ids.add(identifier)
        classes: list[str] = []
        if isinstance(entity, Endpoint):
            classes.append("endpoint")
        if isinstance(entity, Node) and entity.allow_cycle:
            classes.append("controller_loop")
        node = _VisualNode(identifier=identifier, label=label, classes=classes)
        nodes[entity] = node
        return node

    for floe in flow._floes:  # noqa: SLF001 - visualization inspects internals
        source = floe.source
        target = floe.target
        if source is None or target is None:
            continue
        src_node = ensure_node(source)
        tgt_node = ensure_node(target)
        if source is target:
            loop_sources.add(source)
            label = "loop"
        elif isinstance(source, Endpoint):
            label = "ingress"
        elif isinstance(target, Endpoint):
            label = "egress"
        else:
            label = None
        edges.append(_VisualEdge(src_node.identifier, tgt_node.identifier, label))

    if loop_sources:
        for entity, node in nodes.items():
            if entity in loop_sources and "controller_loop" not in node.classes:
                node.classes.append("controller_loop")

    return list(nodes.values()), edges


def _display_label(entity: object) -> str:
    if isinstance(entity, Node):
        return entity.name or entity.node_id
    if isinstance(entity, Endpoint):
        return entity.name
    return str(entity)


def _unique_id(label: str, used: set[str]) -> str:
    base = re.sub(r"[^0-9A-Za-z_]", "_", label) or "node"
    candidate = base
    index = 1
    while candidate in used:
        index += 1
        candidate = f"{base}_{index}"
    return candidate


def _escape_label(label: str) -> str:
    return label.replace("\"", "\\\"")

