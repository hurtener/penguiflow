"""Enterprise telemetry for comprehensive planner observability.

This module provides full visibility into:
- Planner lifecycle events (steps, LLM calls, pause/resume, finish)
- Tool errors surfaced through planner events
- Auto-sequencing decisions
- MLflow/observability backend integration
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from examples.planner_enterprise_agent_v2.config import AgentConfig
    from penguiflow.planner import PlannerEvent


class AgentTelemetry:
    """Comprehensive telemetry for enterprise agent deployments.

    Captures:
    1. Planner errors with their event payloads
    2. LLM call costs and latency
    3. Planning step metrics
    4. Auto-sequencing decisions
    5. Structured events for external systems

    Usage:
        telemetry = AgentTelemetry(config)
        planner = ReactPlanner(...,
        event_callback=telemetry.record_planner_event)
    """

    def __init__(self, config: AgentConfig) -> None:
        """Store ``config``."""
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
            "auto_seq_detected_unique": 0,
            "auto_seq_detected_ambiguous": 0,
            "auto_seq_detected_none": 0,
            "auto_seq_skipped": 0,
            "auto_seq_executed": 0,
        }

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
        self._log_planner_event(event, extra)

        # Collect for observability backend
        if self.config.enable_telemetry:
            self._events.append(
                {
                    "event": f"planner.{event_type}",
                    "payload": extra,
                }
            )

    def _log_planner_event(
        self, event: PlannerEvent, extra: dict[str, Any]
    ) -> None:
        """Log ``event`` at its level and update the planner metrics."""
        event_type = event.event_type
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
        elif event_type.startswith("auto_seq_"):
            if event_type in self._metrics:
                self._metrics[event_type] += 1
            self.planner_logger.info(event_type, extra=extra)
        elif event_type in ("pause", "resume", "finish"):
            self.planner_logger.info(event_type, extra=extra)
        elif event_type.endswith("_error") or "error" in event.extra:
            self.planner_logger.error(event_type, extra=extra)

    def emit_collected_events(self) -> None:
        """Emit batched events to observability backend.

        Call this after planner execution completes to send all collected
        telemetry to your monitoring system.
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
            self.logger.warning(
                "mlflow_tracking_uri not configured, skipping emission"
            )
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
        # Implementation would use datadog client Stub for example purposes
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
            "auto_seq_detected_unique": 0,
            "auto_seq_detected_ambiguous": 0,
            "auto_seq_detected_none": 0,
            "auto_seq_skipped": 0,
            "auto_seq_executed": 0,
        }
