from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import MemoryBudget, MemoryKey, ShortTermMemoryConfig


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
        if not self._answers:
            raise AssertionError("No scripted answers left")
        answer = self._answers.pop(0)
        return json.dumps({"thought": "finish", "next_node": None, "args": {"raw_answer": answer}}), 0.0


async def run_demo() -> dict[str, Any]:
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

    last_user_payload = json.loads(next(msg["content"] for msg in client.calls[-1] if msg["role"] == "user"))
    injected = last_user_payload.get("context", {}).get("conversation_memory", {})
    return {"recent_turns": injected.get("recent_turns", [])}


def main() -> None:
    payload = asyncio.run(run_demo())
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
