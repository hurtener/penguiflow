"""Helpers for extending a live ReactPlanner tool catalog.

The planner is usually initialized with a static catalog derived from nodes +
ModelRegistry. For Playground and other host environments, it's useful to
append platform-provided tools (e.g., tasks.*) without requiring downstream
build_planner() changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..catalog import NodeSpec
from . import prompts


def extend_tool_catalog(planner: object, specs: Sequence[NodeSpec]) -> int:
    """Append tool specs to an already-initialized planner.

    Returns the number of newly-added tools (deduped by name).
    """

    planner_specs: list[NodeSpec] = list(getattr(planner, "_specs", []))
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

    planner._specs = planner_specs  # type: ignore[attr-defined]
    planner._spec_by_name = {spec.name: spec for spec in planner_specs}  # type: ignore[attr-defined]
    catalog_records = [spec.to_tool_record() for spec in planner_specs]
    planner._catalog_records = catalog_records  # type: ignore[attr-defined]

    register_cb = getattr(planner, "_register_resource_callbacks", None)
    if callable(register_cb):
        register_cb()

    planning_hints = getattr(planner, "_planning_hints", None)
    hints_payload = None
    if planning_hints is not None and hasattr(planning_hints, "empty") and not planning_hints.empty():
        hints_payload = planning_hints.to_prompt_payload()

    system_prompt_extra = getattr(planner, "_system_prompt_extra", None)
    planner._system_prompt = prompts.build_system_prompt(  # type: ignore[attr-defined]
        catalog_records,
        extra=system_prompt_extra,
        planning_hints=hints_payload,
    )
    return added


__all__ = ["extend_tool_catalog"]
