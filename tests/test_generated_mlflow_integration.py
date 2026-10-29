"""Optional real-SDK coverage for generated MLflow-enabled agents."""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from penguiflow.cli.new import run_new
from penguiflow.planner import PlannerEvent


def _generated_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    name = "real_mlflow_agent"
    result = run_new(
        name=name,
        template="react",
        output_dir=tmp_path,
        quiet=True,
        no_memory=True,
        with_mlflow=True,
    )
    assert result.success

    monkeypatch.syspath_prepend(str(tmp_path / name / "src"))
    for module_name in tuple(sys.modules):
        if module_name == name or module_name.startswith(f"{name}."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    importlib.invalidate_caches()
    return importlib.import_module(f"{name}.telemetry")


def _flush_traces(mlflow: Any) -> None:
    flush = getattr(mlflow, "flush_trace_async_logging", None)
    if flush is not None:
        flush()


def _only_trace(mlflow: Any) -> Any:
    traces = mlflow.search_traces(return_type="list")
    assert len(traces) == 1
    return traces[0]


def _span_status(span: Any) -> str:
    return str(getattr(span, "status", "")).upper()


@pytest.fixture
def real_mlflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    mlflow = pytest.importorskip("mlflow")
    pytest.importorskip("litellm")
    from litellm.integrations.mlflow import MlflowLogger
    from mlflow.tracking import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
    try:
        # The generated dependency must provide the client required by LiteLLM's
        # optional MLflow logger.
        assert isinstance(MlflowClient(), MlflowClient)
        assert isinstance(MlflowLogger(), MlflowLogger)
        yield mlflow
    finally:
        _flush_traces(mlflow)
        mlflow.set_tracking_uri(previous_uri)


def test_generated_telemetry_marks_real_mlflow_tool_outcomes_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_mlflow: Any
) -> None:
    """Tool span status follows the explicit planner outcome."""

    telemetry_module = _generated_telemetry(tmp_path, monkeypatch)
    telemetry = telemetry_module.AgentTelemetry(
        flow_name="real-mlflow",
        logger=logging.getLogger(__name__),
        mlflow_enabled=True,
    )
    with telemetry.trace_agent_run(operation="execute", trace_id="tool-outcomes"):
        telemetry.record_planner_event(
            PlannerEvent(
                event_type="tool_call_start",
                ts=1.0,
                trajectory_step=1,
                extra={"tool_call_id": "successful", "tool_name": "lookup", "args_json": "{}"},
            )
        )
        telemetry.record_planner_event(
            PlannerEvent(
                event_type="tool_call_result",
                ts=1.0,
                trajectory_step=1,
                extra={
                    "tool_call_id": "successful",
                    "result_json": '{"ok":true,"error":null,"value":42}',
                    "outcome": "success",
                },
            )
        )
        telemetry.record_planner_event(
            PlannerEvent(
                event_type="tool_call_start",
                ts=1.0,
                trajectory_step=2,
                extra={"tool_call_id": "failed", "tool_name": "lookup", "args_json": "{}"},
            )
        )
        telemetry.record_planner_event(
            PlannerEvent(
                event_type="tool_call_result",
                ts=1.0,
                trajectory_step=2,
                extra={"tool_call_id": "failed", "result_json": "null", "outcome": "error"},
            )
        )
        telemetry.record_planner_event(
            PlannerEvent(
                event_type="tool_call_start",
                ts=1.0,
                trajectory_step=3,
                extra={"tool_call_id": "unfinished", "tool_name": "lookup", "args_json": "{}"},
            )
        )

    _flush_traces(real_mlflow)
    trace = _only_trace(real_mlflow)
    tools = {span.attributes["tool.call_id"]: span for span in trace.data.spans if "tool.call_id" in span.attributes}
    assert tools["successful"].attributes["outcome"] == "success"
    assert "OK" in _span_status(tools["successful"])
    assert tools["failed"].attributes["outcome"] == "error"
    assert "ERROR" in _span_status(tools["failed"])
    assert tools["unfinished"].attributes["outcome"] == "unfinished"
    assert "ERROR" in _span_status(tools["unfinished"])
