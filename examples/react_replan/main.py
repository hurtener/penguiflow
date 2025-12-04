from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry


class Question(BaseModel):
    text: str


class Intent(BaseModel):
    intent: str


class Documents(BaseModel):
    documents: list[str]


class Answer(BaseModel):
    answer: str


class RemoteTimeout(RuntimeError):
    """Exception carrying a suggestion for the planner."""

    def __init__(self, message: str, suggestion: str) -> None:
        super().__init__(message)
        self.suggestion = suggestion


@tool(desc="Detect the caller intent", tags=["planner"])
async def triage(args: Question, ctx: object) -> Intent:
    return Intent(intent="docs")


@tool(desc="Call remote retriever", side_effects="external")
async def remote_docs(args: Intent, ctx: object) -> Documents:
    raise RemoteTimeout("remote search timed out", "use_cached_index")


@tool(desc="Fallback to cached index", side_effects="read")
async def cached_docs(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"Cached snippet covering {args.intent}"])


@tool(desc="Compose final answer")
async def summarise(args: Answer, ctx: object) -> Answer:
    return args


class SequenceLLM:
    """Deterministic stub returning authored planner actions."""

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
    registry.register("triage", Question, Intent)
    registry.register("remote_docs", Intent, Documents)
    registry.register("cached_docs", Intent, Documents)
    registry.register("summarise", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(remote_docs, name="remote_docs"),
        Node(cached_docs, name="cached_docs"),
        Node(summarise, name="summarise"),
    ]

    client = SequenceLLM(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"text": "Summarise latest metrics"},
            },
            {
                "thought": "try remote",
                "next_node": "remote_docs",
                "args": {"intent": "docs"},
            },
            {
                "thought": "fallback cache",
                "next_node": "cached_docs",
                "args": {"intent": "docs"},
            },
            {
                "thought": "wrap up",
                "next_node": "summarise",
                "args": {"answer": "Used cached docs after timeout."},
            },
            {
                "thought": "final",
                "next_node": None,
                "args": {"raw_answer": "Cached docs describe the latest metrics."},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        hop_budget=3,
    )

    result = await planner.run("Summarise metrics with fallback")
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
