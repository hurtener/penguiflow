"""Streaming tests for playground backend."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterable

import httpx
import pytest

from penguiflow.cli.playground import create_playground_app
from penguiflow.cli.playground_state import InMemoryStateStore
from penguiflow.cli.playground_wrapper import PlannerAgentWrapper
from penguiflow.planner import PlannerEvent, PlannerFinish


def _parse_sse(lines: Iterable[str | bytes]) -> list[tuple[str | None, dict[str, object]]]:
    events: list[tuple[str | None, dict[str, object]]] = []
    block: list[str] = []
    for raw in lines:
        line = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        if line == "":
            if not block:
                continue
            event_name: str | None = None
            data_lines: list[str] = []
            for entry in block:
                if entry.startswith("event:"):
                    event_name = entry.split(":", 1)[1].strip()
                elif entry.startswith("data:"):
                    data_lines.append(entry.split(":", 1)[1].strip())
            payload = json.loads("".join(data_lines)) if data_lines else {}
            events.append((event_name, payload))
            block = []
        else:
            block.append(line)
    return events


async def _enumerate_async(aiterable: AsyncIterator[str]) -> AsyncIterator[tuple[int, str]]:
    idx = 0
    async for item in aiterable:
        yield idx, item
        idx += 1


class _StreamingPlanner:
    def __init__(self) -> None:
        self._event_callback = None

    async def run(self, query: str, *, llm_context, tool_context) -> PlannerFinish:  # type: ignore[override]
        del llm_context, tool_context
        if self._event_callback:
            self._event_callback(
                PlannerEvent(
                    event_type="stream_chunk",
                    ts=time.time(),
                    trajectory_step=0,
                    extra={"stream_id": "answer", "seq": 0, "text": f"echo:{query}", "done": False},
                )
            )
            self._event_callback(
                PlannerEvent(
                    event_type="step_complete",
                    ts=time.time(),
                    trajectory_step=0,
                    thought="complete",
                    node_name="answer",
                )
            )
        return PlannerFinish(
            reason="answer_complete",
            payload={"answer": f"echo:{query}"},
            metadata={"steps": []},
        )


@pytest.mark.asyncio
async def test_chat_stream_emits_events_and_done() -> None:
    store = InMemoryStateStore()
    agent = PlannerAgentWrapper(_StreamingPlanner(), state_store=store)
    app = create_playground_app(agent=agent, state_store=store)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream("GET", "/chat/stream", params={"query": "hi", "session_id": "sess-1"}) as response:
            raw_lines = [line async for line in response.aiter_lines()]

    events = _parse_sse(raw_lines)
    assert any(name == "chunk" for name, _ in events)
    done = next((payload for name, payload in events if name == "done"), None)
    assert done is not None
    trace_id = done["trace_id"]
    trajectory = await store.get_trajectory(trace_id, "sess-1")
    assert trajectory is not None
    assert trajectory.query == "hi"


@pytest.mark.asyncio
async def test_events_endpoint_replays_history() -> None:
    store = InMemoryStateStore()
    agent = PlannerAgentWrapper(_StreamingPlanner(), state_store=store)
    app = create_playground_app(agent=agent, state_store=store)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post("/chat", json={"query": "hello", "session_id": "sess-2"})
        assert chat_response.status_code == 200
        trace_id = chat_response.json()["trace_id"]

        async with client.stream(
            "GET", "/events", params={"trace_id": trace_id, "session_id": "sess-2"}
        ) as stream:
            lines: list[str] = []
            async for idx, line in _enumerate_async(stream.aiter_lines()):
                lines.append(line)
                if idx >= 2:
                    break

        events = _parse_sse(lines)
        assert events
        assert all(payload.get("trace_id") == trace_id for _, payload in events if payload)

        trajectory_response = await client.get(f"/trajectory/{trace_id}", params={"session_id": "sess-2"})
        assert trajectory_response.status_code == 200
        assert trajectory_response.json()["trace_id"] == trace_id
