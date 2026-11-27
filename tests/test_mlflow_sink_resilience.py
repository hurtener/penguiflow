"""Exercise tracing failure boundaries against the real optional MLflow SDK."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_sdk(script: str, tmp_path: Path) -> None:
    pytest.importorskip("mlflow", minversion="3.18.0")
    result = subprocess.run(
        [sys.executable, "-c", script, f"sqlite:///{tmp_path / 'mlflow.db'}"],
        cwd=tmp_path,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "MLFLOW_EXPERIMENT_NAME",
                    "MLFLOW_EXPERIMENT_ID",
                }
            },
            "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "false",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cancelled_call_is_error_with_real_sdk(tmp_path: Path) -> None:
    _run_sdk(
        r"""
import asyncio
import sys
import mlflow
from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink

mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("cancelled-llm")

async def run():
    sink = MlflowLLMTraceSink()
    await sink.prepare()
    call = LLMCallSpan(provider="test", model="test")
    started = asyncio.Event()
    trace_id = None
    async def complete():
        nonlocal trace_id
        with sink.span(call) as span:
            trace_id = span.trace_id
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(complete())
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation was swallowed")
    span = mlflow.get_trace(trace_id).data.spans[0]
    assert span.status.status_code.value == "ERROR", span.status
    assert span.attributes["error_type"] == "CancelledError"
    assert call.error_type == "CancelledError"

asyncio.run(run())
""",
        tmp_path,
    )


@pytest.mark.parametrize("target", ["planner", "llm_sink"])
def test_cold_sdk_import_stays_off_event_loop(tmp_path: Path, target: str) -> None:
    _run_sdk(
        r"""
import asyncio
import builtins
import json
import os
import sys
import threading
import time

from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink
from penguiflow.planner import ReactPlanner

assert "mlflow" not in sys.modules
os.environ["PENGUIFLOW_LLM_TRACING"] = "mlflow"
os.environ["MLFLOW_TRACKING_URI"] = "http://127.0.0.1:1"
os.environ["MLFLOW_EXPERIMENT_NAME"] = "cold-start"
original_import = builtins.__import__
import_threads = []

def record_import(name, *args, **kwargs):
    if name == "mlflow" and name not in sys.modules:
        import_threads.append(threading.get_ident())
    return original_import(name, *args, **kwargs)

builtins.__import__ = record_import

class Client:
    async def complete(self, **kwargs):
        return json.dumps({
            "thought": "done", "next_node": "final_response", "args": {"raw_answer": "done"},
        }), 0.0

async def run():
    ticks = []
    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)
    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        if TARGET == "planner":
            result = await ReactPlanner(catalog=[], llm_client=Client()).run("query")
            assert result.reason == "answer_complete"
        else:
            sink = MlflowLLMTraceSink()
            await sink.prepare()
            with sink.span(LLMCallSpan(provider="test", model="test")):
                pass
        await asyncio.sleep(0.01)
        assert import_threads and threading.get_ident() not in import_threads, import_threads
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.2, ticks
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

asyncio.run(run())
""".replace("TARGET", repr(target)),
        tmp_path,
    )


def test_configured_destination_and_active_run_keep_their_experiment(tmp_path: Path) -> None:
    _run_sdk(
        r"""
import asyncio
import sys
import mlflow
from mlflow.entities import MlflowExperimentLocation
from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink

mlflow.set_tracking_uri(sys.argv[1])
destination_id = mlflow.create_experiment("destination")
run_experiment_id = mlflow.create_experiment("active-run")

async def record(expected):
    sink = MlflowLLMTraceSink()
    await sink.prepare()
    with sink.span(LLMCallSpan(provider="test", model="test")) as span:
        trace_id = span.trace_id
    assert mlflow.get_trace(trace_id).info.experiment_id == expected

async def run():
    mlflow.tracing.set_destination(MlflowExperimentLocation(experiment_id=destination_id))
    await record(destination_id)
    mlflow.tracing.reset()
    with mlflow.start_run(experiment_id=run_experiment_id):
        await record(run_experiment_id)

asyncio.run(run())
""",
        tmp_path,
    )


@pytest.mark.parametrize("server", ["refused", "hang"])
def test_unavailable_server_never_stalls_loop_or_shutdown(tmp_path: Path, server: str) -> None:
    _run_sdk(
        r"""
import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mlflow
from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink
from penguiflow.planner import ReactPlanner

requests = []
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        threading.Event().wait(120)
    def log_message(self, *args):
        pass
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = server.server_port
if SERVER == "hang":
    threading.Thread(target=server.serve_forever, daemon=True).start()
else:
    server.server_close()
mlflow.set_tracking_uri(f"http://127.0.0.1:{port}")
os.environ["MLFLOW_EXPERIMENT_NAME"] = "unreachable"
os.environ["PENGUIFLOW_LLM_TRACING"] = "mlflow"
os.environ.pop("MLFLOW_EXPERIMENT_ID", None)

class Client:
    calls = 0
    async def complete(self, **kwargs):
        self.calls += 1
        sink = MlflowLLMTraceSink()
        await sink.prepare()
        with sink.span(LLMCallSpan(provider="test", model="test")) as span:
            assert span is None
        return json.dumps({
            "thought": "done", "next_node": "final_response", "args": {"raw_answer": "done"},
        }), 0.0

async def run():
    ticks = []
    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)
    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.monotonic()
    client = Client()
    planner = ReactPlanner(catalog=[], llm_client=client)
    for _ in range(5):
        result = await planner.run("query")
        assert result.reason == "answer_complete"
        await asyncio.sleep(0.01)
    assert client.calls == 5
    assert time.monotonic() - started < 0.8
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.1, ticks
    assert len(requests) <= 1, requests
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

asyncio.run(run())
""".replace("SERVER", repr(server)),
        tmp_path,
    )


def test_remote_export_never_stalls_loop(tmp_path: Path) -> None:
    _run_sdk(
        r"""
import asyncio
import sys
import threading
import time
from unittest.mock import patch
import mlflow
from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink

mlflow.set_tracking_uri(sys.argv[1])
mlflow.set_experiment("export-outage")

async def run():
    sink = MlflowLLMTraceSink()
    await sink.prepare()
    # Warm provider/store imports before changing to the failed remote destination.
    with sink.span(LLMCallSpan(provider="test", model="test")):
        pass
    mlflow.set_tracking_uri("http://127.0.0.1:1")
    await sink.prepare()
    exporting = threading.Event()
    def hang(*args, **kwargs):
        exporting.set()
        threading.Event().wait(120)
    with patch("requests.Session.request", hang):
        started = time.monotonic()
        with sink.span(LLMCallSpan(provider="test", model="test")) as span:
            assert span is not None
        await asyncio.sleep(0.05)
        assert exporting.is_set()
        # A detached summary/subagent must get its own trace during another export.
        with sink.span(LLMCallSpan(provider="test", model="test")) as sibling:
            assert sibling is not None
            assert sibling.trace_id != span.trace_id
            assert sibling.parent_id is None
        for _ in range(30):
            with sink.span(LLMCallSpan(provider="test", model="test")) as pending:
                assert pending is not None
        for _ in range(5):
            with sink.span(LLMCallSpan(provider="test", model="test")) as span:
                assert span is None
        assert time.monotonic() - started < 0.5

asyncio.run(run())
""",
        tmp_path,
    )


def test_slow_initialization_recovers_without_repeated_waits(tmp_path: Path) -> None:
    _run_sdk(
        r"""
import asyncio
import sys
import time
from unittest.mock import patch
import mlflow
from penguiflow.llm.tracing import LLMCallSpan, MlflowLLMTraceSink

mlflow.set_tracking_uri(sys.argv[1])
experiment_id = mlflow.set_experiment("slow-initialization").experiment_id

async def run():
    sink = MlflowLLMTraceSink()
    await sink.prepare()
    mlflow.set_tracking_uri("http://127.0.0.1:1")
    calls = []
    def slow_resolve():
        calls.append(1)
        time.sleep(0.2)
        return experiment_id
    with patch("mlflow.tracking.fluent._get_experiment_id", slow_resolve):
        await sink.prepare()
        with sink.span(LLMCallSpan(provider="test", model="test")) as span:
            assert span is None
        started = time.monotonic()
        await sink.prepare()
        assert time.monotonic() - started < 0.05
        await asyncio.sleep(0.2)
        await sink.prepare()
        with sink.span(LLMCallSpan(provider="test", model="test")) as span:
            assert span is not None
        assert calls == [1]

asyncio.run(run())
""",
        tmp_path,
    )
