"""The ``skill_list`` planner tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillListRequest, SkillListResponse
from ._env import skill_tool_env

if TYPE_CHECKING:
    from ...planner.context import ToolContext


class SkillListArgs(SkillListRequest):
    """Arguments for ``skill_list``."""

    pass


@tool(
    desc="List available skills with filters and paging.",
    loading_mode=ToolLoadingMode.ALWAYS,
)
async def skill_list(
    args: SkillListArgs, ctx: ToolContext
) -> SkillListResponse:
    """List available skills for the planner."""
    env = skill_tool_env(ctx, "skill_list")
    planner = env.planner
    response = await env.provider.list(
        args,
        tool_context=env.tool_context,
        capability_context=env.capability_context,
    )
    planner.emit_event(
        PlannerEvent(
            event_type="skill_list",
            ts=planner.time_source(),
            trajectory_step=0,
            extra={
                "filters": {
                    "task_type": args.task_type,
                    "origin": args.origin,
                    "tags": list(args.tags),
                    "namespace": args.namespace,
                },
                "returned_count": len(response.skills),
            },
        )
    )
    return response


__all__ = ["SkillListArgs", "skill_list"]
