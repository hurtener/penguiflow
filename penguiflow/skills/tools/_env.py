"""Planner lookups shared by the skill tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..provider import build_skill_capability_context

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...planner.context import ToolContext
    from ...planner.react import ReactPlanner
    from ..models import SkillCapabilityContext
    from ..provider import SkillProvider


@dataclass(frozen=True, slots=True)
class SkillToolEnv:
    """The planner, provider and contexts a skill tool runs against."""

    planner: ReactPlanner
    provider: SkillProvider
    tool_context: Mapping[str, object]
    capability_context: SkillCapabilityContext


def context_planner(ctx: ToolContext) -> ReactPlanner | None:
    """Return the planner that built ``ctx``, if it exposes one."""
    planner: ReactPlanner | None = getattr(ctx, "planner", None)
    return planner


def skill_tool_env(ctx: ToolContext, tool_name: str) -> SkillToolEnv:
    """Resolve the planner and skill provider behind a skill tool call.

    Raises:
        RuntimeError: If the planner has no skill provider configured.
    """
    planner = context_planner(ctx)
    provider = planner.skills_provider if planner is not None else None
    if planner is None or provider is None:
        msg = f"{tool_name} is not configured"
        raise RuntimeError(msg)
    execution_specs = planner.execution_spec_by_name
    return SkillToolEnv(
        planner=planner,
        provider=provider,
        tool_context=ctx.tool_context,
        capability_context=build_skill_capability_context(
            execution_specs=execution_specs,
            all_tool_names=execution_specs.keys(),
            allowed_tool_names=planner.tool_visibility_allowed_names,
        ),
    )
