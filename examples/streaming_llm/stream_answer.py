"""ReactPlanner example that streams partial responses via PlannerEvent callbacks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerEvent, ReactPlanner
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    question: str


class Answer(BaseModel):
    answer: str


@tool(desc="Stream answer token-by-token")
async def stream_answer(args: Query, ctx) -> Answer:
    """Emit streaming chunks while assembling the final answer."""

    full_text = (
        "PenguiFlow is a lightweight async agent orchestrator with typed nodes, "
        "reliable retries, and a JSON-only ReAct planner."
    )
    tokens = full_text.split()
    stream_id = "answer_stream"

    for index, token in enumerate(tokens):
        await ctx.emit_chunk(stream_id, index, f"{token} ", done=False)
        await asyncio.sleep(0.05)

    await ctx.emit_chunk(stream_id, len(tokens), "", done=True)
    return Answer(answer=full_text)


class SequenceLLM:
    """Deterministic stub that returns canned planner actions."""

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
                "args": {"raw_answer": "PenguiFlow is a lightweight agent orchestrator."},
            },
        ]
    )

    def stream_logger(event: PlannerEvent) -> None:
        if event.event_type == "stream_chunk":
            print(event.extra["text"], end="", flush=True)
            if event.extra["done"]:
                print()

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        event_callback=stream_logger,
    )

    print("Streaming answer: ", end="")
    result = await planner.run("Describe PenguiFlow")
    print(f"\nFinal payload: {json.dumps(result.payload, ensure_ascii=False)}")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
