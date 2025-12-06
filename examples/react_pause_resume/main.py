"""Phase B demo: summarisation + pause/resume + planning hints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerPause, ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    topic: str


class Intent(BaseModel):
    intent: str


class Documents(BaseModel):
    documents: list[str]


class Answer(BaseModel):
    answer: str


@tool(desc="Detect the type of request", tags=["triage"])
async def triage(args: Query, ctx: ToolContext) -> Intent:
    return Intent(intent="docs")


@tool(desc="Approval checkpoint before side-effects", side_effects="external")
async def approval(args: Intent, ctx: ToolContext) -> Intent:
    # Pause the planner for human approval. This raises internally and
    # returns control to the caller as a PlannerPause.
    await ctx.pause("approval_required", {"intent": args.intent})
    return args  # unreachable but keeps type checkers happy


@tool(desc="Retrieve supporting documents", side_effects="read")
async def retrieve(args: Intent, ctx: ToolContext) -> Documents:
    return Documents(documents=[f"weekly metrics summary for {args.intent}"])


@tool(desc="Compose the final response", tags=["summary"])
async def respond(args: Answer, ctx: ToolContext) -> Answer:
    return args


class StubPlannerLLM:
    """Deterministic LiteLLM-style stub that serves pre-built actions."""

    def __init__(self, actions: Sequence[Mapping[str, Any]]) -> None:
        self._payloads = [json.dumps(item) for item in actions]

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        return self._payloads.pop(0)


class StubSummariser:
    """Cheap summariser used when history exceeds the token budget."""

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        return json.dumps(
            {
                "goals": ["Send weekly metrics to stakeholders"],
                "facts": {"status": "awaiting approval"},
                "pending": ["approval"],
                "last_output_digest": "approval pending",
                "note": "stub",
            }
        )


async def build_planner() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("approval", Intent, Intent)
    registry.register("retrieve", Intent, Documents)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(approval, name="approval"),
        Node(retrieve, name="retrieve"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    scripted_actions = [
        {"thought": "triage", "next_node": "triage", "args": {"topic": "metrics"}},
        {"thought": "approval", "next_node": "approval", "args": {"intent": "docs"}},
        {"thought": "retrieve", "next_node": "retrieve", "args": {"intent": "docs"}},
        {
            "thought": "respond",
            "next_node": "respond",
            "args": {"answer": "Metrics sent to Slack with highlights."},
        },
        {"thought": "finish", "next_node": None, "args": {"raw_answer": "done"}},
    ]

    planner = ReactPlanner(
        llm_client=StubPlannerLLM(scripted_actions),
        catalog=catalog,
        pause_enabled=True,
        token_budget=160,
        planning_hints={
            "ordering_hints": ["triage", "approval", "retrieve", "respond"],
            "disallow_nodes": ["broken_tool"],
            "budget_hints": {"max_parallel": 1},
        },
    )
    planner._summarizer_client = StubSummariser()  # type: ignore[attr-defined]
    return planner


async def main() -> None:
    planner = await build_planner()
    result = await planner.run("Share weekly metrics with approvals")

    if isinstance(result, PlannerPause):
        print("Planner paused:")
        print(f"  reason: {result.reason}")
        print(f"  payload: {result.payload}")
        print("Resuming with approval...\n")
        final = await planner.resume(
            result.resume_token,
            user_input="approved by finance",
        )
        print("Final planner payload:")
        print(final.payload)
        print("Summary note:")
        print(final.metadata["steps"][-1])
    else:
        print("Planner finished in one pass:")
        print(result.payload)


if __name__ == "__main__":
    asyncio.run(main())
