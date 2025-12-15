from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import MemoryBudget, MemoryKey, ShortTermMemoryConfig
from penguiflow.state import RemoteBinding, StoredEvent


class DictStateStore:
    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    async def save_event(self, event: StoredEvent) -> None:
        del event

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        del trace_id
        return []

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        del binding

    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
        self._memory[key] = state

    async def load_memory_state(self, key: str) -> dict[str, Any] | None:
        return self._memory.get(key)


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


async def run_demo() -> dict[str, Any]:
    store = DictStateStore()
    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")

    planner1 = ReactPlanner(
        llm_client=ScriptedLLMClient(["a1"]),
        catalog=[],
        state_store=store,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    await planner1.run("q1", memory_key=key)

    client2 = ScriptedLLMClient(["a2"])
    planner2 = ReactPlanner(
        llm_client=client2,
        catalog=[],
        state_store=store,
        short_term_memory=ShortTermMemoryConfig(
            strategy="truncation",
            budget=MemoryBudget(full_zone_turns=5),
        ),
    )
    await planner2.run("q2", memory_key=key)

    injected = json.loads(next(msg["content"] for msg in client2.calls[0] if msg["role"] == "user")).get("context", {})
    return {"conversation_memory": injected.get("conversation_memory", {})}


def main() -> None:
    payload = asyncio.run(run_demo())
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
