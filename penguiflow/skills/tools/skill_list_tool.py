from __future__ import annotations

from typing import Any

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillListRequest, SkillListResponse


class SkillListArgs(SkillListRequest):
    pass


@tool(desc="List available skills with filters and paging.", loading_mode=ToolLoadingMode.ALWAYS)
async def skill_list(args: SkillListArgs, ctx: Any) -> SkillListResponse:
    planner = getattr(ctx, "_planner", None)
    provider = getattr(planner, "_skills_provider", None) if planner is not None else None
    if provider is None:
        raise RuntimeError("skill_list is not configured")
    tool_context = getattr(ctx, "tool_context", {}) if ctx is not None else {}
    all_tool_names = getattr(planner, "_execution_spec_by_name", {}).keys() if planner is not None else None
    allowed = getattr(planner, "_tool_visibility_allowed_names", None)
    response = await provider.list(
        args,
        tool_context=tool_context,
        all_tool_names=all_tool_names,
        allowed_tool_names=allowed,
    )
    if planner is not None:
        planner._emit_event(
            PlannerEvent(
                event_type="skill_list",
                ts=planner._time_source(),
                trajectory_step=0,
                extra={
                    "filters": {
                        "task_type": args.task_type,
                        "origin": args.origin,
                    },
                    "returned_count": len(response.skills),
                },
            )
        )
    return response


__all__ = ["SkillListArgs", "skill_list"]
