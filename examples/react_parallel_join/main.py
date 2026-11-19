"""Parallel fan-out with explicit join injection and failure handling."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class UpstreamUnavailableError(RuntimeError):
    """Raised when a search provider cannot be reached."""

    def __init__(self, *, code: str, message: str) -> None:
        """Store ``code`` and ``message``."""
        super().__init__(message)
        self.code = code
        self.message = message


class SearchArgs(BaseModel):
    """Arguments of the search tool."""

    topic: str
    provider: str


class SearchResult(BaseModel):
    """Output of the search tool."""

    provider: str
    docs: list[str]


class JoinArgs(BaseModel):
    """Arguments of the join tool."""

    branch_outputs: list[SearchResult]
    total_requests: int
    failures: list[dict[str, Any]] = Field(default_factory=list)
    failure_count: int = 0
    success_count: int = 0


class JoinResult(BaseModel):
    """Output of the join tool."""

    merged: list[str]
    failures: list[dict[str, Any]]
    note: str


@tool(
    desc="Parallel-friendly document search",
    side_effects="read",
    tags=["search"],
)
async def search_catalog(args: SearchArgs, ctx: ToolContext) -> SearchResult:
    """Return canned results; simulate a flaky provider."""
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(f"[{args.provider}] searching for {args.topic}")

    if args.provider == "unstable":
        # Simulate a branch failure so join can surface it
        raise UpstreamUnavailableError(
            code="UPSTREAM_UNAVAILABLE",
            message="Upstream provider is temporarily offline",
        )

    docs = [
        f"{args.topic} overview ({args.provider})",
        f"{args.topic} recent news ({args.provider})",
    ]
    return SearchResult(provider=args.provider, docs=docs)


@tool(
    desc="Merge parallel search results with explicit injection",
    tags=["join", "merge"],
    side_effects="read",
)
async def merge_results(args: JoinArgs, ctx: ToolContext) -> JoinResult:
    """Merge the documents of all shards."""
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(
            f"joining {len(args.branch_outputs)} of "
            f"{args.total_requests} branches "
            f"(failures={args.failure_count})"
        )

    merged = [
        f"{item.provider}: {doc}"
        for item in args.branch_outputs
        for doc in item.docs
    ]
    note = (
        "partial success" if args.failure_count else "all branches succeeded"
    )

    return JoinResult(merged=merged, failures=args.failures, note=note)


class ScriptedPlannerLLM(JSONLLMClient):
    """LiteLLM-compatible stub returning predefined planner actions."""

    def __init__(self, actions: Sequence[Mapping[str, Any]]) -> None:
        """Replay ``actions`` in order."""
        self._payloads = [json.dumps(item) for item in actions]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        return self._payloads.pop(0)


async def build_planner() -> ReactPlanner:
    """Build the planner with the search and join tools."""
    registry = ModelRegistry()
    registry.register("search_catalog", SearchArgs, SearchResult)
    registry.register("merge_results", JoinArgs, JoinResult)

    nodes = [
        Node(search_catalog, name="search_catalog"),
        Node(merge_results, name="merge_results"),
    ]
    catalog = build_catalog(nodes, registry)

    scripted_actions: list[dict[str, object]] = [
        {
            "thought": "fan out to multiple providers",
            "plan": [
                {
                    "node": "search_catalog",
                    "args": {"topic": "penguins", "provider": "wiki"},
                },
                {
                    "node": "search_catalog",
                    "args": {"topic": "penguins", "provider": "unstable"},
                },
            ],
            "join": {
                "node": "merge_results",
                "inject": {
                    "branch_outputs": "$results",
                    "total_requests": "$expect",
                    "failures": "$failures",
                    "failure_count": "$failure_count",
                    "success_count": "$success_count",
                },
            },
        },
        {
            "thought": "finish after merge",
            "next_node": None,
            "args": {"raw_answer": "Merged available search results."},
        },
    ]

    return ReactPlanner(
        llm_client=ScriptedPlannerLLM(scripted_actions),
        catalog=catalog,
        max_iters=4,
    )


async def main() -> None:
    """Run the example."""
    planner = await build_planner()
    status_updates: list[str] = []

    def publish_status(message: str) -> None:
        status_updates.append(message)
        sys.stdout.write(f"STATUS: {message}\n")

    result = await planner.run(
        "Find recent penguin research",
        llm_context={"experiment": "parallel_join"},
        tool_context={"status_publisher": publish_status},
    )

    sys.stdout.write("\nPlanner result:\n")
    sys.stdout.write(f"{result.payload}\n")
    sys.stdout.write("\nStatus updates:\n")
    for line in status_updates:
        sys.stdout.write(f"- {line}\n")


if __name__ == "__main__":
    asyncio.run(main())
