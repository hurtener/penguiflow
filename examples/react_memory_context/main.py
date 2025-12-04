"""Example: Memory-based planning with custom context.

This demonstrates how to use system_prompt_extra and llm_context to provide
custom memory structures to the planner. The library handles injection; you
define the format and semantics.
"""

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
        print(f"System: {messages[0]['content'][:100]}...")
        if len(messages) > 1:
            user_msg = json.loads(messages[1]["content"])
            if "context" in user_msg:
                print(f"Context provided: {user_msg['context']}")

        result = self._responses[self._call_count]
        self._call_count += 1
        return result


async def main() -> None:
    """Run example with different memory formats."""
    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)
    registry.register("analyze", SearchResult, Answer)

    nodes = [
        Node(search, name="search"),
        Node(analyze, name="analyze"),
    ]

    # Example 1: JSON-structured memory
    print("=" * 60)
    print("Example 1: JSON-structured user preferences")
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
            "The context.user_prefs contains a JSON object with user preferences. "
            "When selecting tools and arguments, prioritize these preferences."
        ),
    )

    result1 = await planner1.run(
        "What programming language should I learn?",
        llm_context={
            "user_prefs": {"preferred_language": "Python", "experience": "beginner"}
        },
    )
    print(f"\nResult: {result1.reason}")
    print(f"Payload: {result1.payload}")

    # Example 2: Text-based knowledge
    print("\n" + "=" * 60)
    print("Example 2: Free-form knowledge base")
    print("=" * 60)

    client2 = StubLLM(
        [
            {
                "thought": "Based on previous failures, try different approach",
                "next_node": "search",
                "args": {"text": "JavaScript web development"},
            },
            {
                "thought": "done",
                "next_node": None,
                "args": {"raw_answer": "JavaScript is popular for web development"},
            },
        ]
    )

    planner2 = ReactPlanner(
        llm_client=client2,
        catalog=build_catalog(nodes, registry),
        system_prompt_extra=(
            "The context.previous_failures lists approaches that failed. "
            "Avoid repeating the same tool calls or arguments."
        ),
    )

    result2 = await planner2.run(
        "Help me learn web development",
        llm_context={
            "previous_failures": [
                "search with 'Python web' returned no results",
                "analyze timed out with large result set",
            ]
        },
    )
    print(f"\nResult: {result2.reason}")
    print(f"Payload: {result2.payload}")

    # Example 3: Hierarchical context
    print("\n" + "=" * 60)
    print("Example 3: Hierarchical memory structure")
    print("=" * 60)

    client3 = StubLLM(
        [
            {
                "thought": "Check session history first",
                "next_node": "search",
                "args": {"text": "Python data science"},
            },
            {
                "thought": "done",
                "next_node": None,
                "args": {"raw_answer": "Python excels in data science"},
            },
        ]
    )

    planner3 = ReactPlanner(
        llm_client=client3,
        catalog=build_catalog(nodes, registry),
        system_prompt_extra=(
            "The context.memory has nested structure:\n"
            "- user_profile: Long-term preferences\n"
            "- session_history: Recent interactions in this session\n"
            "- task_context: Current task-specific info\n"
            "Prioritize more specific context (task > session > profile)."
        ),
    )

    result3 = await planner3.run(
        "Recommend a learning path",
        llm_context={
            "memory": {
                "user_profile": {"skill_level": "intermediate", "domain": "data"},
                "session_history": ["asked about Python", "interested in ML"],
                "task_context": {"deadline": "3 months", "goal": "job-ready"},
            }
        },
    )
    print(f"\nResult: {result3.reason}")
    print(f"Payload: {result3.payload}")


if __name__ == "__main__":
    asyncio.run(main())
