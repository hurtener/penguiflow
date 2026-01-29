from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..catalog import ToolLoadingMode, tool
from .models import PlannerEvent


class ToolSearchArgs(BaseModel):
    query: str
    search_type: Literal["fts", "regex", "exact"] = "fts"
    limit: int = Field(default=8, ge=1, le=20)
    include_always_loaded: bool = False


class ToolSearchResult(BaseModel):
    name: str
    description: str
    score: float
    match_type: Literal["exact", "regex", "fts"]
    loading_mode: ToolLoadingMode


class ToolSearchResponse(BaseModel):
    tools: list[ToolSearchResult]
    query: str
    search_type: Literal["fts", "regex", "exact"]


@tool(desc="Discover tools by capability and keywords.", loading_mode=ToolLoadingMode.ALWAYS)
async def tool_search(args: ToolSearchArgs, ctx: Any) -> ToolSearchResponse:
    planner = getattr(ctx, "_planner", None)
    cache = getattr(planner, "_tool_search_cache", None) if planner is not None else None
    if cache is None:
        raise RuntimeError("tool_search is not configured")

    allowed_names = getattr(planner, "_tool_visibility_allowed_names", None)
    if allowed_names is None:
        execution_specs = getattr(planner, "_execution_specs", [])
        allowed_names = {spec.name for spec in execution_specs}

    limit = min(int(args.limit), int(getattr(planner, "_tool_search_max_results", args.limit)))
    results, effective = cache.search(
        args.query,
        search_type=args.search_type,
        limit=limit,
        include_always_loaded=bool(args.include_always_loaded),
        allowed_names=set(allowed_names),
    )

    tools = [ToolSearchResult(**item) for item in results]
    response = ToolSearchResponse(tools=tools, query=args.query, search_type=effective)

    if planner is not None:
        step_index = 0
        planner._emit_event(
            PlannerEvent(
                event_type="tool_search_query",
                ts=planner._time_source(),
                trajectory_step=step_index,
                extra={
                    "query": args.query,
                    "requested_search_type": args.search_type,
                    "effective_search_type": effective,
                    "results_count": len(tools),
                },
            )
        )

    return response


__all__ = [
    "ToolSearchArgs",
    "ToolSearchResponse",
    "ToolSearchResult",
    "tool_search",
]
