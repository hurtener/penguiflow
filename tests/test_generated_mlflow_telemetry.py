"""Tests for the shared MLflow telemetry used by generated projects."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
from contextvars import ContextVar, Token
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from penguiflow.cli.new import run_new
from penguiflow.planner import PlannerEvent, PlannerFinish


class _Span:
    def __init__(self, name: str, span_type: str, parent: _Span | None = None) -> None:
        self.name = name
        self.span_type = span_type
        self.parent = parent
        self.attributes: dict[str, Any] = {}
        self.inputs: Any = None
        self.outputs: Any = None
        self.status: str | None = None
        self.ended = False

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes.update(attributes)

    def set_inputs(self, inputs: Any) -> None:
        self.inputs = inputs

    def set_outputs(self, outputs: Any) -> None:
        self.outputs = outputs

    def set_status(self, status: str) -> None:
        self.status = status

    def end(self, *, status: str) -> None:
        self.status = "OK" if status == "UNSET" else status
        self.ended = True


class _SpanContext:
    def __init__(self, span: _Span, active: ContextVar[_Span | None]) -> None:
        self.span = span
        self.active = active
        self.token: Token[_Span | None] | None = None

    def __enter__(self) -> _Span:
        self.token = self.active.set(self.span)
        return self.span

    def __exit__(self, *_args: Any) -> None:
        assert self.token is not None
        self.span.ended = True
        self.active.reset(self.token)


class _Mlflow:
    def __init__(self) -> None:
        self.roots: list[_Span] = []
        self.tools: list[_Span] = []
        self.llm_spans: list[_Span] = []
        self.active: ContextVar[_Span | None] = ContextVar("fake_mlflow_active", default=None)

    def start_span(self, *, name: str, span_type: str) -> _SpanContext:
        parent = self.active.get()
        span = _Span(name, span_type, parent)
        if parent is None:
            self.roots.append(span)
        else:
            self.llm_spans.append(span)
        return _SpanContext(span, self.active)

    def start_span_no_context(self, *, name: str, span_type: str, parent_span: _Span) -> _Span:
        span = _Span(name, span_type, parent_span)
        self.tools.append(span)
        return span


def _load_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: str = "react",
) -> tuple[Any, _Mlflow]:
    name = f"{template}-trace-agent"
    result = run_new(
        name=name,
        template=template,
        output_dir=tmp_path,
        quiet=True,
        with_mlflow=True,
    )
    assert result.success

    mlflow = _Mlflow()
    monkeypatch.setitem(
        sys.modules,
        "mlflow",
        SimpleNamespace(
            start_span=mlflow.start_span,
            start_span_no_context=mlflow.start_span_no_context,
        ),
    )
    package_name = name.replace("-", "_")
    path = tmp_path / name / "src" / package_name / "telemetry.py"
    spec = importlib.util.spec_from_file_location(f"_generated_{package_name}_telemetry", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, mlflow


def _event(event_type: str, call_id: str, payload: str) -> PlannerEvent:
    key = "args_json" if event_type == "tool_call_start" else "result_json"
    return PlannerEvent(
        event_type=event_type,
        ts=1.0,
        trajectory_step=2,
        extra={
            "outcome": "success",
            "tool_call_id": call_id,
            "tool_name": "lookup",
            "action_seq": 3,
            key: payload,
        },
    )


def test_generated_agent_uses_library_agent_and_tool_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry_module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = telemetry_module.AgentTelemetry(
        flow_name="trace-agent",
        logger=logging.getLogger("test.telemetry"),
        mlflow_enabled=True,
    )

    with telemetry.trace_agent_run(operation="execute", trace_id="trace-1", inputs={"query": "input-secret"}):
        telemetry.record_planner_event(_event("tool_call_start", "call-1", '{"term":"input-secret"}'))
        telemetry.record_planner_event(_event("tool_call_result", "call-1", '{"value":"output-secret"}'))
        telemetry.record_agent_output({"answer": "output-secret"})

    root = mlflow.roots[0]
    tool = mlflow.tools[0]
    assert root.span_type == "AGENT" and root.status == "OK" and root.ended
    assert tool.span_type == "TOOL" and tool.parent is root and tool.status == "OK" and tool.ended
    assert root.inputs == {"query": "input-secret"}
    assert root.outputs == {"answer": "output-secret"}
    assert tool.inputs == {"term": "input-secret"}
    assert tool.outputs == {"value": "output-secret"}
    assert "input-secret" not in str(root.attributes) + str(tool.attributes)
    assert "output-secret" not in str(root.attributes) + str(tool.attributes)


def test_generated_telemetry_handles_invalid_json_and_unfinished_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry_module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = telemetry_module.AgentTelemetry(
        flow_name="trace-agent",
        logger=logging.getLogger("test.unfinished"),
        mlflow_enabled=True,
    )
    with telemetry.trace_agent_run(operation="execute", trace_id="unfinished"):
        telemetry.record_planner_event(_event("tool_call_start", "invalid", "invalid args"))
        telemetry.record_planner_event(_event("tool_call_result", "invalid", "invalid result"))
        telemetry.record_planner_event(_event("tool_call_start", "unfinished", "{}"))

    assert mlflow.tools[0].inputs == "invalid args"
    assert mlflow.tools[0].outputs == "invalid result"
    assert mlflow.tools[0].ended
    assert mlflow.tools[1].attributes["outcome"] == "unfinished"
    assert mlflow.tools[1].status == "ERROR"


@pytest.mark.asyncio
async def test_generated_concurrent_runs_keep_tool_spans_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry_module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = telemetry_module.AgentTelemetry(
        flow_name="trace-agent",
        logger=logging.getLogger("test.concurrent"),
        mlflow_enabled=True,
    )

    async def run(trace_id: str) -> None:
        with telemetry.trace_agent_run(operation="execute", trace_id=trace_id):
            telemetry.record_planner_event(_event("tool_call_start", "shared-id", "{}"))
            await asyncio.sleep(0)
            telemetry.record_planner_event(_event("tool_call_result", "shared-id", "{}"))

    await asyncio.gather(run("trace-a"), run("trace-b"))
    roots = {span.attributes["trace_id"]: span for span in mlflow.roots}
    assert len(mlflow.tools) == 2
    assert {span.parent for span in mlflow.tools} == {roots["trace-a"], roots["trace-b"]}
    assert all(span.status == "OK" and span.ended for span in mlflow.tools)


def test_disabled_mlflow_integration_does_not_import_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from penguiflow.observability import AgentTelemetry

    monkeypatch.setitem(sys.modules, "mlflow", None)
    telemetry = AgentTelemetry(flow_name="plain-agent", logger=logging.getLogger("test.disabled"))
    with telemetry.trace_agent_run(operation="execute", trace_id="disabled"):
        telemetry.record_agent_output({"answer": "ok"})
    assert telemetry.events == []


def test_missing_mlflow_dependency_does_not_break_enabled_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from penguiflow.observability import AgentTelemetry

    monkeypatch.setitem(sys.modules, "mlflow", None)
    telemetry = AgentTelemetry(
        flow_name="plain-agent",
        logger=logging.getLogger("test.missing_mlflow"),
        mlflow_enabled=True,
    )
    with telemetry.trace_agent_run(operation="execute", trace_id="without-mlflow"):
        telemetry.record_agent_output({"answer": "ok"})
    assert telemetry._mlflow_unavailable


def test_generated_trace_does_not_use_litellm_autolog_or_queue_flush(
    tmp_path: Path,
) -> None:
    name = "no-flush-agent"
    result = run_new(name=name, template="react", output_dir=tmp_path, quiet=True, with_mlflow=True)
    assert result.success
    package = tmp_path / name / "src" / name.replace("-", "_")
    telemetry = (package / "telemetry.py").read_text()
    orchestrator = (package / "orchestrator.py").read_text()
    assert "penguiflow.observability" in telemetry
    assert "flush_llm_logging" not in telemetry + orchestrator
    assert "autolog" not in telemetry + orchestrator


@pytest.mark.asyncio
async def test_generated_react_orchestrator_records_query_and_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "react-io-agent"
    result = run_new(
        name=name,
        template="react",
        output_dir=tmp_path,
        quiet=True,
        with_mlflow=True,
        no_memory=True,
    )
    assert result.success

    fake_mlflow = _Mlflow()
    monkeypatch.setitem(
        sys.modules,
        "mlflow",
        SimpleNamespace(
            start_span=fake_mlflow.start_span,
            start_span_no_context=fake_mlflow.start_span_no_context,
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path / name / "src"))
    orchestrator_module = importlib.import_module("react_io_agent.orchestrator")

    async def run(**_kwargs: Any) -> PlannerFinish:
        return PlannerFinish(reason="answer_complete", payload={"answer": "final answer"})

    monkeypatch.setattr(
        orchestrator_module,
        "build_planner",
        lambda *_args, **_kwargs: SimpleNamespace(planner=SimpleNamespace(run=run)),
    )
    orchestrator = orchestrator_module.ReactIoAgentOrchestrator(orchestrator_module.Config())
    response = await orchestrator.execute("original question", tenant_id="tenant", user_id="user", session_id="session")

    assert response.answer == "final answer"
    assert fake_mlflow.roots[0].inputs == {"query": "original question"}
    assert fake_mlflow.roots[0].outputs == {"answer": "final answer"}
