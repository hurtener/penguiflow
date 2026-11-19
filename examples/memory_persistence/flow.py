"""Short-term memory example: persist and hydrate memory state."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    MemoryBudget,
    MemoryKey,
    ShortTermMemoryConfig,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.state import StateStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.state import RemoteBinding, StoredEvent


class DictStateStore(StateStore):
    """State store that keeps only memory state, in a dict."""

    def __init__(self) -> None:
        """Start with no saved memory state."""
        self._memory: dict[str, dict[str, Any]] = {}

    @override
    async def save_event(self, event: StoredEvent) -> None:
        """Drop the event; this example keeps no audit log."""

    @override
    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        return []

    @override
    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Drop the binding; this example calls no remote agents."""

    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
        """Store ``state`` under ``key``."""
        self._memory[key] = state

    async def load_memory_state(self, key: str) -> dict[str, Any] | None:
        """Return the state stored under ``key``, if any."""
        return self._memory.get(key)


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

    return {
        "conversation_memory": _extract_memory_from_messages(client2.calls[0])
    }


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
