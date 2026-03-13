from __future__ import annotations

from typing import Any

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillSearchQuery, SkillSearchResponse
from ..provider import build_skill_capability_context


class SkillSearchArgs(SkillSearchQuery):
    pass


@tool(desc="Discover skills by capability and keywords.", loading_mode=ToolLoadingMode.ALWAYS)
async def skill_search(args: SkillSearchArgs, ctx: Any) -> SkillSearchResponse:
    planner = getattr(ctx, "_planner", None)
    provider = getattr(planner, "_skills_provider", None) if planner is not None else None
    if provider is None:
        raise RuntimeError("skill_search is not configured")
    tool_context = getattr(ctx, "tool_context", {}) if ctx is not None else {}
    execution_specs = getattr(planner, "_execution_spec_by_name", {}) if planner is not None else {}
    allowed = getattr(planner, "_tool_visibility_allowed_names", None)
    capability_context = build_skill_capability_context(
        execution_specs=execution_specs,
        all_tool_names=execution_specs.keys(),
        allowed_tool_names=allowed,
    )
    response = await provider.search(
        args,
        tool_context=tool_context,
        capability_context=capability_context,
    )
    if planner is not None:
        planner._emit_event(
            PlannerEvent(
                event_type="skill_search_query",
                ts=planner._time_source(),
                trajectory_step=0,
                extra={
                    "query": args.query,
                    "requested_search_type": args.search_type,
                    "effective_search_type": response.search_type,
                    "results_count": len(response.skills),
                },
            )
        )
    return response


__all__ = ["SkillSearchArgs", "skill_search"]
