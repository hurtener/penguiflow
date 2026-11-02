"""Check final-call token aggregation with the real, optional MLflow SDK."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_native_and_litellm_tokens_are_stored_before_run_returns(tmp_path: Path) -> None:
    pytest.importorskip("mlflow", minversion="3.8.1")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("alembic")
    # Isolate MLflow's process-wide tracking state from other tests.
    script = r"""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import mlflow
from penguiflow.llm.protocol import NativeLLMAdapter
from penguiflow.llm.types import CompletionResponse, LLMMessage, TextPart, Usage
from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.planner.llm import _LiteLLMJSONClient

mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("token-aggregation")
provider = SimpleNamespace(
    model="test-model", provider_name="test", profile=MagicMock(),
    complete=AsyncMock(return_value=CompletionResponse(
        message=LLMMessage(role="assistant", parts=[TextPart(text='{"ok": true}')]),
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )),
)
litellm = SimpleNamespace(acompletion=AsyncMock(return_value={
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 7},
}))

async def run():
    with patch("penguiflow.llm.protocol.create_provider", return_value=provider), \
         patch.dict(sys.modules, litellm=litellm):
        native = NativeLLMAdapter("test-model")
        legacy = _LiteLLMJSONClient("openai/test", temperature=0, json_schema_mode=False)
        async def execute(*args, **kwargs):
            nonlocal trace_id
            trace_id = mlflow.get_current_active_span().trace_id
            for client in (native, legacy):
                await client.complete(messages=[{"role": "user", "content": "private prompt"}])
            return PlannerFinish(reason="answer_complete", payload="done", metadata={})

        trace_id = None
        with patch("penguiflow.planner.react._run_impl", execute):
            await ReactPlanner(catalog=[], llm_client=native).run("query")
        trace = mlflow.get_trace(trace_id)  # No sleep or callback flush.
        spans = trace.data.spans
        root = next(span for span in spans if span.parent_id is None)
        calls = [span for span in spans if span.span_type == "LLM"]
        assert len(spans) == 3 and len(calls) == 2
        assert all(span.parent_id == root.span_id for span in calls)
        assert all(not span.inputs and not span.outputs for span in spans)
        assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"]) == {
            "input_tokens": 30, "output_tokens": 12, "total_tokens": 42,
        }

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, f"sqlite:///{tmp_path / 'mlflow.db'}"],
        env={**os.environ, "PENGUIFLOW_LLM_TRACING": "mlflow", "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "false"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
