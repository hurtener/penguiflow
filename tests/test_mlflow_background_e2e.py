"""Real planner/background traces over the unpatched LiteLLM HTTP transport."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["memory", "subagents"])
@pytest.mark.parametrize("async_logging", ["default", "false", "true"])
def test_background_planners_have_readable_independent_traces(
    tmp_path: Path, scenario: str, async_logging: str
) -> None:
    pytest.importorskip("mlflow", minversion="3.18.0")
    pytest.importorskip("litellm")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("alembic")
    script = r"""
import asyncio
import json
import sys
import threading
import time
from collections import Counter
from contextvars import ContextVar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mlflow
from mlflow.tracing import provider
from opentelemetry import baggage, context
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.llm import _LiteLLMJSONClient
from penguiflow.planner.memory import MemoryBudget, MemoryKey, ShortTermMemoryConfig
from penguiflow.registry import ModelRegistry
from penguiflow.sessions import StreamingSession, TaskResult, TaskType

scenario = sys.argv[2]
mlflow.set_tracking_uri(sys.argv[1])
experiment_id = mlflow.set_experiment("background-http-e2e").experiment_id
tenant = ContextVar("background-http-tenant", default="missing")
counts = Counter()
parent_closed = threading.Event()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions", self.path
        payloads = []
        for message in request["messages"]:
            if message["role"] != "user":
                continue
            try:
                payloads.append(json.loads(message["content"]))
            except (ValueError, TypeError):
                pass
        summary = any(isinstance(payload, dict) and "previous_summary" in payload for payload in payloads)
        if summary:
            assert parent_closed.is_set(), "summary HTTP call started before its parent returned"
            key = "summary"
            content = {"summary": "<session_summary>remembered</session_summary>"}
            prompt_tokens, completion_tokens = 13, 7
        else:
            query = next(payload["query"] for payload in payloads if isinstance(payload, dict) and "query" in payload)
            key = query
            first = counts[key] == 0
            if scenario == "subagents" and query in {"parent", "child-0"} and first:
                content = {"thought": "delegate", "next_node": "spawn_children", "args": {"owner": query}}
            else:
                content = {"thought": "done", "next_node": "final_response", "args": {"raw_answer": "done"}}
            if query.startswith("child") or query == "grandchild":
                assert parent_closed.is_set(), "child HTTP call started before its parent returned"
            prompt_tokens, completion_tokens = 20, 7
        counts[key] += 1
        response = {
            "id": "chatcmpl-local-background", "object": "chat.completion", "created": 1,
            "model": "gpt-4o-mini", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(content),
            }}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        }
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

def client():
    return _LiteLLMJSONClient(
        {"model": "openai/gpt-4o-mini", "api_base": f"http://127.0.0.1:{server.server_port}/v1",
         "api_key": "local-fixture"},
        temperature=0, json_schema_mode=True, max_retries=1, timeout_s=5,
    )

def assert_context():
    assert tenant.get() == "tenant-1"
    assert baggage.get_baggage("request") == "request-1"
    runtime = getattr(provider, "mlflow_runtime_context", None)
    if runtime is not None:
        assert baggage.get_baggage("mlflow-request", context=runtime.get_current()) == "request-2"

def record_root(ids):
    def callback(event):
        assert_context()
        span = mlflow.get_current_active_span()
        assert span is not None
        ids.add(span.trace_id)
    return callback

def check_trace(result, inputs, outputs, *, agents, tools):
    assert result is not None
    spans = result.data.spans
    roots = [span for span in spans if span.parent_id is None]
    assert len(roots) == 1
    assert sum(span.span_type == "AGENT" for span in spans) == agents
    assert sum(span.span_type == "TOOL" for span in spans) == tools
    assert all(span.status.status_code.value == "OK" for span in spans)
    assert all(not span.inputs and not span.outputs for span in spans)
    token_metadata = result.info.trace_metadata.get("mlflow.trace.tokenUsage")
    assert token_metadata is not None
    actual = json.loads(token_metadata)
    expected = {
        "input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs,
    }
    assert actual == expected, (actual, expected)
    assert sum(span.attributes.get("input_tokens", 0) for span in spans) == inputs
    return result

reads = {}
async def trace(trace_id, inputs, outputs, *, agents=1, tools=0):
    started = time.monotonic()
    while True:
        result = mlflow.get_trace(trace_id)
        try:
            check_trace(result, inputs, outputs, agents=agents, tools=tools)
        except AssertionError:
            reads.setdefault(trace_id, {"immediate_readable": False})
            if time.monotonic() - started > 12:
                raise
            await asyncio.sleep(0.02)
        else:
            reads.setdefault(trace_id, {"immediate_readable": True})
            reads[trace_id].setdefault("readable_after_s", round(time.monotonic() - started, 4))
            return result

async def stored_traces(expected):
    started = time.monotonic()
    while True:
        results = mlflow.search_traces(experiment_ids=[experiment_id], return_type="list")
        if len(results) == expected:
            return results
        assert time.monotonic() - started < 12, len(results)
        await asyncio.sleep(0.02)

async def run():
    tenant.set("tenant-1")
    context.attach(baggage.set_baggage("request", "request-1"))
    runtime = getattr(provider, "mlflow_runtime_context", None)
    if runtime is not None:
        runtime.attach(baggage.set_baggage("mlflow-request", "request-2"))
    all_ids = set()
    if scenario == "memory":
        updates = []
        async def summary_updated(*args):
            assert_context()
            updates.append(1)
        ids = set()
        planner = ReactPlanner(
            catalog=[], llm_client=client(), event_callback=record_root(ids),
            short_term_memory=ShortTermMemoryConfig(
                strategy="rolling_summary", budget=MemoryBudget(full_zone_turns=0),
                retry_attempts=0, on_summary_updated=summary_updated,
            ),
        )
        key = MemoryKey(tenant_id="t", user_id="u", session_id="s")
        for index in range(2):
            parent_closed.clear()
            result = await planner.run(f"memory-parent-{index}", memory_key=key)
            assert result.reason == "answer_complete"
            assert counts["summary"] == index
            parent_closed.set()
            for trace_id in ids:
                await trace(trace_id, 20, 7)
            memory = planner._memory_by_key[key.composite()]
            await asyncio.wait_for(memory.flush(), timeout=10)
        assert len(updates) == counts["summary"] == 2
        traces = await stored_traces(4)
        assert len(traces) == 4
        summaries = [item for item in traces if item.info.trace_id not in ids]
        assert len(summaries) == 2
        for item in summaries:
            await trace(item.info.trace_id, 13, 7, agents=0)
        all_ids = {item.info.trace_id for item in traces}
    else:
        session = StreamingSession("background-e2e")
        gate = asyncio.Event()
        grandchild_gate = asyncio.Event()
        spawned = []
        ids_by_query = {}
        class Args(BaseModel):
            owner: str
        class Result(BaseModel):
            count: int
        def planner_for(query):
            ids_by_query[query] = set()
            return ReactPlanner(
                catalog=catalog if query in {"parent", "child-0"} else [], llm_client=client(),
                event_callback=record_root(ids_by_query[query]),
            )
        async def launch(query, ready):
            async def pipeline(_runtime):
                assert_context()
                assert mlflow.get_current_active_span() is None
                await ready.wait()
                result = await planner_for(query).run(query)
                assert result.reason == "answer_complete"
                return TaskResult(payload=result.payload)
            task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query=query)
            spawned.append((query, task_id, session._task_handles[task_id]))
        @tool(desc="Spawn background planner tasks")
        async def spawn_children(args: Args, ctx) -> Result:
            assert_context()
            if args.owner == "parent":
                await launch("child-0", gate)
                await launch("child-1", gate)
                return Result(count=2)
            await launch("grandchild", grandchild_gate)
            return Result(count=1)
        registry = ModelRegistry()
        registry.register("spawn_children", Args, Result)
        catalog = build_catalog([Node(spawn_children, name="spawn_children")], registry)
        try:
            result = await planner_for("parent").run("parent")
            assert result.reason == "answer_complete"
            parent_closed.set()
            assert len(ids_by_query["parent"]) == 1
            parent_id = next(iter(ids_by_query["parent"]))
            await trace(parent_id, 40, 14, tools=1)
            gate.set()
            children = [handle for query, _, handle in spawned if query != "grandchild"]
            await asyncio.wait_for(asyncio.gather(*children), 10)
            assert len(spawned) == 3
            child_ids = {query: next(iter(ids)) for query, ids in ids_by_query.items()}
            assert len(set(child_ids.values())) == 3
            await trace(child_ids["child-0"], 40, 14, tools=1)
            await trace(child_ids["child-1"], 20, 7)
            grandchild_gate.set()
            await asyncio.wait_for(next(handle for query, _, handle in spawned if query == "grandchild"), 10)
            all_ids = {next(iter(ids)) for ids in ids_by_query.values()}
            assert len(all_ids) == 4
            await trace(next(iter(ids_by_query["grandchild"])), 20, 7)
            await trace(parent_id, 40, 14, tools=1)
            traces = await stored_traces(4)
            assert {item.info.trace_id for item in traces} == all_ids
        finally:
            await session.close()
    # Re-read every closed trace after all descendant work has completed.
    assert all(mlflow.get_trace(trace_id) is not None for trace_id in all_ids)
    Path(sys.argv[3]).write_text(json.dumps({
        "mlflow": mlflow.__version__, "scenario": scenario, "http_calls": dict(counts),
        "async_logging": sys.argv[4],
        "reads": reads,
        "trace_count": len(all_ids), "trace_ids": sorted(all_ids),
        "result": "passed",
    }, indent=2))

try:
    asyncio.run(run())
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
"""
    result_path = tmp_path / "result.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith("MLFLOW_")}
    env.update(
        PENGUIFLOW_LLM_TRACING="mlflow",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
    )
    if async_logging != "default":
        env["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] = async_logging
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            f"sqlite:///{tmp_path / 'mlflow.db'}",
            scenario,
            str(result_path),
            async_logging,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result_path.read_text())
    assert evidence["trace_count"] == 4
    assert evidence["result"] == "passed"
