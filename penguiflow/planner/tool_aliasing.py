"""Tool name aliasing for reserved PlannerAction opcodes.

In the unified action schema, ``PlannerAction.next_node`` is used both for:
- control opcodes (e.g. ``parallel``, ``final_response``)
- tool names from the catalog

To prevent ambiguous routing, we reserve a small set of opcode strings and
auto-alias any colliding tool names when building the LLM-visible catalog.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..catalog import NodeSpec
from .models import RESERVED_NEXT_NODES

_ALIAS_SUFFIX = "__tool"


def build_aliased_tool_catalog(
    specs: Sequence[NodeSpec],
    *,
    reserved: set[str] | frozenset[str] = RESERVED_NEXT_NODES,
) -> tuple[dict[str, NodeSpec], list[dict[str, Any]], dict[str, str]]:
    """Return (spec_by_name, catalog_records, alias_to_real) with collision handling."""

    used_names = {spec.name for spec in specs} | set(reserved)
    spec_by_name: dict[str, NodeSpec] = {}
    catalog_records: list[dict[str, Any]] = []
    alias_to_real: dict[str, str] = {}

    for spec in specs:
        name = spec.name
        alias = _alias_tool_name(name, used_names=used_names, reserved=reserved)
        used_names.add(alias)
        spec_by_name[alias] = spec
        record = spec.to_tool_record()
        record["name"] = alias
        catalog_records.append(record)
        if alias != name:
            alias_to_real[alias] = name

    return spec_by_name, catalog_records, alias_to_real


def _alias_tool_name(
    name: str,
    *,
    used_names: set[str],
    reserved: set[str] | frozenset[str],
) -> str:
    if name not in reserved:
        return name

    base = f"{name}{_ALIAS_SUFFIX}"
    candidate = base
    counter = 2
    while candidate in used_names or candidate in reserved:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def rewrite_action_node(
    node_name: str,
    *,
    alias_to_real: Mapping[str, str],
) -> str:
    """Resolve an LLM-selected tool alias to its real spec name, if applicable."""

    return alias_to_real.get(node_name, node_name)

