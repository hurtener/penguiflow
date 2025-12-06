from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolPolicy
from penguiflow.registry import ModelRegistry


class SearchQuery(BaseModel):
    text: str


class SearchDocs(BaseModel):
    documents: list[str]


class Summary(BaseModel):
    text: str


@tool(desc="Search public knowledge base", tags=["safe"])
async def search_public(args: SearchQuery, ctx: object) -> SearchDocs:
    del ctx
    return SearchDocs(documents=[f"Public insight about {args.text}"])


@tool(desc="Search internal knowledge base", tags=["safe", "internal"])
async def search_internal(args: SearchQuery, ctx: object) -> SearchDocs:
    del ctx
    return SearchDocs(documents=[f"Internal note about {args.text}"])


@tool(desc="Summarise supporting documents", tags=["safe"])
async def summarise(args: SearchDocs, ctx: object) -> Summary:
    del ctx
    joined = "; ".join(args.documents)
    return Summary(text=f"Summary: {joined}")


@tool(desc="Send summary via email", tags=["safe", "write"])
async def send_email(args: Summary, ctx: object) -> Summary:
    del ctx
    return Summary(text=f"Email sent: {args.text}")


@tool(desc="Dangerous user deletion", tags=["unsafe", "admin"])
async def delete_user(args: SearchQuery, ctx: object) -> Summary:
    del ctx
    return Summary(text=f"Deleted user tied to {args.text}")


class SequenceLLM:
    """Deterministic planner stub returning scripted actions."""

    def __init__(self, responses: Sequence[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        del messages, response_format
        if not self._responses:
            raise RuntimeError("SequenceLLM has no responses left")
        return self._responses.pop(0)


async def run_policy(
    name: str,
    *,
    policy: ToolPolicy,
    catalog: Sequence[Node],
    registry: ModelRegistry,
    responses: Sequence[Mapping[str, object]],
) -> None:
    planner = ReactPlanner(
        llm_client=SequenceLLM(responses),
        catalog=build_catalog(catalog, registry),
        tool_policy=policy,
    )
    print(f"{name} visible tools: {sorted(planner._spec_by_name)}")
    result = await planner.run("Share onboarding learnings")
    print(f"{name} final payload: {result.payload}")
    print()


async def main() -> None:
    registry = ModelRegistry()
    registry.register("search_public", SearchQuery, SearchDocs)
    registry.register("search_internal", SearchQuery, SearchDocs)
    registry.register("summarise", SearchDocs, Summary)
    registry.register("send_email", Summary, Summary)
    registry.register("delete_user", SearchQuery, Summary)

    nodes = [
        Node(search_public, name="search_public"),
        Node(search_internal, name="search_internal"),
        Node(summarise, name="summarise"),
        Node(send_email, name="send_email"),
        Node(delete_user, name="delete_user"),
    ]

    free_policy = ToolPolicy(allowed_tools={"search_public", "summarise"})
    premium_policy = ToolPolicy(denied_tools={"delete_user"}, require_tags={"safe"})
    enterprise_policy = ToolPolicy()

    await run_policy(
        "Free tier",
        policy=free_policy,
        catalog=nodes,
        registry=registry,
        responses=[
            {
                "thought": "Search public docs",
                "next_node": "search_public",
                "args": {"text": "onboarding"},
            },
            {
                "thought": "Summarise findings",
                "next_node": "summarise",
                "args": {"documents": ["Public insight about onboarding"]},
            },
            {
                "thought": "Respond",
                "next_node": None,
                "args": {"raw_answer": "Share public summary"},
            },
        ],
    )

    await run_policy(
        "Premium tier",
        policy=premium_policy,
        catalog=nodes,
        registry=registry,
        responses=[
            {
                "thought": "Search internal docs",
                "next_node": "search_internal",
                "args": {"text": "onboarding"},
            },
            {
                "thought": "Summarise",
                "next_node": "summarise",
                "args": {"documents": ["Internal note about onboarding"]},
            },
            {
                "thought": "Send email",
                "next_node": "send_email",
                "args": {"text": "Summary: Internal note about onboarding"},
            },
            {
                "thought": "Wrap up",
                "next_node": None,
                "args": {"raw_answer": "Email sent to stakeholders"},
            },
        ],
    )

    await run_policy(
        "Enterprise tier",
        policy=enterprise_policy,
        catalog=nodes,
        registry=registry,
        responses=[
            {
                "thought": "Try risky action",
                "next_node": "delete_user",
                "args": {"text": "onboarding"},
            },
            {
                "thought": "Summarise",
                "next_node": "summarise",
                "args": {"documents": ["Deleted user tied to onboarding"]},
            },
            {
                "thought": "Wrap up",
                "next_node": None,
                "args": {"raw_answer": "Risky operation executed"},
            },
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
