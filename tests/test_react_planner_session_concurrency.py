"""Tests for ReAct planner session concurrency."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _BarrierClient(JSONLLMClient):
    """LLM client that blocks the first call until released."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.allow_first_finish = asyncio.Event()

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        call_index = self.calls
        self.calls += 1
        if call_index == 0:
            self.first_started.set()
            await self.allow_first_finish.wait()
        elif call_index == 1:
            self.second_started.set()
        payload = {
            "thought": "finish",
            "next_node": "final_response",
            "args": {"raw_answer": f"ok-{call_index}"},
        }
        return json.dumps(payload), 0.0


def _make_minimal_planner(client: _BarrierClient) -> ReactPlanner:
    registry = ModelRegistry()

    class _In(BaseModel):
        text: str

    class _Out(BaseModel):
        text: str

    async def noop(inp: _In, _ctx: object) -> _Out:
        return _Out(text=inp.text)

    registry.register("noop", _In, _Out)
    catalog = build_catalog([Node(noop, name="noop")], registry)
    return ReactPlanner(llm_client=client, catalog=catalog, max_iters=1)


@pytest.mark.asyncio
async def test_shared_planner_serializes_same_session() -> None:
    """Shared planner serializes same session."""
    client = _BarrierClient()
    planner = _make_minimal_planner(client)

    async def _run() -> object:
        return await planner.run("hi", tool_context={"session_id": "s1"})

    t1 = asyncio.create_task(_run())
    await client.first_started.wait()

    t2 = asyncio.create_task(_run())
    # Second call should not start while the first is blocked (same
    # session_id).
    await asyncio.sleep(0)
    assert not client.second_started.is_set()

    client.allow_first_finish.set()
    await asyncio.gather(t1, t2)
    assert client.second_started.is_set()


@pytest.mark.asyncio
async def test_shared_planner_allows_concurrent_different_sessions() -> None:
    """Shared planner allows concurrent different sessions."""
    client = _BarrierClient()
    planner = _make_minimal_planner(client)

    async def _run(session_id: str) -> object:
        return await planner.run("hi", tool_context={"session_id": session_id})

    t1 = asyncio.create_task(_run("s1"))
    await client.first_started.wait()

    t2 = asyncio.create_task(_run("s2"))
    # Second call should start even while first is blocked (different
    # session_id).
    await asyncio.wait_for(client.second_started.wait(), timeout=1.0)

    client.allow_first_finish.set()
    await asyncio.gather(t1, t2)
