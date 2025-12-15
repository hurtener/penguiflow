from __future__ import annotations

import pytest

from examples.memory_basic.flow import run_demo as run_basic
from examples.memory_callbacks.flow import run_demo as run_callbacks
from examples.memory_custom.flow import run_demo as run_custom
from examples.memory_persistence.flow import run_demo as run_persistence
from examples.memory_redis.flow import run_demo as run_redis
from examples.memory_truncation.flow import run_demo as run_truncation


@pytest.mark.asyncio
async def test_memory_basic_injects_previous_turn() -> None:
    payload = await run_basic()
    recent = payload["conversation_memory"]["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio
async def test_memory_truncation_keeps_last_two_turns() -> None:
    payload = await run_truncation()
    recent = payload["recent_turns"]
    assert [t["user"] for t in recent] == ["q2", "q3"]


@pytest.mark.asyncio
async def test_memory_persistence_hydrates_from_state_store() -> None:
    payload = await run_persistence()
    recent = payload["conversation_memory"]["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio
async def test_memory_redis_serialization_roundtrip() -> None:
    payload = await run_redis()
    recent = payload["llm_prompt_context"]["conversation_memory"]["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"


@pytest.mark.asyncio
async def test_memory_callbacks_emit_events() -> None:
    payload = await run_callbacks()
    assert payload["turns"] == [{"user": "q1", "assistant": "a1"}, {"user": "q2", "assistant": "a2"}]
    assert payload["summaries"]
    assert any(item["new"].startswith("<session_summary>") for item in payload["summaries"])
    assert payload["health"]


@pytest.mark.asyncio
async def test_memory_custom_injects_turns() -> None:
    payload = await run_custom()
    recent = payload["conversation_memory"]["recent_turns"]
    assert recent[0]["user"] == "q1"
    assert recent[0]["assistant"] == "a1"
