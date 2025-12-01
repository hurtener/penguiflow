"""Typed ToolContext demo with status callbacks and preference-aware tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    text: str


class Decision(BaseModel):
    intent: str
    note: str | None = None


class Profile(BaseModel):
    name: str
    department: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class Response(BaseModel):
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _status(ctx: ToolContext, message: str) -> None:
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(message)


@tool(desc="Classify the request and decide next step", tags=["triage"])
async def triage(args: Query, ctx: ToolContext) -> Decision:
    prefs = ctx.llm_context.get("preferences", {})
    _status(ctx, "triaging request")
    tone = prefs.get("tone", "concise")
    return Decision(intent="summarize", note=f"tone={tone}")


@tool(desc="Fetch a user profile from tool_context", side_effects="read")
async def fetch_profile(args: Decision, ctx: ToolContext) -> Profile:
    _status(ctx, "fetching profile from tool_context")
    store = ctx.tool_context.get("profile_store", {})
    raw = store.get("user", {"name": "Ava Agent", "department": "eng"})
    return Profile.model_validate({**raw, "preferences": {"note": args.note}})


@tool(desc="Compose the final response", tags=["summary"])
async def respond(args: Profile, ctx: ToolContext) -> Response:
    _status(ctx, "composing answer")
    message = f"Hello {args.name}! I will keep things {args.preferences.get('note', 'brief')}."
    return Response(
        message=message,
        metadata={"department": args.department, "preferences": args.preferences},
    )


class ScriptedLLM:
    """Returns predefined planner actions (JSON)."""

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
    registry.register("triage", Query, Decision)
    registry.register("fetch_profile", Decision, Profile)
    registry.register("respond", Profile, Response)

    nodes = [
        Node(triage, name="triage"),
        Node(fetch_profile, name="fetch_profile"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    scripted_actions = [
        {"thought": "triage", "next_node": "triage", "args": {"text": "send update"}},
        {
            "thought": "fetch profile",
            "next_node": "fetch_profile",
            "args": {"intent": "summarize", "note": "tone=warm"},
        },
        {
            "thought": "respond",
            "next_node": "respond",
            "args": {"name": "Ava Agent", "department": "eng", "preferences": {"note": "tone=warm"}},
        },
        {"thought": "finish", "next_node": None, "args": None},
    ]

    return ReactPlanner(
        llm_client=ScriptedLLM(scripted_actions),
        catalog=catalog,
        max_iters=4,
    )


async def main() -> None:
    planner = await build_planner()
    statuses: list[str] = []

    def publish_status(message: str) -> None:
        statuses.append(message)
        print(f"STATUS: {message}")

    result = await planner.run(
        "Share a weekly summary",
        llm_context={"preferences": {"tone": "warm"}},
        tool_context={
            "status_publisher": publish_status,
            "profile_store": {"user": {"name": "Casey", "department": "product"}},
        },
    )

    print("\nPlanner payload:")
    print(result.payload)
    print("\nMetadata:")
    print(result.metadata)
    print("\nStatuses:")
    for msg in statuses:
        print(f"- {msg}")


if __name__ == "__main__":
    asyncio.run(main())
