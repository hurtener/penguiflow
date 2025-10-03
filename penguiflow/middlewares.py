"""Middleware hooks for PenguiFlow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from .metrics import FlowEvent

LatencyCallback = Callable[[str, float, FlowEvent], None]


class Middleware(Protocol):
    """Base middleware signature receiving :class:`FlowEvent` objects."""

    async def __call__(self, event: FlowEvent) -> None: ...


def log_flow_events(
    logger: logging.Logger | None = None,
    *,
    start_level: int = logging.INFO,
    success_level: int = logging.INFO,
    error_level: int = logging.ERROR,
    latency_callback: LatencyCallback | None = None,
) -> Middleware:
    """Return middleware that emits structured node lifecycle logs.

    Parameters
    ----------
    logger:
        Optional :class:`logging.Logger` instance. When omitted a logger named
        ``"penguiflow.flow"`` is used.
    start_level, success_level, error_level:
        Logging levels for ``node_start``, ``node_success``, and
        ``node_error`` events respectively.
    latency_callback:
        Optional callable invoked with ``(event_type, latency_ms, event)`` for
        ``node_success`` and ``node_error`` events. Use this hook to connect the
        middleware to histogram-based metrics backends without
        re-implementing timing logic.
    """

    log = logger or logging.getLogger("penguiflow.flow")

    async def _middleware(event: FlowEvent) -> None:
        if event.event_type not in {"node_start", "node_success", "node_error"}:
            return

        payload = event.to_payload()
        log_level = start_level

        if event.event_type == "node_start":
            log_level = start_level
        elif event.event_type == "node_success":
            log_level = success_level
        else:
            log_level = error_level
            if event.error_payload is not None:
                payload = dict(payload)
                payload["error_payload"] = dict(event.error_payload)

        log.log(log_level, event.event_type, extra=payload)

        if (
            latency_callback is not None
            and event.event_type in {"node_success", "node_error"}
            and event.latency_ms is not None
        ):
            try:
                latency_callback(event.event_type, float(event.latency_ms), event)
            except Exception:
                log.exception(
                    "log_flow_events_latency_callback_error",
                    extra={
                        "event": "log_flow_events_latency_callback_error",
                        "node_name": event.node_name,
                        "node_id": event.node_id,
                        "trace_id": event.trace_id,
                    },
                )

    return _middleware


__all__ = ["Middleware", "FlowEvent", "log_flow_events", "LatencyCallback"]
