"""Example: External memory hook injection.

This demonstrates how to:
- Define an opt-in pre-run hook that injects `external_memory` into llm_context.
- Render external memory as a dedicated read-only system message.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import LLMContextHookInput, ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    text: str


class SearchResult(BaseModel):
    results: list[str]


class Answer(BaseModel):
    answer: str


@tool(desc="Search for information based on query", tags=["search"])
async def search(args: Query, ctx: ToolContext) -> SearchResult:
    """Mock search that returns canned results."""
    return SearchResult(
        results=[
            "Python is great for data science",
            "JavaScript is popular for web development",
        ]
    )


@tool(desc="Analyze search results and produce answer")
async def analyze(args: SearchResult, ctx: ToolContext) -> Answer:
    """Mock analysis."""
    return Answer(answer="Based on the results, both languages have strengths.")


class StubLLM:
    """Deterministic LLM that uses memory context."""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = [json.dumps(item) for item in responses]
        self._call_count = 0

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        del response_format
        # In a real scenario, the LLM would see the context in messages
        # and use it to inform decisions
        print(f"\nLLM Call {self._call_count + 1}:")
        system_messages = [m["content"] for m in messages if m.get("role") == "system"]
        print(f"System messages: {len(system_messages)}")
        for idx, content in enumerate(system_messages, start=1):
            marker = "<read_only_external_memory_json>" in content
            print(f"  system[{idx}] external_memory={marker}")
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        if user_messages:
            user_msg = json.loads(user_messages[0])
            if "context" in user_msg:
                print(f"User JSON context keys: {list((user_msg.get('context') or {}).keys())}")

        result = self._responses[self._call_count]
        self._call_count += 1
        return result


async def main() -> None:
    """Run example with a hook-injected external_memory payload."""
    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)
    registry.register("analyze", SearchResult, Answer)

    nodes = [
        Node(search, name="search"),
        Node(analyze, name="analyze"),
    ]

    class StaticExternalMemoryHook:
        async def before_run(self, inp: LLMContextHookInput) -> Mapping[str, Any]:
            del inp
            return {
                "external_memory": {
                    "retrieved_memories": [{"title": "pref", "snippet": "User prefers Python"}]
                }
            }

    # Example: Hook-injected external memory
    print("=" * 60)
    print("Example: Hook-injected external memory")
    print("=" * 60)

    client1 = StubLLM(
        [
            {
                "thought": "User prefers Python, search accordingly",
                "next_node": "search",
                "args": {"text": "Python programming"},
            },
            {
                "thought": "done",
                "next_node": None,
                "args": {"raw_answer": "Python is great for data science"},
            },
        ]
    )

    planner1 = ReactPlanner(
        llm_client=client1,
        catalog=build_catalog(nodes, registry),
        system_prompt_extra=(
            "Use external_memory.retrieved_memories (read-only) to personalize planning."
        ),
        llm_context_hooks=[StaticExternalMemoryHook()],
    )

    result1 = await planner1.run(
        "What programming language should I learn?",
        llm_context={},
    )
    print(f"\nResult: {result1.reason}")
    print(f"Payload: {result1.payload}")


if __name__ == "__main__":
    asyncio.run(main())
