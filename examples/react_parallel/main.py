"""Parallel fan-out example for the React planner."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class ShardRequest(BaseModel):
    """Shard request payload."""

    topic: str
    shard: int


class ShardResult(BaseModel):
    """Output of the shard tool."""

    shard: int
    text: str


class MergeArgs(BaseModel):
    """Arguments of the merge tool."""

    expect: int
    results: list[ShardResult]


class Documents(BaseModel):
    """Documents payload."""

    documents: list[str]


@tool(desc="Fetch from the primary shard", tags=["parallel"])
async def fetch_primary(args: ShardRequest, _ctx: ToolContext) -> ShardResult:
    """Fetch documents from the primary shard."""
    await asyncio.sleep(0.1)
    return ShardResult(shard=args.shard, text=f"{args.topic}-primary")


@tool(desc="Fetch from the secondary shard", tags=["parallel"])
async def fetch_secondary(
    args: ShardRequest, _ctx: ToolContext
) -> ShardResult:
    """Fetch documents from the secondary shard."""
    await asyncio.sleep(0.1)
    return ShardResult(shard=args.shard, text=f"{args.topic}-secondary")


@tool(desc="Merge shard payloads")
async def merge_results(args: MergeArgs, ctx: ToolContext) -> Documents:
    # The planner stores branch metadata in ctx.tool_context for joins.
    """Merge the documents of all shards."""
    assert ctx.tool_context["parallel_success_count"] == args.expect
    merged = [item.text for item in args.results]
    return Documents(documents=merged)


class SequenceLLM(JSONLLMClient):
    """Deterministic stub returning scripted planner actions."""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        if not self._responses:
            msg = "SequenceLLM has no responses left"
            raise RuntimeError(msg)
        return self._responses.pop(0)


async def main() -> None:
    """Run the example."""
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
                "args": {
                    "documents": ["penguins-primary", "penguins-secondary"]
                },
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("Compile penguin metrics")
    sys.stdout.write(
        f"{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
