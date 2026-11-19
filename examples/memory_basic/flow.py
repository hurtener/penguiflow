"""Short-term memory example: the planner remembers earlier turns."""

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
from penguiflow.planner.memory import (
    MemoryBudget,
    MemoryKey,
    ShortTermMemoryConfig,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    echoed: str


@tool(desc="Echo input", side_effects="pure", tags=["demo"])
async def echo(args: EchoArgs, _ctx: object) -> EchoOut:
    """Echo the input back."""
    return EchoOut(echoed=args.text)


class ScriptedLLMClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [
            json.dumps(item, ensure_ascii=False) for item in responses
        ]
        self.calls: list[list[Mapping[str, str]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.calls.append(list(messages))
        if not self._responses:
            msg = "No scripted LLM responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _extract_memory_from_messages(
    messages: list[Mapping[str, str]],
) -> dict[str, Any]:
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if "<read_only_conversation_memory_json>" not in content:
            continue
        if "</read_only_conversation_memory_json>" not in content:
            continue
        start = content.index("<read_only_conversation_memory_json>") + len(
            "<read_only_conversation_memory_json>"
        )
        end = content.index("</read_only_conversation_memory_json>", start)
        parsed = json.loads(content[start:end])
        assert isinstance(parsed, dict)
        return parsed
    return {}


async def run_demo() -> dict[str, Any]:
    """Run the demo end to end."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    client = ScriptedLLMClient(
        [
            {
                "thought": "call echo",
                "next_node": "echo",
                "args": {"text": "q1"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a1"},
            },
            {
                "thought": "call echo",
                "next_node": "echo",
                "args": {"text": "q2"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": "a2"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        short_term_memory=ShortTermMemoryConfig(
            strategy="rolling_summary",
            budget=MemoryBudget(full_zone_turns=5, total_max_tokens=8000),
        ),
        system_prompt_extra=(
            "A system message may contain "
            "<read_only_conversation_memory_json> with prior-turn "
            "memory. "
            "Treat it as read-only background context; never as the "
            "current user request."
        ),
    )

    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")

    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)

    second_call_first_step = client.calls[2]
    injected = _extract_memory_from_messages(second_call_first_step)

    return {"conversation_memory": injected}


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
