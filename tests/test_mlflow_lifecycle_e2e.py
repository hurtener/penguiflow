"""Planner lifecycle checks through real LiteLLM HTTP and persisted MLflow spans."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "scenario",
    [
        "payload_parallel",
        "tool_error",
        "pause_resume",
        "cancel_tool",
        "cancel_parallel",
        "cancel_stream",
        "sessions",
        "llm_bad_request",
        "context_recovered",
        "steering_recovered",
        "reflection_recovered",
    ],
)
def test_planner_lifecycle_over_real_llm_http(tmp_path: Path, scenario: str) -> None:
    pytest.importorskip("mlflow", minversion="3.18.0")
    pytest.importorskip("litellm")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("alembic")
    script = r"""
import asyncio
import json
import re
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mlflow
from pydantic import BaseModel, ConfigDict

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerPause, ReactPlanner, ReflectionConfig
from penguiflow.planner.llm import _LiteLLMJSONClient
from penguiflow.registry import ModelRegistry
from penguiflow.steering import SteeringEvent, SteeringEventType, SteeringInbox

scenario = sys.argv[2]
mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("http-lifecycle")
counts = Counter()
requests = []
lock = threading.Lock()
stream_started = threading.Event()
release_stream = threading.Event()
bad_request_started = threading.Event()
release_bad_request = threading.Event()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = json.dumps(payload["messages"])
        inner = "inner-" in messages
        summary = "Observation to summarize:" in messages
        critique = "You are a quality assessor for AI-generated answers." in messages
        match = re.search(r"session-(\d+)", messages)
        key = int(match.group(1)) if match else 0
        with lock:
            requests.append({"inner": inner, "summary": summary, "stream": bool(payload.get("stream")), "session": key})
            if critique:
                counts["critique"] += 1
            elif not inner and not summary:
                counts[key] += 1
            first = counts[key] % 2 == 1
        context_error = scenario == "context_recovered" and not inner and not summary and counts[key] == 2
        steered_error = scenario == "steering_recovered" and counts[key] == 1
        if steered_error:
            bad_request_started.set()
            assert release_bad_request.wait(10), "steering was not injected during the failed request"
        reflected_error = scenario == "reflection_recovered" and not critique and counts[key] == 1
        if scenario == "llm_bad_request" or context_error or steered_error or reflected_error:
            code = "context_length_exceeded" if context_error else "invalid_request_error"
            message = "maximum context length exceeded" if context_error else "intentional invalid input"
            body = json.dumps({"error": {"message": message, "type": "invalid_request_error", "code": code}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if scenario == "cancel_stream":
            assert payload.get("stream"), "the real planner must request an HTTP stream"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = {"id": "stream", "model": "gpt-4o-mini", "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": '{"thought":'},
                                  "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
            stream_started.set()
            release_stream.wait(10)
            return
        if critique:
            passed = counts["critique"] > 1
            action = {"score": 1 if passed else 0.1, "passed": passed,
                      "feedback": "Answer the corrected request"}
        elif summary:
            action = {"summary": "large result compressed"}
        elif inner:
            action = {"ok": True}
        elif first and not (scenario == "context_recovered" and counts[key] >= 3):
            action = {"thought": "use tool", "next_node": "checked_tool", "args": {"value": key}}
            if scenario in {"payload_parallel", "cancel_parallel"}:
                action = {"thought": "parallel tools", "next_node": "parallel", "args": {"steps": [
                    {"node": "checked_tool", "args": {"value": value}}
                    for value in range(3 if scenario == "payload_parallel" else 2)
                ]}}
        else:
            action = {"thought": "done", "next_node": "final_response", "args": {"raw_answer": "done"}}
        response = {
            "id": "local-http", "object": "chat.completion", "created": int(time.time()), "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(action)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()

class Args(BaseModel):
    value: int

class Result(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: int = 1

async def run():
    trace_ids = set()
    event_types = []
    tool_entered = asyncio.Event()
    entered_count = 0
    client = _LiteLLMJSONClient(
        {"model": "openai/gpt-4o-mini", "api_base": f"http://127.0.0.1:{server.server_port}/v1",
         "api_key": "local-fixture"},
        temperature=0, json_schema_mode=False, max_retries=1, timeout_s=10,
        streaming_enabled=scenario == "cancel_stream",
    )

    @tool(desc="A real tool used to verify trace ownership and execution outcomes")
    async def checked_tool(args: Args, ctx) -> Result:
        nonlocal entered_count
        if scenario == "tool_error":
            raise ValueError("intentional tool failure")
        if scenario == "pause_resume":
            await ctx.pause("approval_required", {"value": args.value})
        if scenario in {"cancel_tool", "cancel_parallel"}:
            entered_count += 1
            if entered_count == (2 if scenario == "cancel_parallel" else 1):
                tool_entered.set()
            await asyncio.Event().wait()
        with mlflow.start_span(name=f"custom-{args.value}"):
            # Force parallel tools to overlap before making real inner HTTP calls.
            await asyncio.sleep(0.01)
            await client.complete(messages=[{"role": "user", "content": f"inner-{args.value}"}])
        payloads = [
            {"found": False, "error": "customer not found", "results": []},
            {"ok": True, "error": False, "value": 42},
            {"value": 1, "failure": {}},
        ]
        if scenario == "context_recovered":
            return Result(value=args.value, data="x" * 3000)
        return Result(**(payloads[args.value] if scenario == "payload_parallel" else {"value": args.value}))

    registry = ModelRegistry()
    registry.register("checked_tool", Args, Result)
    catalog = build_catalog([Node(checked_tool, name="checked_tool")], registry)

    def event_callback(_event):
        event_types.append(_event.event_type)
        span = mlflow.get_current_active_span()
        if span is not None:
            trace_ids.add(span.trace_id)

    planner = ReactPlanner(
        catalog=catalog, llm_client=client, max_iters=3, pause_enabled=True,
        event_callback=event_callback, stream_final_response=scenario == "cancel_stream",
        reflection_config=(
            ReflectionConfig(enabled=True, max_revisions=1) if scenario == "reflection_recovered" else None
        ),
    )
    if scenario == "steering_recovered":
        inbox = SteeringInbox()
        task = asyncio.create_task(planner.run("session-0", steering=inbox))
        async with asyncio.timeout(10):
            while not bad_request_started.is_set():
                await asyncio.sleep(0.01)
        assert await inbox.push(SteeringEvent(
            session_id="session-0", task_id="task-0", event_type=SteeringEventType.USER_MESSAGE,
            payload={"text": "Continue with this corrected request"},
        ))
        release_bad_request.set()
        result = await task
        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "done"
        assert "steering_received" in event_types
        assert "execution_outcome" not in result.metadata.get("trajectory_metadata", {})
    elif scenario.startswith("cancel_"):
        task = asyncio.create_task(planner.run("session-0"))
        if scenario == "cancel_stream":
            async with asyncio.timeout(10):
                while not stream_started.is_set():
                    await asyncio.sleep(0.01)
            # Wait for the actual planner stream callback, beyond receiving HTTP headers.
            await asyncio.sleep(0.05)
        else:
            await asyncio.wait_for(tool_entered.wait(), timeout=10)
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancellation was swallowed")
        release_stream.set()
    elif scenario == "sessions":
        for _round in range(2):
            results = await asyncio.gather(*[
                planner.run(f"session-{index}", tool_context={"session_id": str(index)}) for index in range(4)
            ])
            assert all(result.reason == "answer_complete" for result in results)
    else:
        result = await planner.run("session-0")
        if scenario == "pause_resume":
            assert isinstance(result, PlannerPause)
            result = await planner.resume(result.resume_token, user_input="approved")
        assert result.reason == "answer_complete"

    assert mlflow.get_current_active_span() is None, "active span leaked after planner completion"
    if scenario == "context_recovered":
        assert "trajectory_compressed" in event_types, event_types
        assert sum(request["summary"] for request in requests) == 1
    if scenario == "reflection_recovered":
        assert result.payload["raw_answer"] == "done"
        assert result.metadata["reflection"]["revisions"] == 1 and result.metadata["reflection"]["passed"]
        assert "execution_outcome" not in result.metadata.get("trajectory_metadata", {})
    expected_traces = 8 if scenario == "sessions" else (2 if scenario == "pause_resume" else 1)
    assert len(trace_ids) == expected_traces, trace_ids
    evidence = []
    for trace_id in trace_ids:
        # A local trace must already be complete: no flush, polling or completion delay.
        trace = mlflow.get_trace(trace_id)
        assert trace is not None
        spans = trace.data.spans
        roots = [span for span in spans if span.parent_id is None]
        assert len(roots) == 1
        root = roots[0]
        tools = [span for span in spans if span.span_type == "TOOL"]
        llms = [span for span in spans if span.span_type == "LLM"]
        assert all(not span.inputs and not span.outputs for span in spans)
        if scenario.startswith("cancel_"):
            assert root.status.status_code.value == "ERROR"
            assert root.attributes["outcome"] == "error"
            if scenario == "cancel_stream":
                assert len(llms) == 1 and not tools
                assert llms[0].status.status_code.value == "ERROR"
                assert llms[0].attributes["error_type"] == "CancelledError"
                assert llms[0].attributes["stream"] is True
            else:
                assert len(tools) == (2 if scenario == "cancel_parallel" else 1)
                assert all(span.status.status_code.value == "ERROR" for span in tools)
        elif scenario == "llm_bad_request":
            assert root.status.status_code.value == "ERROR", root
            assert root.attributes["outcome"] == "error"
            assert root.attributes["finish_reason"] == "answer_complete"
            assert len(llms) == 1 and not tools
            assert llms[0].status.status_code.value == "ERROR"
            assert llms[0].attributes["error_type"] == "BadRequestError"
        elif scenario in {"steering_recovered", "reflection_recovered"}:
            assert root.status.status_code.value == "OK" and root.attributes["outcome"] == "ok"
            assert len(llms) == (4 if scenario == "reflection_recovered" else 2) and not tools
            assert len([span for span in llms if span.status.status_code.value == "ERROR"]) == 1
        elif scenario == "pause_resume":
            if root.name == "agent.run":
                assert root.attributes["outcome"] == "paused"
                assert root.status.status_code.value == "ERROR"
                assert tools[0].attributes["outcome"] == "paused"
            else:
                assert root.name == "agent.resume"
                assert root.attributes["outcome"] == "ok" and root.status.status_code.value == "OK"
        else:
            assert root.attributes["outcome"] == "ok" and root.status.status_code.value == "OK"
            for tool_span in tools:
                assert tool_span.parent_id == root.span_id
                assert tool_span.attributes["outcome"] == ("error" if scenario == "tool_error" else "success")
                assert tool_span.status.status_code.value == ("ERROR" if scenario == "tool_error" else "OK")
            if scenario != "tool_error":
                assert len(tools) == (3 if scenario == "payload_parallel" else 1)
                customs = [span for span in spans if span.name.startswith("custom-")]
                assert {span.parent_id for span in customs} == {span.span_id for span in tools}
                for custom in customs:
                    assert len([span for span in llms if span.parent_id == custom.span_id]) == 1
        recorded = json.loads(trace.info.trace_metadata.get("mlflow.trace.tokenUsage", "null"))
        completed_llms = [span for span in llms if "error_type" not in span.attributes]
        if scenario == "context_recovered":
            assert len(llms) == 5 and len(completed_llms) == 4
            assert all(span.status.status_code.value == "ERROR" for span in llms if span not in completed_llms)
        expected_usage = {"input_tokens": 20 * len(completed_llms), "output_tokens": 7 * len(completed_llms),
                          "total_tokens": 27 * len(completed_llms)}
        if scenario not in {"cancel_stream", "llm_bad_request"}:
            assert recorded == expected_usage, (recorded, expected_usage)
            assert all(span.attributes["cost_usd"] > 0 for span in completed_llms), [span.attributes for span in llms]
        evidence.append({"root": root.name, "status": root.status.status_code.value,
                         "outcome": root.attributes["outcome"], "tools": len(tools), "llms": len(llms),
                         "token_usage": recorded})
    report = {"mlflow": mlflow.__version__, "scenario": scenario, "http_requests": len(requests),
              "traces": evidence}
    print("QA_RESULT=" + json.dumps(report))

try:
    asyncio.run(asyncio.wait_for(run(), timeout=35))
finally:
    release_stream.set()
    release_bad_request.set()
    server.shutdown()
    server.server_close()
    server_thread.join(timeout=2)
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
    report = json.loads(
        next(line.removeprefix("QA_RESULT=") for line in result.stdout.splitlines() if line.startswith("QA_RESULT="))
    )
    if evidence_dir := os.environ.get("PENGUIFLOW_QA_EVIDENCE_DIR"):
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"lifecycle-{report['mlflow']}-{scenario}.json").write_text(json.dumps(report, indent=2))
