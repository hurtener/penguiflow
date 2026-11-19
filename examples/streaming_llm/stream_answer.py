"""Stream partial ReactPlanner answers through PlannerEvent callbacks."""

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
from penguiflow.planner import PlannerEvent, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext


class Query(BaseModel):
    """Query payload."""

    question: str


class Answer(BaseModel):
    """Answer payload."""

    answer: str


@tool(desc="Stream answer token-by-token")
async def stream_answer(_args: Query, ctx: ToolContext) -> Answer:
    """Emit streaming chunks while assembling the final answer."""
    full_text = (
        "PenguiFlow is a lightweight async agent orchestrator with "
        "typed nodes, "
        "reliable retries, and a JSON-only ReAct planner."
    )
    tokens = full_text.split()
    stream_id = "answer_stream"

    for index, token in enumerate(tokens):
        await ctx.emit_chunk(stream_id, index, f"{token} ", done=False)
        await asyncio.sleep(0.05)

    await ctx.emit_chunk(stream_id, len(tokens), "", done=True)
    return Answer(answer=full_text)


class SequenceLLM(JSONLLMClient):
    """Deterministic stub that returns canned planner actions."""

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
    registry.register("stream_answer", Query, Answer)

    nodes = [Node(stream_answer, name="stream_answer")]

    client = SequenceLLM(
        [
            {
                "thought": "stream answer",
                "next_node": "stream_answer",
                "args": {"question": "What is PenguiFlow?"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {
                    "raw_answer": "PenguiFlow is a lightweight "
                    "agent orchestrator."
                },
            },
        ]
    )

    def stream_logger(event: PlannerEvent) -> None:
        if event.event_type == "stream_chunk":
            sys.stdout.write(f"{event.extra['text']}")
            sys.stdout.flush()
            if event.extra["done"]:
                sys.stdout.write("\n")

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        event_callback=stream_logger,
    )

    sys.stdout.write("Streaming answer: ")
    result = await planner.run("Describe PenguiFlow")
    sys.stdout.write(
        f"\nFinal payload: {json.dumps(result.payload, ensure_ascii=False)}\n"
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
