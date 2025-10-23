"""Enterprise telemetry middleware for comprehensive observability.

This module implements the telemetry patterns from the successful PenguiFlow
implementation case study, providing full visibility into:
- Planner lifecycle events
- Node execution with detailed error payloads
- Flow events with structured logging
- MLflow/observability backend integration
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from examples.planner_enterprise_agent.config import AgentConfig
from penguiflow.metrics import FlowEvent
from penguiflow.planner import PlannerEvent


class AgentTelemetry:
    """Comprehensive telemetry for enterprise agent deployments.

    Implements the telemetry middleware pattern that captures:
    1. Full exception tracebacks from FlowEvents
    2. Detailed error payloads with context
    3. LLM call costs and latency
    4. Planning step metrics
    5. Structured events for external systems

    Usage:
        telemetry = AgentTelemetry(config)

        # Add to PenguiFlow
        flow.add_middleware(log_flow_events(telemetry.logger))
        flow.add_middleware(telemetry.record_flow_event)

        # Add to ReactPlanner
        planner = ReactPlanner(..., event_callback=telemetry.record_planner_event)
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(f"penguiflow.{config.agent_name}")
        self.planner_logger = logging.getLogger(
            f"penguiflow.{config.agent_name}.planner"
        )

        # Event collection for batch emission
        self._events: list[dict[str, Any]] = []

        # Metrics tracking
        self._metrics: dict[str, Any] = {
            "planner_steps": 0,
            "planner_llm_calls": 0,
            "planner_cost_usd": 0.0,
            "flow_node_errors": 0,
            "flow_node_successes": 0,
        }

    async def record_flow_event(self, event: FlowEvent) -> FlowEvent:
        """Middleware function that intercepts all PenguiFlow events.

        This is the CRITICAL pattern from the case study - it extracts
        detailed error payloads that would otherwise be trapped in flow state.
        """
        event_type = event.event_type

        if event_type == "node_start":
            self.logger.debug(
                "node_start",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "node_id": event.node_id,
                },
            )

        elif event_type == "node_success":
            self._metrics["flow_node_successes"] += 1
            self.logger.info(
                "node_success",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "latency_ms": event.latency_ms,
                },
            )

        elif event_type == "node_error":
            # THIS IS THE CRITICAL PART - Extract error details!
            # Without this, you only see "node_error" with no context
            error_payload = event.error_payload or {}

            self._metrics["flow_node_errors"] += 1

            # Log everything for debugging (the breakthrough from the case study)
            self.logger.error(
                "node_error",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "node_id": event.node_id,
                    "error_class": error_payload.get("error_class"),
                    "error_message": error_payload.get("error_message"),
                    "error_traceback": error_payload.get("error_traceback"),
                    "flow_error_code": error_payload.get("code"),
                    "flow_error_message": error_payload.get("message"),
                    # Include full payload for complete visibility
                    **error_payload,
                },
            )

            # Collect for batch emission to observability backend
            if self.config.enable_telemetry:
                self._events.append(
                    {
                        "event": "flow.node_error",
                        "payload": {
                            "node": event.node_name,
                            "trace_id": event.trace_id,
                            **error_payload,
                        },
                    }
                )

        # Always return event unmodified - middleware is read-only
        return event

    def record_planner_event(self, event: PlannerEvent) -> None:
        """Callback for ReactPlanner events.

        Captures planner-specific telemetry:
        - Step start/complete with latency
        - LLM calls with cost tracking
        - Pause/resume operations
        - Constraint violations
        """
        event_type = event.event_type

        # Extract all event data
        extra = event.to_payload()

        if event_type == "step_start":
            self.planner_logger.debug("step_start", extra=extra)

        elif event_type == "step_complete":
            self._metrics["planner_steps"] += 1
            self.planner_logger.info("step_complete", extra=extra)

            # Track cost if available
            cost = extra.get("cost_usd", 0)
            if cost > 0:
                self._metrics["planner_cost_usd"] += cost

        elif event_type == "llm_call":
            self._metrics["planner_llm_calls"] += 1
            self.planner_logger.debug("llm_call", extra=extra)

        elif event_type == "pause":
            self.planner_logger.info("pause", extra=extra)

        elif event_type == "resume":
            self.planner_logger.info("resume", extra=extra)

        elif event_type == "finish":
            self.planner_logger.info("finish", extra=extra)

        elif event_type.endswith("_error") or "error" in event.extra:
            self.planner_logger.error(event_type, extra=extra)

        # Collect for observability backend
        if self.config.enable_telemetry:
            self._events.append(
                {
                    "event": f"planner.{event_type}",
                    "payload": extra,
                }
            )

    def emit_collected_events(self) -> None:
        """Emit batched events to observability backend.

        Call this after planner execution completes to send all
        collected telemetry to your monitoring system.
        """
        if not self._events:
            return

        if self.config.telemetry_backend == "mlflow":
            self._emit_to_mlflow()
        elif self.config.telemetry_backend == "datadog":
            self._emit_to_datadog()
        else:
            # Default: log as JSON for structured log aggregation
            self.logger.info(
                "telemetry_batch",
                extra={
                    "events": self._events,
                    "metrics": self._metrics,
                },
            )

        # Clear for next execution
        self._events.clear()

    def _emit_to_mlflow(self) -> None:
        """Emit events to MLflow tracking server."""
        if not self.config.mlflow_tracking_uri:
            self.logger.warning("mlflow_tracking_uri not configured, skipping emission")
            return

        # Implementation would use mlflow.log_metrics, mlflow.log_params, etc.
        # Stub for example purposes
        self.logger.info(
            "mlflow_emit",
            extra={
                "tracking_uri": self.config.mlflow_tracking_uri,
                "event_count": len(self._events),
            },
        )

    def _emit_to_datadog(self) -> None:
        """Emit events to DataDog APM."""
        # Implementation would use datadog client
        # Stub for example purposes
        self.logger.info(
            "datadog_emit",
            extra={"event_count": len(self._events)},
        )

    def get_metrics(self) -> Mapping[str, Any]:
        """Return current metrics snapshot."""
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        """Reset metrics counters (useful for testing)."""
        self._metrics = {
            "planner_steps": 0,
            "planner_llm_calls": 0,
            "planner_cost_usd": 0.0,
            "flow_node_errors": 0,
            "flow_node_successes": 0,
        }
