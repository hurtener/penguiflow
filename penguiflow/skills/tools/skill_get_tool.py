from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ...catalog import ToolLoadingMode, tool
from ...planner.models import PlannerEvent
from ..models import SkillResultDetailed


class SkillGetArgs(BaseModel):
    names: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    format: Literal["raw", "injection"] = "injection"
    max_tokens: int = Field(default=1500, ge=200, le=6000)


class SkillGetResponse(BaseModel):
    skills: list[SkillResultDetailed]
    formatted_context: str


@tool(desc="Fetch skills by name for full content.", loading_mode=ToolLoadingMode.ALWAYS)
async def skill_get(args: SkillGetArgs, ctx: Any) -> SkillGetResponse:
    planner = getattr(ctx, "_planner", None)
    provider = getattr(planner, "_skills_provider", None) if planner is not None else None
    if provider is None:
        raise RuntimeError("skill_get is not configured")
    tool_context = getattr(ctx, "tool_context", {}) if ctx is not None else {}
    all_tool_names = getattr(planner, "_execution_spec_by_name", {}).keys() if planner is not None else None
    allowed = getattr(planner, "_tool_visibility_allowed_names", None)
    skills = await provider.get_by_name(
        list(args.names),
        tool_context=tool_context,
        all_tool_names=all_tool_names,
        allowed_tool_names=allowed,
    )
    formatted = ""
    final_tokens = 0
    if args.format == "injection":
        formatted, _raw_tokens, final_tokens, _summarized = await provider.format_for_injection(
            skills, max_tokens=args.max_tokens
        )
    if planner is not None:
        planner._emit_event(
            PlannerEvent(
                event_type="skill_get",
                ts=planner._time_source(),
                trajectory_step=0,
                extra={
                    "names": list(args.names),
                    "returned_count": len(skills),
                    "max_tokens": args.max_tokens,
                    "final_tokens_est": final_tokens if args.format == "injection" else 0,
                },
            )
        )
    return SkillGetResponse(skills=skills, formatted_context=formatted)


__all__ = ["SkillGetArgs", "SkillGetResponse", "skill_get"]
