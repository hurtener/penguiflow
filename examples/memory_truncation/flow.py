"""Short-term memory example: truncate history to a token budget."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    MemoryBudget,
    MemoryKey,
    ShortTermMemoryConfig,
)
from penguiflow.planner.models import JSONLLMClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class ScriptedLLMClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, answers: list[str]) -> None:
        """Replay ``answers`` in order."""
        self._answers = list(answers)
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
        if not self._answers:
            msg = "No scripted answers left"
            raise AssertionError(msg)
        answer = self._answers.pop(0)
        return json.dumps(
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": answer},
            }
        ), 0.0


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
    client = ScriptedLLMClient(["a1", "a2", "a3", "a4"])
    planner = ReactPlanner(
        llm_client=client,
        catalog=[],
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=2),
        ),
    )
    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")

    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)
    await planner.run("q3", memory_key=key)
    await planner.run("q4", memory_key=key)

    injected = _extract_memory_from_messages(client.calls[-1])
    return {"recent_turns": injected.get("recent_turns", [])}


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
