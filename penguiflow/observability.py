"""Reusable agent telemetry and optional MLflow tracing."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .planner.models import PlannerEvent

_LOGGER = logging.getLogger("penguiflow.observability")


@dataclass
class StatusUpdate:
    """Status update payload for UI hooks."""

    status: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class _MlflowRun:
    span: Any
    tools: dict[str, Any] = field(default_factory=dict)


class AgentTelemetry:
    """Planner event logging with opt-in agent and tool MLflow spans.

    MLflow is imported lazily and is only needed when ``mlflow_enabled`` is
    true. LLM call spans are emitted by the configured LLM trace sink, rather
    than by planner events or an SDK background queue.
    """

    def __init__(
        self,
        *,
        flow_name: str,
        logger: logging.Logger,
        status_callback: Callable[[StatusUpdate], None] | None = None,
        mlflow_enabled: bool = False,
    ) -> None:
        self.flow_name = flow_name
        self.logger = logger
        self._status_callback = status_callback
        self.events: list[PlannerEvent] = []
        self._mlflow_enabled = mlflow_enabled
        self._mlflow: Any | None = None
        self._mlflow_unavailable = False
        self._mlflow_run: ContextVar[_MlflowRun | None] = ContextVar(
            f"penguiflow_mlflow_run_{id(self)}", default=None
        )

    def record_planner_event(self, event: PlannerEvent) -> None:
        """Synchronous callback attached to the planner."""
        self.events.append(event)
        payload: dict[str, Any] = {"flow": self.flow_name, **event.to_payload()}
        if self._mlflow_enabled:
            self._record_mlflow_event(event)

        if event.event_type == "node_error":
            self.logger.error("planner_node_error", extra=payload)
        elif event.event_type == "finish":
            self.logger.info("planner_finish", extra=payload)
        else:
            self.logger.debug(event.event_type, extra=payload)

    def _mlflow_module(self) -> Any | None:
        if not self._mlflow_enabled or self._mlflow_unavailable:
            return None
        if self._mlflow is None:
            try:
                import mlflow
            except ImportError as exc:
                self._mlflow_unavailable = True
                self.logger.warning("mlflow telemetry disabled: package unavailable (%s)", exc)
                return None
            if not hasattr(mlflow, "start_span"):
                self._mlflow_unavailable = True
                self.logger.warning("mlflow telemetry disabled: tracing API unavailable")
                return None
            self._mlflow = mlflow
        return self._mlflow

    def _set_attributes(self, span: Any, attributes: dict[str, Any]) -> None:
        try:
            span.set_attributes(attributes)
        except Exception:
            self.logger.debug("mlflow span attribute error", exc_info=True)

    def _set_inputs(self, span: Any, inputs: Any) -> None:
        try:
            span.set_inputs(inputs)
        except Exception:
            self.logger.debug("mlflow span input error", exc_info=True)

    def _set_outputs(self, span: Any, outputs: Any) -> None:
        try:
            span.set_outputs(outputs)
        except Exception:
            self.logger.debug("mlflow span output error", exc_info=True)

    @staticmethod
    def _event_payload(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def _open_span(self, name: str, span_type: str) -> tuple[Any, Any] | None:
        mlflow = self._mlflow_module()
        if mlflow is None:
            return None
        try:
            context = mlflow.start_span(name=name, span_type=span_type)
            return context.__enter__(), context
        except Exception:
            self.logger.debug("mlflow span start error", exc_info=True)
            return None

    def _close_span(self, span: Any, context: Any) -> None:
        try:
            context.__exit__(None, None, None)
        except Exception:
            self.logger.debug("mlflow span close error", exc_info=True)

    def _set_status(self, span: Any, status: str) -> None:
        try:
            span.set_status(status)
        except Exception:
            self.logger.debug("mlflow span status error", exc_info=True)

    def _open_tool_span(self, name: str, parent_span: Any) -> Any | None:
        mlflow = self._mlflow_module()
        if mlflow is None or not hasattr(mlflow, "start_span_no_context"):
            return None
        try:
            return mlflow.start_span_no_context(name=name, span_type="TOOL", parent_span=parent_span)
        except Exception:
            self.logger.debug("mlflow tool span start error", exc_info=True)
            return None

    def _close_tool_span(self, span: Any, status: str) -> None:
        try:
            span.end(status=status)
        except Exception:
            self.logger.debug("mlflow tool span close error", exc_info=True)

    @contextmanager
    def trace_agent_run(self, *, operation: str, trace_id: str, inputs: Any = None) -> Iterator[None]:
        """Open an agent span, when MLflow tracing is enabled."""
        opened = self._open_span(f"agent.{operation}", "AGENT")
        if opened is None:
            yield
            return
        span, context = opened
        self._set_attributes(span, {"flow.name": self.flow_name, "operation": operation, "trace_id": trace_id})
        if inputs is not None:
            self._set_inputs(span, inputs)
        run = _MlflowRun(span=span)
        token = self._mlflow_run.set(run)
        outcome = "ok"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            for tool_span in run.tools.values():
                self._set_attributes(tool_span, {"outcome": "unfinished"})
                self._close_tool_span(tool_span, "ERROR")
            self._mlflow_run.reset(token)
            self._set_status(span, "OK" if outcome == "ok" else "ERROR")
            self._set_attributes(span, {"outcome": outcome})
            self._close_span(span, context)

    def record_agent_output(self, output: Any) -> None:
        """Record output on the active agent span."""
        run = self._mlflow_run.get()
        if run is not None:
            self._set_outputs(run.span, output)

    def _record_mlflow_event(self, event: PlannerEvent) -> None:
        run = self._mlflow_run.get()
        if run is None or event.event_type not in {"tool_call_start", "tool_call_result"}:
            return
        tool_call_id = event.extra.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            return
        if event.event_type == "tool_call_start":
            if tool_call_id in run.tools:
                return
            tool_name = event.extra.get("tool_name")
            span = self._open_tool_span(
                str(tool_name) if isinstance(tool_name, str) else "tool.call",
                run.span,
            )
            if span is None:
                return
            self._set_attributes(
                span,
                {
                    "tool.call_id": tool_call_id,
                    "tool.name": tool_name if isinstance(tool_name, str) else "unknown",
                    "trajectory.step": event.trajectory_step,
                    "action.sequence": event.extra.get("action_seq", 0),
                },
            )
            args = event.extra.get("args_json")
            if args is not None:
                self._set_inputs(span, self._event_payload(args))
            run.tools[tool_call_id] = span
            return

        span = run.tools.pop(tool_call_id, None)
        if span is None:
            return
        result = event.extra.get("result_json")
        if result is not None:
            self._set_outputs(span, self._event_payload(result))
        outcome = event.extra.get("outcome", "unknown")
        self._set_attributes(span, {"outcome": outcome})
        self._close_tool_span(span, "OK" if outcome == "success" else "ERROR")

    def publish_status(self, update: StatusUpdate) -> None:
        """Forward status updates to callback or logs."""
        if self._status_callback:
            self._status_callback(update)
        else:
            self.logger.info(
                "status_update",
                extra={
                    "flow": self.flow_name,
                    "status": update.status,
                    "message": update.message,
                    "timestamp": update.timestamp.isoformat(),
                },
            )


__all__ = ["AgentTelemetry", "StatusUpdate"]
