"""Redacted evidence records and best-effort OpenTelemetry/MLflow publishers.

Publishers never make an LCP decision and never raise into an agent workload.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

logger = logging.getLogger("learning_control_plane.evidence")

MLFLOW_LINEAGE_SCHEMA_VERSION = "v1"

_SENSITIVE_ATTRIBUTE_PARTS = frozenset(
    {
        "api_key",
        "authorization",
        "content",
        "cookie",
        "credential",
        "input",
        "message",
        "output",
        "password",
        "prompt",
        "secret",
        "token",
    }
)


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def redact_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Remove content and credential fields from evidence metadata."""

    redacted: dict[str, Any] = {}
    for raw_key, value in attributes.items():
        key = str(raw_key).strip()
        normalized_key = key.lower().replace("-", "_")

        if not key or any(part in normalized_key for part in _SENSITIVE_ATTRIBUTE_PARTS):
            continue

        try:
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            redacted[key] = str(value)
        else:
            redacted[key] = value
    return redacted


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Identify the exact agent, deployment, and evaluation evidence belongs to."""

    agent_id: str
    deployment_digest: str
    trace_id: str | None = None
    evaluation_id: str | None = None
    candidate_id: str | None = None
    dataset_version: str | None = None
    metric_version: str | None = None
    policy_version: str | None = None
    scope_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _require_non_empty(self.agent_id, "agent_id"))
        object.__setattr__(self, "deployment_digest", _require_non_empty(self.deployment_digest, "deployment_digest"))


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """Record one offline learning-plane event and its redacted metadata."""

    event_type: str
    context: EvidenceContext
    attributes: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, float] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"ev_{uuid4().hex}")
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _require_non_empty(self.event_type, "event_type"))
        object.__setattr__(self, "event_id", _require_non_empty(self.event_id, "event_id"))
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "attributes", redact_attributes(self.attributes))
        object.__setattr__(self, "metrics", self._validated_metrics())

    def _validated_metrics(self) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for raw_name, raw_value in self.metrics.items():
            name = _require_non_empty(str(raw_name), "metric name")
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"metric {name!r} must be finite")
            metrics[name] = value
        return metrics

    def record(self) -> dict[str, Any]:
        """Return the JSON-safe record stored by evidence systems."""

        record = asdict(self.context)
        record.update(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "occurred_at": self.occurred_at.isoformat(),
                "attributes": dict(self.attributes),
                "metrics": dict(self.metrics),
            }
        )
        return record

    def mlflow_tags(self) -> dict[str, str]:
        """Return the versioned MLflow tags that identify this evidence."""

        tags = {
            "lcp.lineage_schema": MLFLOW_LINEAGE_SCHEMA_VERSION,
            "lcp.event_id": self.event_id,
            "lcp.event_type": self.event_type,
            "lcp.occurred_at": self.occurred_at.isoformat(),
        }
        for key, value in asdict(self.context).items():
            if value is not None:
                tags[f"lcp.{key}"] = str(value)
        return tags

    def mlflow_metrics(self) -> dict[str, float]:
        """Return metric values under the reserved MLflow metric namespace."""

        return {f"lcp.metric.{name}": value for name, value in self.metrics.items()}

    def mlflow_artifact_path(self) -> str:
        """Return the fixed artifact path for this complete evidence receipt."""

        return f"learning_control_plane/evidence/{MLFLOW_LINEAGE_SCHEMA_VERSION}/{self.event_id}.json"

    def telemetry_attributes(self) -> dict[str, str | bool | float | int]:
        """Return flat scalar attributes accepted by OpenTelemetry."""

        attributes: dict[str, str | bool | float | int] = {
            "lcp.event_id": self.event_id,
            "lcp.event_type": self.event_type,
            "lcp.occurred_at": self.occurred_at.isoformat(),
        }
        for key, value in asdict(self.context).items():
            if value is not None:
                attributes[f"lcp.{key}"] = str(value)
        for key, value in self.attributes.items():
            if isinstance(value, (str, bool, float, int)):
                attributes[f"lcp.attr.{key}"] = value
        return attributes


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
                mlflow.set_tags(event.mlflow_tags())
                metrics = event.mlflow_metrics()
                if metrics and hasattr(mlflow, "log_metrics"):
                    mlflow.log_metrics(metrics)
                if hasattr(mlflow, "log_dict"):
                    mlflow.log_dict(event.record(), event.mlflow_artifact_path())
            return True
        except Exception:
            logger.warning("MLflow evidence emission failed", exc_info=True)
            return False


__all__ = [
    "CompositeEvidenceSink",
    "EvidenceContext",
    "EvidenceEvent",
    "EvidenceSink",
    "MLFLOW_LINEAGE_SCHEMA_VERSION",
    "MlflowEvidenceSink",
    "OpenTelemetryEvidenceSink",
    "redact_attributes",
]
