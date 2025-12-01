"""Parallel fan-out example for the React planner."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry


class ShardRequest(BaseModel):
    topic: str
    shard: int


class ShardResult(BaseModel):
    shard: int
    text: str


class MergeArgs(BaseModel):
    expect: int
    results: list[ShardResult]


class Documents(BaseModel):
    documents: list[str]


@tool(desc="Fetch from the primary shard", tags=["parallel"])
async def fetch_primary(args: ShardRequest, ctx: ToolContext) -> ShardResult:
    await asyncio.sleep(0.1)
    return ShardResult(shard=args.shard, text=f"{args.topic}-primary")


@tool(desc="Fetch from the secondary shard", tags=["parallel"])
async def fetch_secondary(args: ShardRequest, ctx: ToolContext) -> ShardResult:
    await asyncio.sleep(0.1)
    return ShardResult(shard=args.shard, text=f"{args.topic}-secondary")


@tool(desc="Merge shard payloads")
async def merge_results(args: MergeArgs, ctx: ToolContext) -> Documents:
    # The planner stores branch metadata in ctx.tool_context for joins.
    assert ctx.tool_context["parallel_success_count"] == args.expect
    merged = [item.text for item in args.results]
    return Documents(documents=merged)


class SequenceLLM:
    """Deterministic stub returning scripted planner actions."""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        del messages, response_format
        if not self._responses:
            raise RuntimeError("SequenceLLM has no responses left")
        return self._responses.pop(0)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("fetch_primary", ShardRequest, ShardResult)
    registry.register("fetch_secondary", ShardRequest, ShardResult)
    registry.register("merge_results", MergeArgs, Documents)

    nodes = [
        Node(fetch_primary, name="fetch_primary"),
        Node(fetch_secondary, name="fetch_secondary"),
        Node(merge_results, name="merge_results"),
    ]

    client = SequenceLLM(
        [
            {
                "thought": "fan out",
                "plan": [
                    {
                        "node": "fetch_primary",
                        "args": {"topic": "penguins", "shard": 0},
                    },
                    {
                        "node": "fetch_secondary",
                        "args": {"topic": "penguins", "shard": 1},
                    },
                ],
                "join": {
                    "node": "merge_results",
                    "inject": {
                        "results": "$results",
                        "expect": "$expect",
                    },
                },
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"documents": ["penguins-primary", "penguins-secondary"]},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("Compile penguin metrics")
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
