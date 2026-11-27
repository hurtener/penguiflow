"""Planner probes through real LiteLLM HTTP and a fault-injected MLflow server."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def http_servers(tmp_path_factory: pytest.TempPathFactory):
    sdk = pytest.importorskip("mlflow", minversion="3.18.0")
    pytest.importorskip("mlflow.server")
    pytest.importorskip("litellm")
    root = tmp_path_factory.mktemp("real-mlflow-http")
    backend_port = _unused_port()
    log = (root / "server.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mlflow",
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
            "--workers",
            "1",
            "--backend-store-uri",
            f"sqlite:///{root / 'tracking.db'}",
            "--artifacts-destination",
            str(root / "artifacts"),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=root,
        start_new_session=True,
    )
    state = {"mode": "healthy", "provider_calls": 0, "requests": []}
    release = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            inner = any(message.get("content") == "INNER_QA" for message in request.get("messages", []))
            if not inner:
                state["provider_calls"] += 1
            content = (
                {"inner": True}
                if inner
                else (
                    {"thought": "lookup", "next_node": "lookup", "args": {"value": 1}}
                    if state["provider_calls"] % 2
                    else {"thought": "done", "next_node": "final_response", "args": {"raw_answer": "done"}}
                )
            )
            payload = json.dumps(
                {
                    "id": "local-http-completion",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": json.dumps(content)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def do_PATCH(self):
            self.forward()

        def forward(self):
            if self.path.startswith("/__control/"):
                state["mode"] = self.path.rsplit("/", 1)[-1]
                if state["mode"] == "healthy":
                    release.set()
                else:
                    release.clear()
                state["provider_calls"] = 0
                self.send_response(200)
                self.end_headers()
                return
            state["requests"].append({"path": self.path, "mode": state["mode"]})
            lookup = "experiments/get-by-name" in self.path
            export = self.command != "GET" and "traces" in self.path
            mode = state["mode"]
            if lookup and mode == "delay":
                time.sleep(3)
            if (lookup and mode == "hang") or (export and mode == "export_hang"):
                release.wait(20)
            if mode == "http500" or (export and mode == "export_500"):
                payload = b'{"error_code":"INTERNAL_ERROR","message":"injected tracking outage"}'
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            connection = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=20)
            try:
                connection.request(
                    self.command,
                    self.path,
                    body=body,
                    headers={
                        key: value
                        for key, value in self.headers.items()
                        if key.lower() not in {"host", "connection", "content-length"}
                    },
                )
                response = connection.getresponse()
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                connection.close()

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = False
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{backend_port}/health", timeout=0.3).close()
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.flush()
                    pytest.fail((root / "server.log").read_text())
                time.sleep(0.1)
        for server in (proxy, provider):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        serving = True
        # Create the experiment through the actual server, before injecting failures.
        connection = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=5)
        connection.request(
            "POST",
            "/api/2.0/mlflow/experiments/create",
            json.dumps({"name": "http-e2e"}),
            {
                "Content-Type": "application/json",
            },
        )
        assert connection.getresponse().status == 200
        connection.close()
        yield {
            "proxy": f"http://127.0.0.1:{proxy.server_port}",
            "provider": f"http://127.0.0.1:{provider.server_port}/v1",
            "refused": f"http://127.0.0.1:{_unused_port()}",
            "state": state,
            "release": release,
        }
    finally:
        release.set()
        for server in (proxy, provider):
            if serving:
                server.shutdown()
            server.server_close()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        log.close()
        evidence = Path(os.environ.get("PENGUIFLOW_QA_EVIDENCE_DIR", str(root / "evidence")))
        evidence.mkdir(parents=True, exist_ok=True)
        for name in ("server.log", "tracking.db"):
            if (root / name).exists():
                shutil.copy2(root / name, evidence / f"http-server-{sdk.__version__}-{root.parent.name}-{name}")


_CLIENT = r"""
import asyncio
import faulthandler
from contextlib import nullcontext
import json
import os
import sys
import time
import urllib.request

import litellm
import mlflow
from pydantic import BaseModel
from penguiflow.catalog import build_catalog, tool
from penguiflow.llm.protocol import NativeLLMAdapter
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.llm import _LiteLLMJSONClient
from penguiflow.registry import ModelRegistry

config = json.loads(sys.argv[1])
faulthandler.dump_traceback_later(10)
standalone = config["scenario"].startswith("standalone")
application_owned = config["scenario"] == "external" or standalone

class Args(BaseModel):
    value: int

class Result(BaseModel):
    ok: bool = True
    error: bool = False
    value: int = 42

@tool(desc="A real tool executed after an HTTP LLM response")
async def lookup(args: Args, ctx) -> Result:
    await asyncio.sleep(0.005)
    if config["scenario"] in {"healthy", "external"}:
        with mlflow.start_span(name="tool.custom"):
            await make_client().complete(messages=[{"role": "user", "content": "INNER_QA"}])
    else:
        await make_client().complete(messages=[{"role": "user", "content": "INNER_QA"}])
    return Result()

registry = ModelRegistry()
registry.register("lookup", Args, Result)
catalog = build_catalog([Node(lookup, name="lookup")], registry)
trace_ids = set()

def event_callback(_event):
    if (span := mlflow.get_current_active_span()) is not None:
        trace_ids.add(span.trace_id)

def make_client():
    return _LiteLLMJSONClient({
        "model": "openai/gpt-4o-mini", "api_base": config["provider"], "api_key": "local-test-only",
    }, temperature=0, json_schema_mode=False, max_retries=1, timeout_s=5)

def control(mode):
    urllib.request.urlopen(config["proxy"] + "/__control/" + mode, timeout=2).close()

async def run():
    # Exercise real provider setup before the timed interval; no SDK or transport mocks.
    os.environ["PENGUIFLOW_LLM_TRACING"] = "off"
    await make_client().complete(messages=[{"role": "user", "content": "warm HTTP transport"}])
    control("healthy")
    os.environ["PENGUIFLOW_LLM_TRACING"] = "off" if config["scenario"] == "off" else "mlflow"
    mlflow.set_tracking_uri(config["refused"] if config["scenario"] == "refused" else config["proxy"])
    planner = ReactPlanner(catalog=catalog, llm_client=make_client(), event_callback=event_callback)
    if config["scenario"] == "healthy" or application_owned or config["scenario"].startswith("export_"):
        await planner._mlflow_telemetry.prepare()
        # Wait for one background initialization, without replacing its implementation.
        await asyncio.sleep(1)
    control(config["scenario"])
    ticks = []
    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)
    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    durations = []
    solo = (NativeLLMAdapter("gpt-4o-mini", api_key="local-test-only", base_url=config["provider"],
                            transport="native", streaming_enabled=False, json_schema_mode=False)
            if config["scenario"].startswith("standalone_native") else make_client()) if standalone else None
    for _ in range(3):
        started = time.monotonic()
        with mlflow.start_span(name="application") if application_owned else nullcontext() as application:
            if standalone:
                trace_ids.add(application.trace_id)
                if config["scenario"] == "standalone_native_tools":
                    result = await solo.complete_with_tools(messages=[{"role": "user", "content": "value"}], tools=[])
                    assert result.content and result.cost > 0
                else:
                    content, cost = await solo.complete(messages=[{"role": "user", "content": "value"}])
                    assert content and cost > 0
            else:
                finish = await asyncio.wait_for(planner.run("look up value"), timeout=5)
                assert finish.reason == "answer_complete", finish
        durations.append(time.monotonic() - started)
        await asyncio.sleep(0.05)
    beat.cancel()
    try:
        await beat
    except asyncio.CancelledError:
        pass
    gap = max(b - a for a, b in zip(ticks, ticks[1:]))
    assert max(durations) < 2, durations
    assert gap < 0.25, gap
    control("healthy")
    return {"durations_s": durations, "max_loop_gap_s": gap, "trace_ids": list(trace_ids)}

result = asyncio.run(run())
if config["scenario"] == "healthy" or application_owned:
    # Root export is intentionally asynchronous: poll persisted server state outside the loop.
    assert result["trace_ids"], result
    deadline = time.monotonic() + 15
    stored = []
    while time.monotonic() < deadline:
        stored = [mlflow.get_trace(trace_id) for trace_id in result["trace_ids"]]
        if all(trace is not None for trace in stored):
            break
        time.sleep(0.05)
    assert all(trace is not None for trace in stored), result
    full = stored if standalone else [
        trace for trace in stored if any(span.name == "agent.run" for span in trace.data.spans)
    ]
    assert full, [(trace.info.trace_id, [s.name for s in trace.data.spans]) for trace in stored]
    for trace in full:
        if standalone:
            application = next(span for span in trace.data.spans if span.name == "application")
            llms = [span for span in trace.data.spans if span.span_type == "LLM"]
            assert len(trace.data.spans) == 2 and len(llms) == 1
            assert llms[0].parent_id == application.span_id
            assert llms[0].attributes["cost_usd"] > 0
            assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"]) == {
                "input_tokens": 20, "output_tokens": 7, "total_tokens": 27,
            }
            continue
        root = next(span for span in trace.data.spans if span.name == "agent.run")
        if config["scenario"] == "external":
            application = next(span for span in trace.data.spans if span.name == "application")
            assert root.parent_id == application.span_id
            assert len(trace.data.spans) == 7, [span.name for span in trace.data.spans]
        assert root.status.status_code.value == "OK"
        tools = [s for s in trace.data.spans if s.span_type == "TOOL"]
        llms = [s for s in trace.data.spans if s.span_type == "LLM"]
        assert len(tools) == 1 and len(llms) == 3
        assert tools[0].attributes["outcome"] == "success"
        assert tools[0].parent_id == root.span_id
        custom = next(span for span in trace.data.spans if span.name == "tool.custom")
        assert custom.parent_id == tools[0].span_id
        assert sum(span.parent_id == root.span_id for span in llms) == 2
        assert sum(span.parent_id == custom.span_id for span in llms) == 1
        assert all(span.attributes["cost_usd"] > 0 for span in llms)
        assert json.loads(trace.info.trace_metadata["mlflow.trace.tokenUsage"]) == {
            "input_tokens": 60, "output_tokens": 21, "total_tokens": 81,
        }
    result["persisted_full_traces"] = len(full)
result.update({"version": mlflow.__version__, "scenario": config["scenario"], "async_export": config["async_export"]})
print("HTTP_QA_RESULT " + json.dumps(result), flush=True)
"""


@pytest.mark.parametrize("async_export", ["default", "false", "true"])
@pytest.mark.parametrize(
    "scenario", ["off", "healthy", "delay", "refused", "hang", "http500", "export_hang", "export_500"]
)
def test_planner_with_real_http_tracking(http_servers, tmp_path: Path, scenario: str, async_export: str) -> None:
    http_servers["release"].clear()
    http_servers["state"]["requests"].clear()
    config = {key: value for key, value in http_servers.items() if key not in {"state", "release"}}
    config.update(scenario=scenario, async_export=async_export)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MLFLOW_EXPERIMENT_ID", "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "PENGUIFLOW_LLM_TRACING"}
    }
    env.update(MLFLOW_EXPERIMENT_NAME="http-e2e", LITELLM_LOCAL_MODEL_COST_MAP="True", MLFLOW_DISABLE_AGENT_HINT="1")
    if async_export != "default":
        env["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] = async_export
    evidence = Path(os.environ.get("PENGUIFLOW_QA_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    import importlib.metadata

    version = importlib.metadata.version("mlflow-skinny")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CLIENT, json.dumps(config)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=35,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode() + (exc.stderr or b"").decode()
        (evidence / f"http-{version}-{scenario}-{async_export}.log").write_text(output)
        pytest.fail(output + "\nclient process exceeded 35 seconds")
    (evidence / f"http-{version}-{scenario}-{async_export}.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(
        next(
            line.removeprefix("HTTP_QA_RESULT ")
            for line in result.stdout.splitlines()
            if line.startswith("HTTP_QA_RESULT ")
        )
    )
    record["tracking_requests"] = list(http_servers["state"]["requests"])
    if scenario == "off":
        assert not record["tracking_requests"]
    elif scenario != "refused":
        assert any(request["mode"] == scenario for request in record["tracking_requests"])
        if scenario.startswith("export_"):
            assert any(
                request["mode"] == scenario and "traces" in request["path"] for request in record["tracking_requests"]
            ), record
    path = evidence / "http-results.json"
    records = json.loads(path.read_text()) if path.exists() else []
    records = [
        old
        for old in records
        if (old["version"], old["scenario"], old["async_export"]) != (version, scenario, async_export)
    ]
    path.write_text(json.dumps(records + [record], indent=2))


@pytest.mark.parametrize("async_export", ["false", "true"])
def test_existing_application_root_keeps_all_children(http_servers, tmp_path: Path, async_export: str) -> None:
    test_planner_with_real_http_tracking(http_servers, tmp_path, "external", async_export)


@pytest.mark.parametrize("client_kind", ["standalone", "standalone_native", "standalone_native_tools"])
def test_standalone_client_under_application_root(http_servers, tmp_path: Path, client_kind: str) -> None:
    test_planner_with_real_http_tracking(http_servers, tmp_path, client_kind, "false")
