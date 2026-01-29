from __future__ import annotations

from typing import Any

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillSearchQuery, SkillSearchResponse


class SkillSearchArgs(SkillSearchQuery):
    pass


@tool(desc="Discover skills by capability and keywords.", loading_mode=ToolLoadingMode.ALWAYS)
async def skill_search(args: SkillSearchArgs, ctx: Any) -> SkillSearchResponse:
    planner = getattr(ctx, "_planner", None)
    provider = getattr(planner, "_skills_provider", None) if planner is not None else None
    if provider is None:
        raise RuntimeError("skill_search is not configured")
    tool_context = getattr(ctx, "tool_context", {}) if ctx is not None else {}
    all_tool_names = getattr(planner, "_execution_spec_by_name", {}).keys() if planner is not None else None
    allowed = getattr(planner, "_tool_visibility_allowed_names", None)
    response = await provider.search(
        args,
        tool_context=tool_context,
        all_tool_names=all_tool_names,
        allowed_tool_names=allowed,
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
