"""Visualization helpers for PenguiFlow graphs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .core import Endpoint
from .node import Node

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from .core import PenguiFlow

__all__ = ["flow_to_mermaid"]


def flow_to_mermaid(flow: PenguiFlow, *, direction: str = "TD") -> str:
    """Render the flow graph as a Mermaid diagram string.

    Parameters
    ----------
    flow:
        The `PenguiFlow` instance to visualize.
    direction:
        Mermaid graph direction ("TD", "LR", etc.). Defaults to top-down.
    """

    lines: list[str] = [f"graph {direction}"]
    nodes: set[object] = set()

    for floe in flow._floes:  # noqa: SLF001 - visualization accesses internals by design
        if floe.source is not None:
            nodes.add(floe.source)
        if floe.target is not None:
            nodes.add(floe.target)

    id_lookup: dict[object, str] = {}
    used_ids: set[str] = set()

    for entity in nodes:
        label = _display_label(entity)
        node_id = _unique_id(label, used_ids)
        used_ids.add(node_id)
        id_lookup[entity] = node_id
        lines.append(f"    {node_id}[\"{label}\"]")

    for floe in flow._floes:  # noqa: SLF001
        source = floe.source
        target = floe.target
        if source is None or target is None:
            continue
        src_id = id_lookup.get(source)
        tgt_id = id_lookup.get(target)
        if src_id is None or tgt_id is None:
            continue
        lines.append(f"    {src_id} --> {tgt_id}")

    return "\n".join(lines)


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
