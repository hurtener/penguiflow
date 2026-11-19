"""ReactPlanner example that replans after a remote failure."""

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


class Answer(BaseModel):
    """Answer payload."""

    answer: str


class RemoteTimeoutError(RuntimeError):
    """Exception carrying a suggestion for the planner."""

    def __init__(self, message: str, suggestion: str) -> None:
        """Store ``message`` and ``suggestion``."""
        super().__init__(message)
        self.suggestion = suggestion


@tool(desc="Detect the caller intent", tags=["planner"])
async def triage(_args: Question, _ctx: object) -> Intent:
    """Classify the question into an intent."""
    return Intent(intent="docs")


@tool(desc="Call remote retriever", side_effects="external")
async def remote_docs(_args: Intent, _ctx: object) -> Documents:
    """Fetch documents from the remote source."""
    msg = "remote search timed out"
    raise RemoteTimeoutError(msg, "use_cached_index")


@tool(desc="Fallback to cached index", side_effects="read")
async def cached_docs(args: Intent, _ctx: object) -> Documents:
    """Return documents from the local cache."""
    return Documents(documents=[f"Cached snippet covering {args.intent}"])


@tool(desc="Compose final answer")
async def summarise(args: Answer, _ctx: object) -> Answer:
    """Summarise the retrieved documents."""
    return args


class SequenceLLM(JSONLLMClient):
    """Deterministic stub returning authored planner actions."""

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
                "args": {
                    "raw_answer": "Cached docs describe the latest metrics."
                },
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        hop_budget=3,
    )

    result = await planner.run("Summarise metrics with fallback")
    sys.stdout.write(
        f"{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
