"""Typed ToolContext demo with status callbacks and preference-aware tools."""

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
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class Query(BaseModel):
    """Query payload."""

    text: str


class Decision(BaseModel):
    """Decision payload."""

    intent: str
    note: str | None = None


class Profile(BaseModel):
    """Profile payload."""

    name: str
    department: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel):
    """Response payload."""

    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _status(ctx: ToolContext, message: str) -> None:
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(message)


@tool(desc="Classify the request and decide next step", tags=["triage"])
async def triage(_args: Query, ctx: ToolContext) -> Decision:
    """Classify the question into an intent."""
    prefs = ctx.llm_context.get("preferences", {})
    _status(ctx, "triaging request")
    tone = prefs.get("tone", "concise")
    return Decision(intent="summarize", note=f"tone={tone}")


@tool(desc="Fetch a user profile from tool_context", side_effects="read")
async def fetch_profile(args: Decision, ctx: ToolContext) -> Profile:
    """Return the user profile."""
    _status(ctx, "fetching profile from tool_context")
    store = ctx.tool_context.get("profile_store", {})
    raw = store.get("user", {"name": "Ava Agent", "department": "eng"})
    return Profile.model_validate({**raw, "preferences": {"note": args.note}})


@tool(desc="Compose the final response", tags=["summary"])
async def respond(args: Profile, ctx: ToolContext) -> Response:
    """Compose the final answer."""
    _status(ctx, "composing answer")
    message = (
        f"Hello {args.name}! I will keep things "
        f"{args.preferences.get('note', 'brief')}."
    )
    return Response(
        message=message,
        metadata={
            "department": args.department,
            "preferences": args.preferences,
        },
    )


class ScriptedLLM(JSONLLMClient):
    """Returns predefined planner actions (JSON)."""

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
    """Build the planner with the typed tools."""
    registry = ModelRegistry()
    registry.register("triage", Query, Decision)
    registry.register("fetch_profile", Decision, Profile)
    registry.register("respond", Profile, Response)

    nodes = [
        Node(triage, name="triage"),
        Node(fetch_profile, name="fetch_profile"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    scripted_actions: list[dict[str, object]] = [
        {
            "thought": "triage",
            "next_node": "triage",
            "args": {"text": "send update"},
        },
        {
            "thought": "fetch profile",
            "next_node": "fetch_profile",
            "args": {"intent": "summarize", "note": "tone=warm"},
        },
        {
            "thought": "respond",
            "next_node": "respond",
            "args": {
                "name": "Ava Agent",
                "department": "eng",
                "preferences": {"note": "tone=warm"},
            },
        },
        {"thought": "finish", "next_node": None, "args": None},
    ]

    return ReactPlanner(
        llm_client=ScriptedLLM(scripted_actions),
        catalog=catalog,
        max_iters=4,
    )


async def main() -> None:
    """Run the example."""
    planner = await build_planner()
    statuses: list[str] = []

    def publish_status(message: str) -> None:
        statuses.append(message)
        sys.stdout.write(f"STATUS: {message}\n")

    result = await planner.run(
        "Share a weekly summary",
        llm_context={"preferences": {"tone": "warm"}},
        tool_context={
            "status_publisher": publish_status,
            "profile_store": {
                "user": {"name": "Casey", "department": "product"}
            },
        },
    )

    sys.stdout.write("\nPlanner payload:\n")
    sys.stdout.write(f"{result.payload}\n")
    sys.stdout.write("\nMetadata:\n")
    assert isinstance(result, PlannerFinish)
    sys.stdout.write(f"{result.metadata}\n")
    sys.stdout.write("\nStatuses:\n")
    for msg in statuses:
        sys.stdout.write(f"- {msg}\n")


if __name__ == "__main__":
    asyncio.run(main())
