"""The ``skill_search`` planner tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillSearchQuery, SkillSearchResponse
from ._env import skill_tool_env

if TYPE_CHECKING:
    from ...planner.context import ToolContext


class SkillSearchArgs(SkillSearchQuery):
    """Arguments for ``skill_search``."""

    pass


@tool(
    desc="Discover skills by capability and keywords.",
    loading_mode=ToolLoadingMode.ALWAYS,
)
async def skill_search(
    args: SkillSearchArgs, ctx: ToolContext
) -> SkillSearchResponse:
    """Search skills for the planner."""
    env = skill_tool_env(ctx, "skill_search")
    planner = env.planner
    response = await env.provider.search(
        args,
        tool_context=env.tool_context,
        capability_context=env.capability_context,
    )
    planner.emit_event(
        PlannerEvent(
            event_type="skill_search_query",
            ts=planner.time_source(),
            trajectory_step=0,
            extra={
                "query": args.query,
                "requested_search_type": args.search_type,
                "effective_search_type": response.search_type,
                "results_count": len(response.skills),
                "tags": list(args.tags),
                "namespace": args.namespace,
            },
        )
    )
    return response


__all__ = ["SkillSearchArgs", "skill_search"]
