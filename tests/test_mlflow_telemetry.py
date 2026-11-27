"""Focused coverage for automatic agent/tool tracing and run isolation."""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause, ReactPlanner
from penguiflow.planner.tool_calls import ToolCallOutcome, execute_tool_call
from penguiflow.telemetry import MlflowTelemetry


def _event(kind: str) -> PlannerEvent:
    return PlannerEvent(event_type=kind, ts=0, trajectory_step=0)


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    recorded: list[Any] = []
    active: ContextVar[Any] = ContextVar("test_active_span", default=None)

    @contextmanager
    def start_span(_mlflow: Any, *, name: str, span_type: str) -> Any:
        span = MagicMock(name=name, span_type=span_type)
        span.parent_span = active.get()
        recorded.append(span)
        token = active.set(span)
        try:
            yield span
        finally:
            active.reset(token)

    monkeypatch.setitem(sys.modules, "mlflow", MagicMock())
    monkeypatch.setattr("penguiflow.telemetry.safe_mlflow_span", start_span)
    monkeypatch.setattr("penguiflow.telemetry.prepare_mlflow", AsyncMock())
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
        planner._emit_event(_event("step_start"))
        return PlannerFinish(reason="answer_complete", payload="done", metadata={})

    monkeypatch.setattr(f"penguiflow.planner.react._{operation}_impl", execute)
    planner = ReactPlanner(catalog=[], llm_client=MagicMock(), event_callback=events.append)
    await getattr(planner, operation)("query-or-token", tool_context={"session_id": session_id})

    assert len(spans) == 1  # Session dispatch must not create a second root.
    assert [event.event_type for event in events] == ["step_start"]


@pytest.mark.parametrize("error", [None, ValueError("original"), asyncio.CancelledError()])
def test_unfinished_tools_close_without_masking_errors(spans: list[Any], error: BaseException | None) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")

    def execute() -> None:
        with telemetry.trace_agent_run(operation="run"):
            with telemetry.trace_tool_call(tool_name="lookup", tool_call_id="id", trajectory_step=0, action_seq=1):
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
    tool.set_status.assert_called_once_with("ERROR")
    tool.set_attributes.assert_any_call({"outcome": "error" if error is not None else "unfinished"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (ToolCallOutcome(observation={"found": False, "error": "customer not found", "results": []}), "success"),
        (ToolCallOutcome(observation={"ok": True, "error": False, "value": 42}), "success"),
        (ToolCallOutcome(observation={"value": 1, "failure": {}}), "success"),
        (ToolCallOutcome(error="failed"), "error"),
        (ToolCallOutcome(failure={}), "error"),
        (ToolCallOutcome(pause=PlannerPause(resume_token="pause-token", reason="approval_required")), "paused"),
    ],
)
async def test_tool_execution_uses_explicit_outcome(
    spans: list[Any], monkeypatch: pytest.MonkeyPatch, outcome: ToolCallOutcome, expected: str
) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")
    monkeypatch.setattr("penguiflow.planner.tool_calls._execute_tool_call", AsyncMock(return_value=outcome))
    planner = SimpleNamespace(_mlflow_telemetry=telemetry)
    with telemetry.trace_agent_run(operation="run"):
        assert (
            await execute_tool_call(
                planner,
                trajectory=SimpleNamespace(steps=[]),
                spec=SimpleNamespace(name="lookup"),
                parsed_args=MagicMock(),
                tool_call_id="id",
                action_seq=1,
                step_index=0,
                artifact_collector=None,
                source_collector=None,
            )
            is outcome
        )
    root, tool = spans
    assert tool.parent_span is root
    tool.set_attributes.assert_any_call({"outcome": expected})
    tool.set_status.assert_called_once_with("OK" if expected == "success" else "ERROR")


@pytest.mark.asyncio
async def test_concurrent_runs_isolate_identical_tool_ids(spans: list[Any]) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")

    async def execute() -> None:
        with telemetry.trace_agent_run(operation="run"):
            with telemetry.trace_tool_call(
                tool_name="lookup", tool_call_id="same-id", trajectory_step=0, action_seq=1
            ) as trace:
                await asyncio.sleep(0)
                assert trace is not None
                trace.result = ToolCallOutcome(observation={})

    await asyncio.gather(execute(), execute())
    first_root, first_tool, second_root, second_tool = spans
    assert first_tool.parent_span is first_root and second_tool.parent_span is second_root
    for tool in (first_tool, second_tool):
        tool.set_status.assert_called_once_with("OK")


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


@pytest.mark.parametrize(
    ("result", "outcome", "status"),
    [
        (PlannerFinish(reason="answer_complete"), "ok", "OK"),
        (
            PlannerFinish(reason="answer_complete", metadata={"trajectory_metadata": {"execution_outcome": "error"}}),
            "error",
            "ERROR",
        ),
        (PlannerFinish(reason="answer_complete", payload={"error": "business data"}), "ok", "OK"),
        (PlannerFinish(reason="no_path"), "error", "ERROR"),
        (PlannerFinish(reason="budget_exhausted"), "error", "ERROR"),
        (PlannerPause(resume_token="pause-token", reason="approval_required"), "paused", "ERROR"),
    ],
)
def test_agent_status_records_finish_reason(
    spans: list[Any], result: PlannerFinish | PlannerPause, outcome: str, status: str
) -> None:
    telemetry = MlflowTelemetry(flow_name="agent")
    with telemetry.trace_agent_run(operation="run") as trace:
        assert trace is not None
        trace.result = result
    spans[0].set_attributes.assert_any_call({"outcome": outcome, "finish_reason": result.reason})
    spans[0].set_status.assert_called_once_with(status)
