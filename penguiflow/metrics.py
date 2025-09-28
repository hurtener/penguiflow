"""Observability primitives for PenguiFlow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class FlowEvent:
    """Structured runtime event emitted around node execution."""

    event_type: str
    ts: float
    node_name: str | None
    node_id: str | None
    trace_id: str | None
    attempt: int
    latency_ms: float | None
    queue_depth_in: int
    queue_depth_out: int
    outgoing_edges: int
    queue_maxsize: int
    trace_pending: int | None
    trace_inflight: int
    trace_cancelled: bool
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @property
    def queue_depth(self) -> int:
        """Return the combined depth of incoming and outgoing queues."""

        return self.queue_depth_in + self.queue_depth_out

    def to_payload(self) -> dict[str, Any]:
        """Render a dictionary payload suitable for structured logging."""

        payload: dict[str, Any] = {
            "ts": self.ts,
            "event": self.event_type,
            "node_name": self.node_name,
            "node_id": self.node_id,
            "trace_id": self.trace_id,
            "latency_ms": self.latency_ms,
            "q_depth_in": self.queue_depth_in,
            "q_depth_out": self.queue_depth_out,
            "q_depth_total": self.queue_depth,
            "outgoing": self.outgoing_edges,
            "queue_maxsize": self.queue_maxsize,
            "attempt": self.attempt,
            "trace_inflight": self.trace_inflight,
            "trace_cancelled": self.trace_cancelled,
        }
        if self.trace_pending is not None:
            payload["trace_pending"] = self.trace_pending
        if self.extra:
            payload.update(self.extra)
        return payload

    def metric_samples(self) -> dict[str, float]:
        """Derive numeric metrics for integrations such as MLflow."""

        metrics: dict[str, float] = {
            "queue_depth_in": float(self.queue_depth_in),
            "queue_depth_out": float(self.queue_depth_out),
            "queue_depth_total": float(self.queue_depth),
            "attempt": float(self.attempt),
            "trace_inflight": float(self.trace_inflight),
            "trace_cancelled": 1.0 if self.trace_cancelled else 0.0,
        }
        if self.trace_pending is not None:
            metrics["trace_pending"] = float(self.trace_pending)
        if self.latency_ms is not None:
            metrics["latency_ms"] = self.latency_ms
        if (latency := self.extra.get("latency_ms")) is not None:
            # Allow extra payloads to inject a latency override for retries.
            try:
                metrics["latency_ms"] = float(latency)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                pass
        return metrics

    def tag_values(self) -> dict[str, str]:
        """Return string tags describing the event."""

        tags: dict[str, str] = {"event_type": self.event_type}
        if self.node_name is not None:
            tags["node_name"] = self.node_name
        if self.node_id is not None:
            tags["node_id"] = self.node_id
        if self.trace_id is not None:
            tags["trace_id"] = self.trace_id
        if self.extra:
            for key, value in self.extra.items():
                if isinstance(value, str | int | float | bool):
                    tags[key] = str(value)
        return tags


__all__ = ["FlowEvent"]
