"""Short-term memory example: react to summaries through callbacks."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import (
    ConversationTurn,
    DefaultShortTermMemory,
    MemoryBudget,
    MemoryHealth,
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
        answer = self._answers.pop(0)
        return json.dumps(
            {
                "thought": "finish",
                "next_node": None,
                "args": {"raw_answer": answer},
            }
        ), 0.0


async def run_demo() -> dict[str, Any]:
    """Run the demo end to end."""
    events: dict[str, list[Any]] = {"turns": [], "summaries": [], "health": []}

    async def on_turn(turn: ConversationTurn) -> None:
        events["turns"].append(
            {"user": turn.user_message, "assistant": turn.assistant_response}
        )

    async def on_summary(old: str, new: str) -> None:
        events["summaries"].append({"old": old, "new": new})

    async def on_health(old: MemoryHealth, new: MemoryHealth) -> None:
        events["health"].append({"old": old.value, "new": new.value})

    fail_first = {"done": False}

    async def flaky_summarizer(
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not fail_first["done"]:
            fail_first["done"] = True
            msg = "simulated summarizer failure"
            raise RuntimeError(msg)
        turns = payload.get("turns") or []
        return {
            "summary": f"<session_summary>summarized {len(turns)} "
            "turn(s)</session_summary>"
        }

    config = ShortTermMemoryConfig(
        strategy="rolling_summary",
        budget=MemoryBudget(full_zone_turns=1, total_max_tokens=8000),
        retry_attempts=2,
        retry_backoff_base_s=0.0,
        degraded_retry_interval_s=0.0,
        on_turn_added=on_turn,
        on_summary_updated=on_summary,
        on_health_changed=on_health,
    )
    memory = DefaultShortTermMemory(config=config, summarizer=flaky_summarizer)

    client = ScriptedLLMClient(["a1", "a2"])
    planner = ReactPlanner(
        llm_client=client, catalog=[], short_term_memory=memory
    )

    key = MemoryKey(tenant_id="demo", user_id="user", session_id="session")
    await planner.run("q1", memory_key=key)
    await planner.run("q2", memory_key=key)
    await memory.flush()
    await asyncio.sleep(0)

    return events


def main() -> None:
    """Run the example."""
    payload = asyncio.run(run_demo())
    sys.stdout.write(f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n")


if __name__ == "__main__":
    main()
