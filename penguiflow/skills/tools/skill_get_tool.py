"""The ``skill_get`` planner tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillResultDetailed
from ._env import skill_tool_env

if TYPE_CHECKING:
    from ...planner.context import ToolContext


class SkillGetArgs(BaseModel):
    """Arguments for ``skill_get``."""

    names: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    format: Literal["raw", "injection"] = "injection"
    max_tokens: int = Field(default=1500, ge=200, le=6000)


class SkillGetResponse(BaseModel):
    """The requested skills and their formatted context."""

    skills: list[SkillResultDetailed]
    formatted_context: str


@tool(
    desc="Fetch skills by name for full content.",
    loading_mode=ToolLoadingMode.ALWAYS,
)
async def skill_get(args: SkillGetArgs, ctx: ToolContext) -> SkillGetResponse:
    """Fetch skills by name for the planner."""
    env = skill_tool_env(ctx, "skill_get")
    planner = env.planner
    skills = await env.provider.get_by_name(
        list(args.names),
        tool_context=env.tool_context,
        capability_context=env.capability_context,
    )
    formatted = ""
    final_tokens = 0
    if args.format == "injection":
        (
            formatted,
            _raw_tokens,
            final_tokens,
            _summarized,
        ) = await env.provider.format_for_injection(
            skills, max_tokens=args.max_tokens
        )
    planner.emit_event(
        PlannerEvent(
            event_type="skill_get",
            ts=planner.time_source(),
            trajectory_step=0,
            extra={
                "names": list(args.names),
                "returned_count": len(skills),
                "max_tokens": args.max_tokens,
                "final_tokens_est": final_tokens
                if args.format == "injection"
                else 0,
            },
        )
    )
    return SkillGetResponse(skills=skills, formatted_context=formatted)


__all__ = ["SkillGetArgs", "SkillGetResponse", "skill_get"]
