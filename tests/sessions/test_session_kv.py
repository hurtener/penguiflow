"""Tests for session KV."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import (
    PlannerTaskPipeline,
    StreamingSession,
    TaskContextSnapshot,
    TaskType,
)
from penguiflow.sessions.models import UpdateType
from penguiflow.sessions.session_kv import SessionKVFacade
from penguiflow.state import InMemoryStateStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext


class KVArgs(BaseModel):
    """Arguments of the kv tool."""

    value: str = "STEP_1"


class KVOut(BaseModel):
    """Output of the kv tool."""

    ok: bool = True


@tool(desc="Write session KV state.", side_effects="write")
async def kv_tool(args: KVArgs, ctx: ToolContext) -> KVOut:
    """Store a value in the session KV store."""
    await ctx.kv.set("state", {"status": args.value, "token": "secret"})
    return KVOut(ok=True)


class KVClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.calls = 0

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                '{"thought":"kv","next_node":"kv_tool",'
                '"args":{"value":"STEP_1"}}'
            )
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


def _planner_factory(store: InMemoryStateStore) -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("kv_tool", KVArgs, KVOut)
    catalog = build_catalog([Node(kv_tool, name="kv_tool")], registry)
    return ReactPlanner(
        llm_client=KVClient(), catalog=catalog, max_iters=3, state_store=store
    )


@pytest.mark.asyncio
async def test_session_kv_emits_checkpoint_update_via_pipeline() -> None:
    """Session KV emits checkpoint update via pipeline."""
    store = InMemoryStateStore()
    session = StreamingSession("kv-session", state_store=store)
    pipeline = PlannerTaskPipeline(
        planner_factory=lambda: _planner_factory(store)
    )
    snapshot = TaskContextSnapshot(
        session_id="kv-session",
        task_id="foreground",
        tool_context={"tenant_id": "tenant", "user_id": "user"},
    )
    task_id = await session.spawn_task(
        pipeline,
        task_type=TaskType.FOREGROUND,
        context_snapshot=snapshot,
        query="kv",
    )

    updates_iter = await session.subscribe(task_ids=[task_id])

    async def _wait_for_kv_checkpoint() -> dict[str, object]:
        async for update in updates_iter:
            if (
                update.update_type == UpdateType.CHECKPOINT
                and isinstance(update.content, dict)
                and update.content.get("kind") == "kv_set"
            ):
                return update.content
        msg = "no_kv_checkpoint"
        raise RuntimeError(msg)

    content = await asyncio.wait_for(_wait_for_kv_checkpoint(), timeout=2.0)
    assert content["scope"] == "session"
    assert content["namespace"] == "tool:kv_tool"
    assert content["key"] == "state"
    assert content["prev"] is None
    next_value = content["next"]
    assert isinstance(next_value, dict)
    assert next_value["inline"]["status"] == "STEP_1"
    # Redaction applies to updates
    assert next_value["inline"]["token"] == "<redacted>"

    # Persisted memory state is in the reserved keyspace and has no TTL for
    # session scope.
    kv_key = "kv:v1:tenant:user:kv-session:session:tool:kv_tool:state"
    persisted = await store.load_memory_state(kv_key)
    assert persisted is not None
    assert "expires_at" not in persisted


@pytest.mark.asyncio
async def test_session_kv_task_scope_has_fixed_ttl_and_expires() -> None:
    """Session KV task scope has fixed TTL and expires."""
    store = InMemoryStateStore()
    artifacts = InMemoryArtifactStore()
    tool_context = {
        "tenant_id": "t",
        "user_id": "u",
        "session_id": "s",
        "task_id": "task-1",
        "trace_id": "trace-1",
        "_current_tool_name": "my_tool",
        "_current_tool_call_id": "call-1",
    }
    kv = SessionKVFacade(
        state_store=store, artifacts=artifacts, tool_context=tool_context
    )
    await kv.set("state", {"x": 1}, scope="task")

    kv_key = "kv:v1:t:u:s:task:task-1:tool:my_tool:state"
    persisted = await store.load_memory_state(kv_key)
    assert persisted is not None
    assert isinstance(persisted.get("expires_at"), str)

    # Force expiry
    persisted["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    await store.save_memory_state(kv_key, persisted)
    assert await kv.get("state", scope="task") is None


@pytest.mark.asyncio
async def test_session_kv_spills_large_values_to_artifacts_and_redacts() -> (
    None
):
    """Session KV spills large values to artifacts and redacts."""
    store = InMemoryStateStore()
    artifacts = InMemoryArtifactStore()
    tool_context = {
        "tenant_id": "t",
        "user_id": "u",
        "session_id": "s",
        "task_id": "task-1",
        "trace_id": "trace-1",
        "_current_tool_name": "my_tool",
        "_current_tool_call_id": "call-1",
    }
    kv = SessionKVFacade(
        state_store=store, artifacts=artifacts, tool_context=tool_context
    )
    big = {"token": "secret", "blob": "a" * 10_000}
    await kv.set("big", big)

    kv_key = "kv:v1:t:u:s:session:tool:my_tool:big"
    persisted = await store.load_memory_state(kv_key)
    assert persisted is not None
    value = persisted.get("value")
    assert isinstance(value, dict)
    assert value.get("inline") is None
    ref = value.get("artifact_ref")
    assert isinstance(ref, dict)
    artifact_id = ref.get("id")
    assert isinstance(artifact_id, str) and artifact_id
    data = await artifacts.get(artifact_id)
    assert data is not None
    payload = json.loads(data.decode("utf-8"))
    assert payload["token"] == "<redacted>"
