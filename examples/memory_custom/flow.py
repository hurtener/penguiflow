"""Short-term memory example: plug in a custom memory implementation."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    ConversationTurn,
    MemoryHealth,
    MemoryKey,
    ShortTermMemory,
    default_token_estimator,
)
from penguiflow.planner.models import JSONLLMClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


@dataclass
class WindowShortTermMemory(ShortTermMemory):
    """Short-term memory that keeps only the last few turns."""

    max_turns: int = 3
    _turns: list[ConversationTurn] = field(default_factory=list)

    @property
    @override
    def health(self) -> MemoryHealth:
        return MemoryHealth.HEALTHY

    @override
    async def add_turn(self, turn: ConversationTurn) -> None:
        self._turns.append(turn)
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    @override
    async def get_llm_context(self) -> Mapping[str, Any]:
        return {
            "conversation_memory": {
                "recent_turns": [
                    {"user": t.user_message, "assistant": t.assistant_response}
                    for t in self._turns
                ],
            }
        }

    @override
    def estimate_tokens(self) -> int:
        return default_token_estimator(
            json.dumps({"turns": [t.user_message for t in self._turns]})
        )

    @override
    async def flush(self) -> None:
        return


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
    memory = WindowShortTermMemory(max_turns=2)
    client = ScriptedLLMClient(["a1", "a2"])
    planner = ReactPlanner(
        llm_client=client, catalog=[], short_term_memory=memory
    )

    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")
    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)

    return {
        "conversation_memory": _extract_memory_from_messages(client.calls[1])
    }


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
