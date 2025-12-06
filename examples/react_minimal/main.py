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


class FinalAnswer(BaseModel):
    answer: str


@tool(desc="Detect the caller intent", tags=["planner"])
async def triage(args: Question, ctx: object) -> Intent:
    return Intent(intent="docs")


@tool(desc="Retrieve supporting documents", side_effects="read")
async def retrieve(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"PenguiFlow remains lightweight for {args.intent}"])


@tool(desc="Summarise retrieved documents")
async def summarise(args: FinalAnswer, ctx: object) -> FinalAnswer:
    return args


class SequenceLLM:
    """Deterministic stub that returns pre-authored planner actions."""

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
    registry.register("retrieve", Intent, Documents)
    registry.register("summarise", FinalAnswer, FinalAnswer)

    nodes = [
        Node(triage, name="triage"),
        Node(retrieve, name="retrieve"),
        Node(summarise, name="summarise"),
    ]

    client = SequenceLLM(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"text": "How does PenguiFlow stay lightweight?"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "wrap up",
                "next_node": None,
                "args": {
                    "raw_answer": "PenguiFlow uses async orchestration and minimal deps."
                },
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("Explain PenguiFlow's lightweight design")
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
