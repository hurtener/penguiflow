"""Short-term memory example: persist memory in a Redis-like store."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    DefaultShortTermMemory,
    MemoryKey,
    ShortTermMemoryConfig,
)
from penguiflow.planner.models import JSONLLMClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class FakeRedis:
    """In-memory stand-in for a Redis client."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        """Return the value stored under ``key``, if any."""
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self._kv[key] = value


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


async def handle_turn(
    redis: FakeRedis, *, key: MemoryKey, user_message: str, answer: str
) -> dict[str, Any]:
    """Handle one conversation turn."""
    memory = DefaultShortTermMemory(
        config=ShortTermMemoryConfig(strategy="truncation")
    )
    stored = await redis.get(key.composite())
    if stored:
        memory.from_dict(json.loads(stored))

    client = ScriptedLLMClient([answer])
    planner = ReactPlanner(
        llm_client=client, catalog=[], short_term_memory=memory
    )
    await planner.run(user_message, memory_key=key)

    await redis.set(
        key.composite(), json.dumps(memory.to_dict(), ensure_ascii=False)
    )

    memory_context = _extract_memory_from_messages(client.calls[0])
    return {"llm_prompt_context": {"conversation_memory": memory_context}}


async def run_demo() -> dict[str, Any]:
    """Run the demo end to end."""
    redis = FakeRedis()
    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")

    await handle_turn(redis, key=key, user_message="q1", answer="a1")
    return await handle_turn(redis, key=key, user_message="q2", answer="a2")


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
