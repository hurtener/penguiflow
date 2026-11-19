"""The ``tool_search`` planner tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..catalog import ToolLoadingMode, tool
from .models import PlannerEvent

if TYPE_CHECKING:
    from .context import ToolContext


class ToolSearchArgs(BaseModel):
    """Arguments for ``tool_search``."""

    query: str
    search_type: Literal["fts", "regex", "exact"] = "fts"
    limit: int = Field(default=8, ge=1, le=20)
    include_always_loaded: bool = False
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional declared-tag AND filter. Each tag must appear "
            "in the tool's "
            "declared tag list. Empty list disables the filter "
            "(back-compat default)."
        ),
    )
    namespace: str | None = Field(
        default=None,
        description=(
            "Optional dot-prefix namespace filter. Matches tools "
            "whose name equals "
            "the namespace or starts with '<namespace>.'. None "
            "disables the filter."
        ),
    )


class ToolSearchResult(BaseModel):
    """A tool matching the search."""

    name: str
    description: str
    score: float
    match_type: Literal["exact", "regex", "fts"]
    loading_mode: ToolLoadingMode


class ToolSearchResponse(BaseModel):
    """The tools matching a search."""

    tools: list[ToolSearchResult]
    query: str
    search_type: Literal["fts", "regex", "exact"]


@tool(
    desc="Discover tools by capability and keywords.",
    loading_mode=ToolLoadingMode.ALWAYS,
)
async def tool_search(
    args: ToolSearchArgs, ctx: ToolContext
) -> ToolSearchResponse:
    """Search the tool catalog by capability and keywords."""
    planner = getattr(ctx, "planner", None)
    cache = (
        getattr(planner, "tool_search_cache", None)
        if planner is not None
        else None
    )
    if cache is None:
        msg = "tool_search is not configured"
        raise RuntimeError(msg)

    allowed_names = getattr(planner, "tool_visibility_allowed_names", None)
    if allowed_names is None:
        execution_specs = getattr(planner, "execution_specs", [])
        allowed_names = {spec.name for spec in execution_specs}

    limit = min(
        int(args.limit),
        int(getattr(planner, "tool_search_max_results", args.limit)),
    )
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
    response = ToolSearchResponse(
        tools=tools, query=args.query, search_type=effective
    )

    if planner is not None:
        step_index = 0
        planner.emit_event(
            PlannerEvent(
                event_type="tool_search_query",
                ts=planner.time_source(),
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
