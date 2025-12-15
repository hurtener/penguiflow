from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import DefaultShortTermMemory, MemoryKey, ShortTermMemoryConfig


class FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value


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


async def handle_turn(redis: FakeRedis, *, key: MemoryKey, user_message: str, answer: str) -> dict[str, Any]:
    memory = DefaultShortTermMemory(config=ShortTermMemoryConfig(strategy="truncation"))
    stored = await redis.get(key.composite())
    if stored:
        memory.from_dict(json.loads(stored))

    client = ScriptedLLMClient([answer])
    planner = ReactPlanner(llm_client=client, catalog=[], short_term_memory=memory)
    await planner.run(user_message, memory_key=key)

    await redis.set(key.composite(), json.dumps(memory.to_dict(), ensure_ascii=False))

    memory_context = _extract_memory_from_messages(client.calls[0])
    return {"llm_prompt_context": {"conversation_memory": memory_context}}


async def run_demo() -> dict[str, Any]:
    redis = FakeRedis()
    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")

    await handle_turn(redis, key=key, user_message="q1", answer="a1")
    out = await handle_turn(redis, key=key, user_message="q2", answer="a2")
    return out


def main() -> None:
    payload = asyncio.run(run_demo())
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
