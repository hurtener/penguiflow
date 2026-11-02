"""Focused coverage for automatic agent/tool tracing and run isolation."""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from penguiflow.planner import PlannerEvent, PlannerFinish, ReactPlanner
from penguiflow.telemetry import MlflowTelemetry


def _event(kind: str, result: str = "{}") -> PlannerEvent:
    return PlannerEvent(
        event_type=kind,
        ts=0,
        trajectory_step=0,
        extra={"tool_call_id": "same-id", "tool_name": "lookup", "result_json": result},
    )


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    recorded: list[Any] = []

    def create(name: str, span_type: str, parent_span: Any = None) -> Any:
        span = MagicMock(name=name, span_type=span_type)
        span.parent_span = parent_span
        recorded.append(span)
        return span

    @contextmanager
    def start_span(**kwargs: Any) -> Any:
        yield create(**kwargs)

    monkeypatch.setitem(
        sys.modules,
        "mlflow",
        MagicMock(
            start_span=start_span,
            start_span_no_context=create,
        ),
    )
    return recorded


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "session-one"])
@pytest.mark.parametrize("operation", ["run", "resume"])
async def test_environment_tracing_preserves_callbacks(
    spans: list[Any], monkeypatch: pytest.MonkeyPatch, session_id: str | None, operation: str
) -> None:
    monkeypatch.setenv("PENGUIFLOW_LLM_TRACING", "mlflow")
    events: list[PlannerEvent] = []

    async def execute(planner: ReactPlanner, *_args: Any, **_kwargs: Any) -> PlannerFinish:
        planner._emit_event(_event("tool_call_start"))
        planner._emit_event(_event("tool_call_result"))
        return PlannerFinish(reason="answer_complete", payload="done", metadata={})

    monkeypatch.setattr(f"penguiflow.planner.react._{operation}_impl", execute)
    planner = ReactPlanner(catalog=[], llm_client=MagicMock(), event_callback=events.append)
    await getattr(planner, operation)("query-or-token", tool_context={"session_id": session_id})

    root, tool = spans  # A session-dispatched operation must not create a second root.
    assert tool.parent_span is root
    tool.end.assert_called_once_with(status="OK")
    assert [event.event_type for event in events] == ["tool_call_start", "tool_call_result"]


@pytest.mark.parametrize("error", [None, ValueError("original"), asyncio.CancelledError()])
def test_unfinished_tools_close_without_masking_errors(spans: list[Any], error: BaseException | None) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")

    def execute() -> None:
        with telemetry.trace_agent_run(operation="run"):
            telemetry.record_planner_event(_event("tool_call_start"))
            if error is not None:
                raise error

    if error is None:
        execute()
    else:
        with pytest.raises(type(error)) as caught:
            execute()
        assert caught.value is error
    root, tool = spans
    root.set_status.assert_called_once_with("ERROR" if error is not None else "OK")
    tool.end.assert_called_once_with(status="ERROR")


def test_failed_tool_result(spans: list[Any]) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")
    with telemetry.trace_agent_run(operation="run"):
        telemetry.record_planner_event(_event("tool_call_start"))
        telemetry.record_planner_event(_event("tool_call_result", '{"error": "failed"}'))
    spans[1].end.assert_called_once_with(status="ERROR")


@pytest.mark.asyncio
async def test_concurrent_runs_isolate_identical_tool_ids(spans: list[Any]) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")

    async def execute() -> None:
        with telemetry.trace_agent_run(operation="run"):
            telemetry.record_planner_event(_event("tool_call_start"))
            await asyncio.sleep(0)
            telemetry.record_planner_event(_event("tool_call_result"))

    await asyncio.gather(execute(), execute())
    first_root, first_tool, second_root, second_tool = spans
    assert first_tool.parent_span is first_root and second_tool.parent_span is second_root
    for tool in (first_tool, second_tool):
        tool.end.assert_called_once_with(status="OK")


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, "off", "mlflow"])
async def test_disabled_or_missing_sdk_does_not_block_agent(
    monkeypatch: pytest.MonkeyPatch, setting: str | None
) -> None:
    monkeypatch.delenv("PENGUIFLOW_LLM_TRACING", raising=False)
    if setting is not None:
        monkeypatch.setenv("PENGUIFLOW_LLM_TRACING", setting)
    monkeypatch.setitem(sys.modules, "mlflow", None)
    result = PlannerFinish(reason="answer_complete", payload="done", metadata={})

    async def execute(*_args: Any, **_kwargs: Any) -> PlannerFinish:
        return result

    monkeypatch.setattr("penguiflow.planner.react._run_impl", execute)
    assert await ReactPlanner(catalog=[], llm_client=MagicMock()).run("query") is result
