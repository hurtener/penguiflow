"""Minimal ReactPlanner example: triage, retrieve and summarise."""

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
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class Question(BaseModel):
    """Question payload."""

    text: str


class Intent(BaseModel):
    """Intent payload."""

    intent: str


class Documents(BaseModel):
    """Documents payload."""

    documents: list[str]


class FinalAnswer(BaseModel):
    """Final answer payload."""

    answer: str


@tool(desc="Detect the caller intent", tags=["planner"])
async def triage(_args: Question, _ctx: object) -> Intent:
    """Classify the question into an intent."""
    return Intent(intent="docs")


@tool(desc="Retrieve supporting documents", side_effects="read")
async def retrieve(args: Intent, _ctx: object) -> Documents:
    """Return canned documents for the intent."""
    return Documents(
        documents=[f"PenguiFlow remains lightweight for {args.intent}"]
    )


@tool(desc="Summarise retrieved documents")
async def summarise(args: FinalAnswer, _ctx: object) -> FinalAnswer:
    """Summarise the retrieved documents."""
    return args


class SequenceLLM(JSONLLMClient):
    """Deterministic stub that returns pre-authored planner actions."""

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
                    "raw_answer": "PenguiFlow uses async "
                    "orchestration and minimal "
                    "deps."
                },
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
    )

    result = await planner.run("Explain PenguiFlow's lightweight design")
    sys.stdout.write(
        f"{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
