"""Observability primitives for PenguiFlow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class FlowEvent:
    """Structured runtime event emitted around node execution.

    Attributes:
        event_type: Kind of event (e.g. ``node_start``, ``node_end``, ``node_error``,
            ``retry``, ``emit``, ``fetch``, ``stream_chunk``, ``cancel_begin``,
            ``cancel_end``).
        ts: Unix timestamp (seconds) at which the event was recorded.
        node_name: Name of the node associated with the event, if any.
        node_id: Identifier of the node instance associated with the event, if any.
        trace_id: Identifier of the trace associated with the event, if any.
        attempt: Attempt number for the node execution (1-indexed).
        latency_ms: Duration of the associated operation in milliseconds, if available.
        queue_depth_in: Depth of the node's inbound queue at event time.
        queue_depth_out: Depth of the node's outbound queue at event time.
        outgoing_edges: Number of outgoing edges from the node.
        queue_maxsize: Configured maximum size of the node's queue.
        trace_pending: Number of pending messages for the trace, if tracked.
        trace_inflight: Number of in-flight messages for the trace.
        trace_cancelled: Whether the trace has been cancelled.
        extra: Additional event-specific data as an immutable mapping.
    """

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
    def error_payload(self) -> Mapping[str, Any] | None:
        """Return the structured ``FlowError`` payload if present.

        Returns:
            An immutable mapping built from the ``flow_error`` entry in ``extra``, or
            ``None`` if no such entry is present.
        """

        raw_payload = self.extra.get("flow_error")
        if isinstance(raw_payload, Mapping):
            return MappingProxyType(dict(raw_payload))
        return None

    @property
    def queue_depth(self) -> int:
        """Return the combined depth of incoming and outgoing queues.

        Returns:
            The sum of ``queue_depth_in`` and ``queue_depth_out``.
        """

        return self.queue_depth_in + self.queue_depth_out

    def to_payload(self) -> dict[str, Any]:
        """Render a dictionary payload suitable for structured logging.

        Returns:
            A mutable dictionary with core event fields plus any entries from ``extra``
            (and ``trace_pending`` when set).
        """

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
        """Derive numeric metrics for integrations such as MLflow.

        Returns:
            A dictionary of numeric samples (queue depths, attempt, trace inflight,
            trace cancelled as 0.0/1.0, and latency when available).
        """

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
        """Return string tags describing the event.

        Returns:
            A dictionary of string tags including ``event_type`` plus ``node_name``,
            ``node_id``, and ``trace_id`` when set, and scalar entries from ``extra``.
        """

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
