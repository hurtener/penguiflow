"""Best-effort OpenTelemetry and MLflow evidence publishers.

Publishers never make an LCP decision and never raise into an agent workload.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any, Protocol, runtime_checkable

from .contracts.evidence import EvidenceEvent

logger = logging.getLogger("learning_control_plane.evidence")


@runtime_checkable
class EvidenceSink(Protocol):
    """Best-effort destination for redacted evidence records."""

    def emit(self, event: EvidenceEvent) -> bool:
        """Store or publish an event, returning whether the attempt succeeded."""
        ...


class CompositeEvidenceSink:
    """Fan out evidence without allowing one unavailable backend to block another."""

    def __init__(self, sinks: Sequence[EvidenceSink]) -> None:
        self._sinks = tuple(sinks)

    def emit(self, event: EvidenceEvent) -> bool:
        delivered = False
        for sink in self._sinks:
            try:
                delivered = sink.emit(event) or delivered
            except Exception:
                logger.warning("Evidence sink failed", exc_info=True)
        return delivered


class OpenTelemetryEvidenceSink:
    """Emit metadata-only LCP spans when OpenTelemetry is configured.

    OpenTelemetry is imported lazily. If it is absent or an exporter fails, the
    caller receives ``False`` and can continue its scheduled/offline work.
    """

    def __init__(self, *, tracer: Any | None = None, span_prefix: str = "lcp.evidence") -> None:
        self._tracer = tracer
        self._span_prefix = span_prefix
        self._unavailable = False

    def _resolve_tracer(self) -> Any | None:
        if self._unavailable:
            return None
        if self._tracer is not None:
            return self._tracer
        try:
            from opentelemetry import trace
        except ImportError:
            self._unavailable = True
            logger.info("OpenTelemetry evidence sink disabled: opentelemetry-api is not installed")
            return None
        self._tracer = trace.get_tracer("learning_control_plane")
        return self._tracer

    def emit(self, event: EvidenceEvent) -> bool:
        tracer = self._resolve_tracer()
        if tracer is None:
            return False
        try:
            with tracer.start_as_current_span(f"{self._span_prefix}.{event.event_type}") as span:
                for key, value in event.telemetry_attributes().items():
                    span.set_attribute(key, value)
            return True
        except Exception:
            logger.warning("OpenTelemetry evidence emission failed", exc_info=True)
            return False


class MlflowEvidenceSink:
    """Persist redacted evidence records as MLflow tags and JSON artifacts.

    MLflow remains an evidence store: this sink creates no candidate, gate, or
    promotion decision. It accepts an injected module to make integration tests and
    host-specific MLflow configuration straightforward.
    """

    def __init__(self, *, mlflow_module: Any | None = None, run_name_prefix: str = "lcp-evidence") -> None:
        self._mlflow = mlflow_module
        self._run_name_prefix = run_name_prefix
        self._unavailable = False

    def _module(self) -> Any | None:
        if self._unavailable:
            return None
        if self._mlflow is not None:
            return self._mlflow
        try:
            import mlflow
        except ImportError:
            self._unavailable = True
            logger.info("MLflow evidence sink disabled: mlflow is not installed")
            return None
        self._mlflow = mlflow
        return mlflow

    def emit(self, event: EvidenceEvent) -> bool:
        mlflow = self._module()
        if mlflow is None:
            return False
        try:
            active_run = getattr(mlflow, "active_run", lambda: None)()
            run_context = nullcontext(active_run)
            if active_run is None:
                run_context = mlflow.start_run(run_name=f"{self._run_name_prefix}-{event.event_type}")
            with run_context:
                mlflow.set_tags({"lcp.event_type": event.event_type, "lcp.event_id": event.event_id})
                for key, value in asdict(event.context).items():
                    if value is not None:
                        mlflow.set_tag(f"lcp.{key}", str(value))
                if hasattr(mlflow, "log_dict"):
                    mlflow.log_dict(event.record(), f"learning_control_plane/evidence/{event.event_id}.json")
            return True
        except Exception:
            logger.warning("MLflow evidence emission failed", exc_info=True)
            return False


__all__ = ["CompositeEvidenceSink", "EvidenceSink", "MlflowEvidenceSink", "OpenTelemetryEvidenceSink"]
