"""Behavior tests for generated MLflow telemetry."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
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
        self.status = status
        self.ended = True


class _SpanContext:
    def __init__(self, span: _Span) -> None:
        self.span = span
        self.exit_type: type[BaseException] | None = None

    def __enter__(self) -> _Span:
        return self.span

    def __exit__(self, exc_type: type[BaseException] | None, *_args: Any) -> None:
        self.exit_type = exc_type
        self.span.ended = True


class _Mlflow:
    def __init__(self) -> None:
        self.roots: list[_Span] = []
        self.root_contexts: list[_SpanContext] = []
        self.tools: list[_Span] = []

    def start_span(self, *, name: str, span_type: str) -> _SpanContext:
        span = _Span(name, span_type)
        context = _SpanContext(span)
        self.roots.append(span)
        self.root_contexts.append(context)
        return context

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

    fake_mlflow = _Mlflow()
    monkeypatch.setitem(sys.modules, "mlflow", SimpleNamespace(
        start_span=fake_mlflow.start_span,
        start_span_no_context=fake_mlflow.start_span_no_context,
    ))

    package_name = name.replace("-", "_")
    path = tmp_path / name / "src" / package_name / "telemetry.py"
    spec = importlib.util.spec_from_file_location(f"_generated_{package_name}_telemetry", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, fake_mlflow


def _event(event_type: str, call_id: str, payload: str) -> PlannerEvent:
    key = "args_json" if event_type == "tool_call_start" else "result_json"
    return PlannerEvent(
        event_type=event_type,
        ts=1.0,
        trajectory_step=2,
        extra={
            "tool_call_id": call_id,
            "tool_name": "lookup",
            "action_seq": 3,
            key: payload,
        },
    )


@pytest.mark.parametrize(
    "template",
    ["minimal", "react", "parallel", "rag_server", "wayfinder", "analyst", "enterprise"],
)
def test_generated_spans_record_inputs_outputs_and_tool_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: str,
) -> None:
    module, mlflow = _load_telemetry(tmp_path, monkeypatch, template)
    telemetry = module.AgentTelemetry(flow_name="trace-agent", logger=logging.getLogger("test.telemetry"))

    with telemetry.trace_agent_run(
        operation="execute", trace_id="trace-1", inputs={"query": "input-secret"}
    ):
        telemetry.record_planner_event(_event("tool_call_start", "call-1", '{"term":"input-secret"}'))
        telemetry.record_planner_event(_event("tool_call_end", "call-1", "{}"))
        assert not mlflow.tools[0].ended
        telemetry.record_planner_event(_event("tool_call_result", "call-1", '{"value":"output-secret"}'))
        telemetry.record_agent_output({"answer": "output-secret"})

    root = mlflow.roots[0]
    tool = mlflow.tools[0]
    assert root.span_type == "AGENT"
    assert root.status == "OK"
    assert root.ended
    assert tool.span_type == "TOOL"
    assert tool.parent is root
    assert tool.status == "OK"
    assert tool.ended
    assert root.inputs == {"query": "input-secret"}
    assert root.outputs == {"answer": "output-secret"}
    assert tool.inputs == {"term": "input-secret"}
    assert tool.outputs == {"value": "output-secret"}
    recorded_attributes = str(root.attributes) + str(tool.attributes)
    assert "input-secret" not in recorded_attributes
    assert "output-secret" not in recorded_attributes


def test_generated_telemetry_handles_invalid_tool_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = module.AgentTelemetry(flow_name="trace-agent", logger=logging.getLogger("test.telemetry"))

    with telemetry.trace_agent_run(operation="execute", trace_id="trace-invalid"):
        telemetry.record_planner_event(_event("tool_call_start", "call-1", "invalid args"))
        telemetry.record_planner_event(_event("tool_call_result", "call-1", "invalid result"))

    assert mlflow.tools[0].inputs == "invalid args"
    assert mlflow.tools[0].outputs == "invalid result"
    assert mlflow.tools[0].ended


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
    monkeypatch.setitem(sys.modules, "mlflow", SimpleNamespace(
        start_span=fake_mlflow.start_span,
        start_span_no_context=fake_mlflow.start_span_no_context,
    ))
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
    response = await orchestrator.execute(
        "original question", tenant_id="tenant", user_id="user", session_id="session"
    )

    assert response.answer == "final answer"
    assert fake_mlflow.roots[0].inputs == {"query": "original question"}
    assert fake_mlflow.roots[0].outputs == {"answer": "final answer"}


@pytest.mark.asyncio
async def test_generated_concurrent_runs_keep_tool_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = module.AgentTelemetry(flow_name="trace-agent", logger=logging.getLogger("test.concurrent"))

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


def test_generated_error_and_cleanup_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, mlflow = _load_telemetry(tmp_path, monkeypatch)
    telemetry = module.AgentTelemetry(flow_name="trace-agent", logger=logging.getLogger("test.cleanup"))

    with pytest.raises(RuntimeError, match="private failure"):
        with telemetry.trace_agent_run(operation="execute", trace_id="trace-error"):
            telemetry.record_planner_event(_event("tool_call_start", "unfinished", "{}"))
            raise RuntimeError("private failure")

    assert mlflow.roots[0].status == "ERROR"
    assert mlflow.root_contexts[0].exit_type is None
    assert mlflow.tools[0].status == "UNSET"
    assert mlflow.tools[0].ended
    assert "private failure" not in str(mlflow.roots[0].attributes)
