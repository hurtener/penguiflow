"""Parallel fan-out with explicit join injection and failure handling."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from penguiflow.catalog import build_catalog, tool
from penguiflow.errors import FlowError
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry


class SearchArgs(BaseModel):
    topic: str
    provider: str


class SearchResult(BaseModel):
    provider: str
    docs: list[str]


class JoinArgs(BaseModel):
    branch_outputs: list[SearchResult]
    total_requests: int
    failures: list[dict[str, Any]] = Field(default_factory=list)
    failure_count: int = 0
    success_count: int = 0


class JoinResult(BaseModel):
    merged: list[str]
    failures: list[dict[str, Any]]
    note: str


@tool(desc="Parallel-friendly document search", side_effects="read", tags=["search"])
async def search_catalog(args: SearchArgs, ctx: ToolContext) -> SearchResult:
    """Return canned results; simulate a flaky provider."""
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(f"[{args.provider}] searching for {args.topic}")

    if args.provider == "unstable":
        # Simulate a branch failure so join can surface it
        raise FlowError(
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
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(
            f"joining {len(args.branch_outputs)} of {args.total_requests} branches "
            f"(failures={args.failure_count})"
        )

    merged = [f"{item.provider}: {doc}" for item in args.branch_outputs for doc in item.docs]
    note = "partial success" if args.failure_count else "all branches succeeded"

    return JoinResult(merged=merged, failures=args.failures, note=note)


class ScriptedPlannerLLM:
    """LiteLLM-compatible stub returning predefined planner actions."""

    def __init__(self, actions: Sequence[Mapping[str, Any]]) -> None:
        self._payloads = [json.dumps(item) for item in actions]

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        del messages, response_format
        return self._payloads.pop(0)


async def build_planner() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("search_catalog", SearchArgs, SearchResult)
    registry.register("merge_results", JoinArgs, JoinResult)

    nodes = [
        Node(search_catalog, name="search_catalog"),
        Node(merge_results, name="merge_results"),
    ]
    catalog = build_catalog(nodes, registry)

    scripted_actions = [
        {
            "thought": "fan out to multiple providers",
            "plan": [
                {"node": "search_catalog", "args": {"topic": "penguins", "provider": "wiki"}},
                {"node": "search_catalog", "args": {"topic": "penguins", "provider": "unstable"}},
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
    planner = await build_planner()
    status_updates: list[str] = []

    def publish_status(message: str) -> None:
        status_updates.append(message)
        print(f"STATUS: {message}")

    result = await planner.run(
        "Find recent penguin research",
        llm_context={"experiment": "parallel_join"},
        tool_context={"status_publisher": publish_status},
    )

    print("\nPlanner result:")
    print(result.payload)
    print("\nStatus updates:")
    for line in status_updates:
        print(f"- {line}")


if __name__ == "__main__":
    asyncio.run(main())
