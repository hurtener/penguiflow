"""Phase B demo: summarisation + pause/resume + planning hints."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import (
    PlannerFinish,
    PlannerPause,
    ReactPlanner,
    ToolContext,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class Query(BaseModel):
    """Query payload."""

    topic: str


class Intent(BaseModel):
    """Intent payload."""

    intent: str


class Documents(BaseModel):
    """Documents payload."""

    documents: list[str]


class Answer(BaseModel):
    """Answer payload."""

    answer: str


@tool(desc="Detect the type of request", tags=["triage"])
async def triage(_args: Query, _ctx: ToolContext) -> Intent:
    """Classify the question into an intent."""
    return Intent(intent="docs")


@tool(desc="Approval checkpoint before side-effects", side_effects="external")
async def approval(args: Intent, ctx: ToolContext) -> Intent:
    # Pause the planner for human approval. This raises internally and
    # returns control to the caller as a PlannerPause.
    """Pause for approval."""
    await ctx.pause("approval_required", {"intent": args.intent})
    return args  # unreachable but keeps type checkers happy


@tool(desc="Retrieve supporting documents", side_effects="read")
async def retrieve(args: Intent, _ctx: ToolContext) -> Documents:
    """Return canned documents for the intent."""
    return Documents(documents=[f"weekly metrics summary for {args.intent}"])


@tool(desc="Compose the final response", tags=["summary"])
async def respond(args: Answer, _ctx: ToolContext) -> Answer:
    """Compose the final answer."""
    return args


class StubPlannerLLM(JSONLLMClient):
    """Deterministic LiteLLM-style stub that serves pre-built actions."""

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


class StubSummariser(JSONLLMClient):
    """Cheap summariser used when history exceeds the token budget."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
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
    """Build the planner with the approval tool."""
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

    scripted_actions: list[dict[str, object]] = [
        {
            "thought": "triage",
            "next_node": "triage",
            "args": {"topic": "metrics"},
        },
        {
            "thought": "approval",
            "next_node": "approval",
            "args": {"intent": "docs"},
        },
        {
            "thought": "retrieve",
            "next_node": "retrieve",
            "args": {"intent": "docs"},
        },
        {
            "thought": "respond",
            "next_node": "respond",
            "args": {"answer": "Metrics sent to Slack with highlights."},
        },
        {
            "thought": "finish",
            "next_node": None,
            "args": {"raw_answer": "done"},
        },
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
    planner.summarizer_client = StubSummariser()
    return planner


async def main() -> None:
    """Run the example."""
    planner = await build_planner()
    result = await planner.run("Share weekly metrics with approvals")

    if isinstance(result, PlannerPause):
        sys.stdout.write("Planner paused:\n")
        sys.stdout.write(f"  reason: {result.reason}\n")
        sys.stdout.write(f"  payload: {result.payload}\n")
        sys.stdout.write("Resuming with approval...\n\n")
        final = await planner.resume(
            result.resume_token,
            user_input="approved by finance",
        )
        sys.stdout.write("Final planner payload:\n")
        sys.stdout.write(f"{final.payload}\n")
        sys.stdout.write("Summary note:\n")
        assert isinstance(final, PlannerFinish)
        sys.stdout.write(f"{final.metadata['steps'][-1]}\n")
    else:
        sys.stdout.write("Planner finished in one pass:\n")
        sys.stdout.write(f"{result.payload}\n")


if __name__ == "__main__":
    asyncio.run(main())
