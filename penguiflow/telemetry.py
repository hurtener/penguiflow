"""Optional, metadata-only MLflow tracing for planner runs and tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .planner.models import PlannerEvent

_LOGGER = logging.getLogger("penguiflow.telemetry")


@dataclass
class _Run:
    span: Any
    tools: dict[str, Any] = field(default_factory=dict)


class MlflowTelemetry:
    """Record agent/tool spans with MLflow >=3.8.1, without capturing payloads."""

    def __init__(self, *, flow_name: str) -> None:
        self._flow_name = flow_name
        self._mlflow: Any = None
        self._unavailable = False
        self._run: ContextVar[_Run | None] = ContextVar("penguiflow_mlflow_run", default=None)

    def _module(self) -> Any | None:
        if not self._unavailable and self._mlflow is None:
            try:
                import mlflow
            except ImportError:
                self._unavailable = True
                _LOGGER.warning("MLflow telemetry disabled: install penguiflow[mlflow]")
            else:
                self._mlflow = mlflow
        return self._mlflow

    @contextmanager
    def trace_agent_run(self, *, operation: str, trace_id: str | None = None) -> Iterator[None]:
        mlflow = self._module()
        if mlflow is None:
            yield
            return
        with mlflow.start_span(name=f"agent.{operation}", span_type="AGENT") as span:
            attributes = {"flow.name": self._flow_name, "operation": operation}
            if trace_id is not None:
                attributes["trace_id"] = trace_id
            span.set_attributes(attributes)
            run = _Run(span=span)
            token = self._run.set(run)
            outcome = "ok"
            try:
                yield
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._run.reset(token)
                for tool in run.tools.values():
                    self._end(tool, "ERROR", "unfinished")
                span.set_attributes({"outcome": outcome})
                span.set_status("OK" if outcome == "ok" else "ERROR")

    def record_planner_event(self, event: PlannerEvent) -> None:
        run = self._run.get()
        if run is None or event.event_type not in {"tool_call_start", "tool_call_result"}:
            return
        call_id = event.extra["tool_call_id"]
        if event.event_type == "tool_call_start":
            if call_id in run.tools:
                return
            tool = self._mlflow.start_span_no_context(
                name=event.extra["tool_name"], span_type="TOOL", parent_span=run.span
            )
            run.tools[call_id] = tool
            tool.set_attributes(
                {
                    "tool.call_id": call_id,
                    "tool.name": event.extra["tool_name"],
                    "trajectory.step": event.trajectory_step,
                    "action.sequence": event.extra.get("action_seq", 0),
                }
            )
        else:
            tool = run.tools.pop(call_id, None)
            if tool is None:
                return
            result = event.extra.get("result_json")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = None
            failed = isinstance(result, dict) and (result.get("error") is not None or result.get("failure") is not None)
            self._end(tool, "ERROR" if failed else "OK", "error" if failed else "success")

    @staticmethod
    def _end(span: Any, status: str, outcome: str) -> None:
        # Cleanup must preserve application exceptions and close the remaining spans.
        try:
            span.set_attributes({"outcome": outcome})
            span.end(status=status)
        except Exception:
            _LOGGER.warning("MLflow tool span close failed", exc_info=True)


__all__ = ["MlflowTelemetry"]
