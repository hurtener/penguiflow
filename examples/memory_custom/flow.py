from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    ConversationTurn,
    MemoryHealth,
    MemoryKey,
    ShortTermMemory,
    default_token_estimator,
)


@dataclass
class WindowShortTermMemory(ShortTermMemory):
    max_turns: int = 3
    _turns: list[ConversationTurn] = field(default_factory=list)

    @property
    def health(self) -> MemoryHealth:
        return MemoryHealth.HEALTHY

    async def add_turn(self, turn: ConversationTurn) -> None:
        self._turns.append(turn)
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    async def get_llm_context(self) -> Mapping[str, Any]:
        return {
            "conversation_memory": {
                "recent_turns": [{"user": t.user_message, "assistant": t.assistant_response} for t in self._turns],
            }
        }

    def estimate_tokens(self) -> int:
        return default_token_estimator(json.dumps({"turns": [t.user_message for t in self._turns]}))

    async def flush(self) -> None:
        return


class ScriptedLLMClient:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Any = None,
    ) -> tuple[str, float]:
        del response_format, stream, on_stream_chunk
        self.calls.append(list(messages))
        answer = self._answers.pop(0)
        return json.dumps({"thought": "finish", "next_node": None, "args": {"raw_answer": answer}}), 0.0


def _extract_memory_from_messages(messages: list[Mapping[str, str]]) -> dict[str, Any]:
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        if "<read_only_conversation_memory_json>" not in content:
            continue
        if "</read_only_conversation_memory_json>" not in content:
            continue
        start = content.index("<read_only_conversation_memory_json>") + len("<read_only_conversation_memory_json>")
        end = content.index("</read_only_conversation_memory_json>", start)
        return json.loads(content[start:end])
    return {}


async def run_demo() -> dict[str, Any]:
    memory = WindowShortTermMemory(max_turns=2)
    client = ScriptedLLMClient(["a1", "a2"])
    planner = ReactPlanner(llm_client=client, catalog=[], short_term_memory=memory)

    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")
    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)

    return {"conversation_memory": _extract_memory_from_messages(client.calls[1])}


def main() -> None:
    payload = asyncio.run(run_demo())
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
