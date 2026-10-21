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
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional declared-tag AND filter. Each tag must appear in the tool's "
            "declared tag list. Empty list disables the filter (back-compat default)."
        ),
    )
    namespace: str | None = Field(
        default=None,
        description=(
            "Optional dot-prefix namespace filter. Matches tools whose name equals "
            "the namespace or starts with '<namespace>.'. None disables the filter."
        ),
    )


class ToolSearchResult(BaseModel):
    name: str
    description: str
    score: float = Field(
        description="Relevance relative to the other results, from 0.0 to 1.0 (the best full-text match is 1.0).",
    )
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
        tags=tuple(args.tags),
        namespace=args.namespace,
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
                    "tags": list(args.tags),
                    "namespace": args.namespace,
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
