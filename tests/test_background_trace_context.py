"""Background work must not extend an already-exported MLflow trace."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path

import pytest

from penguiflow._tracing_context import detached_trace_context


def test_detached_context_without_optional_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_without_otel(name: str, *args, **kwargs):
        if name == "opentelemetry":
            raise ImportError("optional dependency is absent")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_otel)
    tenant = ContextVar("tenant", default="missing")
    tenant.set("tenant-1")
    assert detached_trace_context().run(tenant.get) == "tenant-1"


def test_background_memory_and_subagent_have_independent_traces(tmp_path: Path) -> None:
    pytest.importorskip("mlflow", minversion="3.18.0")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("alembic")
    script = r"""
import asyncio
import json
import sys
from contextvars import ContextVar

import mlflow
from mlflow.tracing import provider
from opentelemetry import baggage

from penguiflow.planner.memory import (
    ConversationTurn, DefaultShortTermMemory, MemoryBudget, MemoryHealth, ShortTermMemoryConfig,
)
from penguiflow.sessions import StreamingSession, TaskResult, TaskType

mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("background-traces")
tenant = ContextVar("tenant", default="missing")

def llm_call(name):
    assert tenant.get() == "tenant-1"
    assert baggage.get_baggage("request") == "request-1"
    if hasattr(provider, "mlflow_runtime_context"):
        mlflow_context = provider.mlflow_runtime_context.get_current()
        assert baggage.get_baggage("mlflow-request", context=mlflow_context) == "request-2"
    assert mlflow.get_current_active_span() is None
    with mlflow.start_span(name=name, span_type="LLM") as span:
        span.set_attribute("mlflow.chat.tokenUsage", {
            "input_tokens": 13, "output_tokens": 7, "total_tokens": 20,
        })
        return span.trace_id

async def run():
    from opentelemetry import context
    context.attach(baggage.set_baggage("request", "request-1"))
    if hasattr(provider, "mlflow_runtime_context"):
        provider.mlflow_runtime_context.attach(baggage.set_baggage("mlflow-request", "request-2"))
    tenant.set("tenant-1")
    for recovering in (False, True):
        gate = asyncio.Event()
        child_ids = []
        fail = recovering

        async def summarize(_payload):
            nonlocal fail
            if fail:
                fail = False
                raise RuntimeError("transient summarizer failure")
            await gate.wait()
            child_ids.append(llm_call("memory.summary"))
            return {"summary": "summary"}

        memory = DefaultShortTermMemory(
            config=ShortTermMemoryConfig(
                strategy="rolling_summary", budget=MemoryBudget(full_zone_turns=0),
                retry_attempts=0, degraded_retry_interval_s=0,
            ),
            summarizer=summarize,
        )
        if recovering:
            await memory.add_turn(ConversationTurn(user_message="before", assistant_response="before"))
            await memory.flush()
            assert memory.health == MemoryHealth.DEGRADED
        with mlflow.start_span(name="agent.parent") as parent:
            parent_id = parent.trace_id
            await memory.add_turn(ConversationTurn(user_message="query", assistant_response="answer"))
            assert mlflow.get_current_active_span().span_id == parent.span_id
        assert len(mlflow.get_trace(parent_id).data.spans) == 1
        gate.set()
        await memory.flush()
        assert memory.health == MemoryHealth.HEALTHY
        assert len(child_ids) == 1 and child_ids[0] != parent_id
        trace = mlflow.get_trace(child_ids[0])
        assert len(trace.data.spans) == 1 and trace.data.spans[0].parent_id is None
        assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"])["total_tokens"] == 20
        assert len(mlflow.get_trace(parent_id).data.spans) == 1

    gate = asyncio.Event()
    session = StreamingSession("tracing-session")
    child_ids = []

    async def pipeline(_runtime):
        await gate.wait()
        child_ids.append(llm_call("background.subagent"))
        return TaskResult(payload="done")

    with mlflow.start_span(name="agent.foreground") as parent:
        parent_id = parent.trace_id
        task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="child")
        handle = session._task_handles[task_id]
        assert mlflow.get_current_active_span().span_id == parent.span_id
    assert len(mlflow.get_trace(parent_id).data.spans) == 1
    gate.set()
    await handle
    assert len(child_ids) == 1 and child_ids[0] != parent_id
    trace = mlflow.get_trace(child_ids[0])
    assert len(trace.data.spans) == 1 and trace.data.spans[0].parent_id is None
    assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"])["total_tokens"] == 20
    assert len(mlflow.get_trace(parent_id).data.spans) == 1

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, f"sqlite:///{tmp_path / 'mlflow.db'}"],
        cwd=tmp_path,
        env={**os.environ, "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "false"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
