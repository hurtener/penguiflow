"""Helpers for extending a live ReactPlanner tool catalog.

The planner is usually initialized with a static catalog derived from nodes +
ModelRegistry. For Playground and other host environments, it's useful to
append platform-provided tools (e.g., tasks.*) without requiring downstream
build_planner() changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from ..catalog import NodeSpec
from . import prompts
from .react_runtime import _compute_visible_specs
from .tool_aliasing import build_aliased_tool_catalog
from .tool_search_cache import ToolSearchCache


def extend_tool_catalog(planner: Any, specs: Sequence[NodeSpec]) -> int:
    """Append tool specs to an already-initialized planner.

    Returns the number of newly-added tools (deduped by name).
    """

    planner = cast(Any, planner)

    planner_specs: list[NodeSpec] = list(
        getattr(planner, "_execution_specs", None) or getattr(planner, "_specs", None) or []
    )
    existing = {spec.name for spec in planner_specs}
    added = 0
    for spec in specs:
        if spec.name in existing:
            continue
        planner_specs.append(spec)
        existing.add(spec.name)
        added += 1

    if not added:
        return 0

    planner._execution_specs = planner_specs
    planner._execution_spec_by_name = {spec.name: spec for spec in planner_specs}

    tool_search_config = getattr(planner, "_tool_search_config", None)
    tool_search_cache = getattr(planner, "_tool_search_cache", None)
    if isinstance(tool_search_cache, ToolSearchCache):
        tool_search_cache.sync_tools(planner_specs)

    allowed_names = {spec.name for spec in planner_specs}
    planner._tool_visibility_allowed_names = allowed_names
    activated = getattr(planner, "_active_tool_names", None) or set()
    visible_specs = (
        _compute_visible_specs(
            planner_specs,
            allowed_names=allowed_names,
            activated_names=activated,
            config=tool_search_config if getattr(tool_search_config, "enabled", False) else None,
        )
        if planner_specs
        else []
    )

    planner._specs = list(visible_specs)
    spec_by_name, catalog_records, alias_to_real = build_aliased_tool_catalog(visible_specs)
    planner._spec_by_name = spec_by_name
    planner._catalog_records = catalog_records
    planner._tool_aliases = alias_to_real

    register_cb = getattr(planner, "_register_resource_callbacks", None)
    if callable(register_cb):
        register_cb()

    planning_hints = getattr(planner, "_planning_hints", None)
    hints_payload = None
    if planning_hints is not None and hasattr(planning_hints, "empty") and not planning_hints.empty():
        hints_payload = planning_hints.to_prompt_payload()

    system_prompt_extra = getattr(planner, "_system_prompt_extra", None)
    planner._system_prompt = prompts.build_system_prompt(
        catalog_records,
        extra=system_prompt_extra,
        planning_hints=hints_payload,
        tool_examples=getattr(planner, "_tool_examples_config", None),
    )
    return added


__all__ = ["extend_tool_catalog"]
