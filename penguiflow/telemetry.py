"""Optional, metadata-only MLflow tracing for planner runs and tools."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .llm.tracing import finish_mlflow_spans, prepare_mlflow, safe_mlflow_span

if TYPE_CHECKING:
    from .planner.models import PlannerFinish, PlannerPause
    from .planner.tool_calls import ToolCallOutcome

_LOGGER = logging.getLogger("penguiflow.telemetry")


@dataclass
class _Run:
    result: PlannerFinish | PlannerPause | None = None


@dataclass
class _ToolRun:
    result: ToolCallOutcome | None = None


class MlflowTelemetry:
    """Record agent/tool spans with MLflow >=3.18.0, without capturing payloads."""

    def __init__(self, *, flow_name: str) -> None:
        self._flow_name = flow_name
        self._mlflow: Any = None
        self._unavailable = False

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

    async def prepare(self) -> None:
        mlflow = self._mlflow
        if mlflow is None and not self._unavailable:
            mlflow = await asyncio.to_thread(self._module)
        if mlflow is not None:
            await prepare_mlflow(mlflow)

    @contextmanager
    def trace_agent_run(self, *, operation: str, trace_id: str | None = None) -> Iterator[_Run | None]:
        mlflow = self._module()
        if mlflow is None:
            yield None
            return
        with safe_mlflow_span(mlflow, name=f"agent.{operation}", span_type="AGENT") as span:
            if span is None:
                yield None
                return
            attributes = {"flow.name": self._flow_name, "operation": operation}
            if trace_id is not None:
                attributes["trace_id"] = trace_id
            span.set_attributes(attributes)
            run = _Run()
            outcome = "ok"
            try:
                yield run
            except BaseException:
                outcome = "error"
                raise
            finally:
                from .planner.models import PlannerPause

                finish_reason = run.result.reason if run.result is not None else None
                if outcome != "error" and run.result is not None:
                    if isinstance(run.result, PlannerPause):
                        outcome = "paused"
                    elif (
                        finish_reason != "answer_complete"
                        or run.result.metadata.get("trajectory_metadata", {}).get("execution_outcome") == "error"
                    ):
                        outcome = "error"
                self._finish(span, outcome, finish_reason=finish_reason)

    @asynccontextmanager
    async def atrace_agent_run(self, *, operation: str, trace_id: str | None = None) -> AsyncIterator[_Run | None]:
        try:
            with self.trace_agent_run(operation=operation, trace_id=trace_id) as run:
                yield run
        finally:
            await finish_mlflow_spans(self._mlflow)

    @contextmanager
    def trace_tool_call(
        self, *, tool_name: str, tool_call_id: str, trajectory_step: int, action_seq: int
    ) -> Iterator[_ToolRun | None]:
        mlflow = self._module()
        if mlflow is None:
            yield None
            return
        with safe_mlflow_span(mlflow, name=tool_name, span_type="TOOL") as span:
            if span is None:
                yield None
                return
            span.set_attributes(
                {
                    "tool.call_id": tool_call_id,
                    "tool.name": tool_name,
                    "trajectory.step": trajectory_step,
                    "action.sequence": action_seq,
                }
            )
            run = _ToolRun()
            outcome = "unfinished"
            try:
                yield run
            except BaseException:
                outcome = "error"
                raise
            finally:
                if outcome != "error" and run.result is not None:
                    if run.result.pause is not None:
                        outcome = "paused"
                    elif run.result.error is not None or run.result.failure is not None:
                        outcome = "error"
                    else:
                        outcome = "success"
                self._finish(span, outcome)

    @staticmethod
    def _finish(span: Any, outcome: str, *, finish_reason: str | None = None) -> None:
        try:
            attributes = {"outcome": outcome}
            if finish_reason is not None:
                attributes["finish_reason"] = finish_reason
            span.set_attributes(attributes)
            span.set_status("OK" if outcome in {"ok", "success"} else "ERROR")
        except Exception:
            _LOGGER.warning("MLflow span close failed", exc_info=True)


__all__ = ["MlflowTelemetry"]
