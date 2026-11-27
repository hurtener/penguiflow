"""Exercise real MLflow spans through the actual planner and LiteLLM response types."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["success", "parallel", "error", "pause", "cancel_llm", "no_path", "guardrail"])
def test_actual_planner_traces_with_real_sdks(tmp_path: Path, scenario: str) -> None:
    pytest.importorskip("mlflow", minversion="3.18.0")
    pytest.importorskip("litellm")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("alembic")
    # Isolate the SDKs' process-wide tracking and provider state between scenarios.
    script = r"""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import mlflow
from litellm import ModelResponse
from pydantic import BaseModel, ConfigDict

from penguiflow.catalog import build_catalog, tool
from penguiflow.llm.protocol import NativeLLMAdapter
from penguiflow.llm.types import CompletionResponse, LLMMessage, TextPart, Usage
from penguiflow.node import Node
from penguiflow.planner import PlannerPause, ReactPlanner
from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator, GuardrailGateway, RuleRegistry, ToolAllowlistRule,
)
from penguiflow.planner.llm import _LiteLLMJSONClient
from penguiflow.registry import ModelRegistry
from penguiflow.steering import InMemoryGuardInbox

mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("planner-integration")
scenario = sys.argv[2]

class Args(BaseModel):
    value: int

class Result(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: int = 1

async def run():
    trace_ids = []
    events = []
    payload = {}
    provider = SimpleNamespace(
        model="test-model", provider_name="test", profile=MagicMock(),
        complete=AsyncMock(return_value=CompletionResponse(
            message=LLMMessage(role="assistant", parts=[TextPart(text='{"ok": true}')]),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )),
    )
    with patch("penguiflow.llm.protocol.create_provider", return_value=provider):
        native = NativeLLMAdapter("test-model")

    @tool(desc="A tool whose response is application data")
    async def checked_tool(args: Args, ctx) -> Result:
        if scenario == "error":
            raise ValueError("tool failed")
        if scenario == "pause":
            await ctx.pause("approval_required", {"value": args.value})
        with mlflow.start_span(name="custom_inner_span"):
            await asyncio.sleep(0)
            await native.complete(messages=[{"role": "user", "content": "private inner prompt"}])
        return Result(**payload)

    registry = ModelRegistry()
    registry.register("checked_tool", Args, Result)
    catalog = build_catalog([Node(checked_tool, name="checked_tool")], registry)
    final = {"thought": "finish", "next_node": "final_response", "args": {"raw_answer": "done"}}
    call = {"thought": "call", "next_node": "checked_tool", "args": {"value": 1}}
    if scenario == "parallel":
        call = {"thought": "parallel", "next_node": "parallel", "args": {"steps": [
            {"node": "checked_tool", "args": {"value": value}} for value in (1, 2)
        ]}}
    responses = []
    entered = asyncio.Event()

    async def completion(**kwargs):
        active = mlflow.get_current_active_span()
        assert active is not None, "the real LLM call must have an active span"
        trace_ids.append(active.trace_id)
        if scenario == "cancel_llm":
            entered.set()
            await asyncio.Event().wait()
        response = ModelResponse(
            model="openai/test", choices=[{"message": {"role": "assistant", "content": json.dumps(responses.pop(0))}}],
            usage={"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        )
        response._hidden_params["response_cost"] = 3.7e-6
        return response

    def on_event(event):
        events.append(event.event_type)

    kwargs = {}
    if scenario == "guardrail":
        rules = RuleRegistry()
        rules.register(ToolAllowlistRule(denied_tools=frozenset({"checked_tool"})))
        kwargs["guardrail_gateway"] = GuardrailGateway(
            registry=rules, guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(rules)),
        )
    client = _LiteLLMJSONClient("openai/test", temperature=0, json_schema_mode=False, max_retries=1)
    planner = ReactPlanner(
        catalog=catalog, llm_client=client, event_callback=on_event,
        max_iters=1 if scenario == "no_path" else 3, **kwargs,
    )
    payloads = [
        {"found": False, "error": "customer not found", "results": []},
        {"ok": True, "error": False, "value": 42},
        {"value": 1, "failure": {}},
    ] if scenario == "success" else [{}]
    with patch.object(litellm, "acompletion", completion):
        for payload in payloads:
            responses[:] = [call, final]
            if scenario == "cancel_llm":
                task = asyncio.create_task(planner.run("private query"))
                await asyncio.wait_for(entered.wait(), timeout=5)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("cancellation was swallowed")
            else:
                result = await planner.run("private query")
                if scenario == "pause":
                    assert isinstance(result, PlannerPause)
                else:
                    assert result.reason == ("no_path" if scenario in {"no_path", "guardrail"} else "answer_complete")

            # Read immediately: no flush, polling, or sleeps to hide incomplete spans.
            trace = mlflow.get_trace(trace_ids[-1])
            assert trace is not None
            spans = trace.data.spans
            root = next(span for span in spans if span.parent_id is None)
            llms = [span for span in spans if span.span_type == "LLM"]
            tools = [span for span in spans if span.span_type == "TOOL"]
            assert root.name == "agent.run"
            assert all(not span.inputs and not span.outputs for span in spans)
            expected_root_status = "ERROR" if scenario in {"cancel_llm", "no_path", "guardrail", "pause"} else "OK"
            assert root.status.status_code.value == expected_root_status, root
            expected_outcome = (
                "paused" if scenario == "pause" else ("error" if expected_root_status == "ERROR" else "ok")
            )
            assert root.attributes["outcome"] == expected_outcome
            if scenario != "cancel_llm":
                assert root.attributes["finish_reason"] == result.reason
            if scenario == "cancel_llm":
                assert len(llms) == 1
                assert llms[0].status.status_code.value == "ERROR"
                assert llms[0].attributes["error_type"] == "CancelledError"
                assert not tools
                continue
            if scenario == "guardrail":
                assert all(span.status.status_code.value == "ERROR" for span in tools)
                assert all(span.attributes["outcome"] == "error" for span in tools)
                assert not provider.complete.called
                continue
            if scenario == "parallel":
                assert len(tools) == 2 and len(llms) == 4
                customs = [span for span in spans if span.name == "custom_inner_span"]
                assert {span.parent_id for span in customs} == {span.span_id for span in tools}
                for custom in customs:
                    nested = [span for span in llms if span.parent_id == custom.span_id]
                    assert len(nested) == 1 and nested[0].attributes["provider"] == "test"
                assert all(span.parent_id == root.span_id for span in tools)
                assert all(span.attributes["outcome"] == "success" for span in tools)
                assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"]) == {
                    "input_tokens": 60, "output_tokens": 24, "total_tokens": 84,
                }
                continue
            assert len(tools) == 1
            tool_span = tools[0]
            assert tool_span.parent_id == root.span_id
            expected_tool_outcome = "paused" if scenario == "pause" else ("error" if scenario == "error" else "success")
            assert tool_span.attributes["outcome"] == expected_tool_outcome
            assert tool_span.status.status_code.value == ("ERROR" if scenario in {"error", "pause"} else "OK")
            if scenario in {"success", "no_path"}:
                custom = next(span for span in spans if span.name == "custom_inner_span")
                inner = next(span for span in llms if span.attributes["provider"] == "test")
                assert custom.parent_id == tool_span.span_id
                assert inner.parent_id == custom.span_id
                assert inner.attributes["input_tokens"] == 10
                assert inner.attributes["output_tokens"] == 5
            if scenario == "success":
                assert len(llms) == 3
                assert all(span.parent_id == root.span_id for span in llms if span is not inner)
                assert all(span.attributes["cost_usd"] == 3.7e-6 for span in llms if span is not inner)
                assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"]) == {
                    "input_tokens": 50, "output_tokens": 19, "total_tokens": 69,
                }
    assert any(event not in {"tool_call_start", "tool_call_end", "tool_call_result"} for event in events)

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, f"sqlite:///{tmp_path / 'mlflow.db'}", scenario],
        cwd=tmp_path,
        env={
            **os.environ,
            "PENGUIFLOW_LLM_TRACING": "mlflow",
            "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "false",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
