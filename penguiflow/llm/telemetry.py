"""Telemetry hooks for the LLM layer.

Provides pluggable, low-overhead events for logs/MLflow/Prometheus integration.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("penguiflow.llm.telemetry")


@dataclass
class LLMEvent:
    """Event emitted during LLM operations for observability."""

    event_type: str  # "request_start", "request_end", "stream_chunk", "retry", "error"
    timestamp: datetime
    provider: str
    model: str
    trace_id: str | None = None
    extra: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        event_type: str,
        provider: str,
        model: str,
        *,
        trace_id: str | None = None,
        **extra: Any,
    ) -> LLMEvent:
        """Create an event with current timestamp."""
        return cls(
            event_type=event_type,
            timestamp=datetime.now(),
            provider=provider,
            model=model,
            trace_id=trace_id,
            extra=extra if extra else None,
        )


# Type alias for event callbacks
TelemetryCallback = Callable[[LLMEvent], None]


class TelemetryHooks:
    """Pluggable telemetry for LLM operations.

    Integrates with PenguiFlow's metrics system and supports external
    observability platforms (MLflow, Prometheus, OpenTelemetry).

    Usage:
        hooks = get_telemetry_hooks()
        hooks.register(my_callback)

        # In your code
        hooks.emit(LLMEvent.create("request_start", "openai", "gpt-4o"))
    """

    def __init__(self) -> None:
        self._callbacks: list[TelemetryCallback] = []
        self._enabled = True

    def register(self, callback: TelemetryCallback) -> None:
        """Register a telemetry callback.

        Args:
            callback: Function to call with each event.
        """
        self._callbacks.append(callback)

    def unregister(self, callback: TelemetryCallback) -> bool:
        """Unregister a telemetry callback.

        Args:
            callback: The callback to remove.

        Returns:
            True if callback was found and removed.
        """
        try:
            self._callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def emit(self, event: LLMEvent) -> None:
        """Emit event to all registered callbacks (non-blocking).

        Args:
            event: The event to emit.
        """
        if not self._enabled:
            return

        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                # Telemetry should never break the main flow
                logger.debug(f"Telemetry callback error: {e}")

    def enable(self) -> None:
        """Enable telemetry emission."""
        self._enabled = True

    def disable(self) -> None:
        """Disable telemetry emission."""
        self._enabled = False

    def clear(self) -> None:
        """Remove all registered callbacks."""
        self._callbacks.clear()


# Global hooks instance
_hooks: TelemetryHooks | None = None


def get_telemetry_hooks() -> TelemetryHooks:
    """Get the global telemetry hooks instance."""
    global _hooks
    if _hooks is None:
        _hooks = TelemetryHooks()
        # Register default logger callback
        _hooks.register(default_logger_callback)
    return _hooks


def set_telemetry_hooks(hooks: TelemetryHooks) -> None:
    """Replace the global telemetry hooks instance (for testing)."""
    global _hooks
    _hooks = hooks


# ---------------------------------------------------------------------------
# Default Callbacks
# ---------------------------------------------------------------------------


def default_logger_callback(event: LLMEvent) -> None:
    """Default callback that logs events at DEBUG level."""
    if event.event_type == "request_end":
        extra = event.extra or {}
        latency = extra.get("latency_ms", 0)
        input_tokens = extra.get("input_tokens", 0)
        output_tokens = extra.get("output_tokens", 0)
        logger.debug(
            f"LLM {event.provider}/{event.model}: "
            f"latency={latency}ms, tokens={input_tokens}+{output_tokens}"
        )
    elif event.event_type == "error":
        extra = event.extra or {}
        error = extra.get("error", "unknown")
        logger.warning(f"LLM {event.provider}/{event.model} error: {error}")
    elif event.event_type == "retry":
        extra = event.extra or {}
        attempt = extra.get("attempt", 0)
        reason = extra.get("reason", "unknown")
        logger.info(f"LLM {event.provider}/{event.model} retry {attempt}: {reason}")


# ---------------------------------------------------------------------------
# Example Integrations (not imported by default)
# ---------------------------------------------------------------------------


def create_mlflow_callback() -> TelemetryCallback:
    """Create a callback that logs to MLflow.

    Returns:
        Callback function for MLflow integration.

    Example:
        from penguiflow.llm.telemetry import get_telemetry_hooks, create_mlflow_callback
        hooks = get_telemetry_hooks()
        hooks.register(create_mlflow_callback())
    """
    def mlflow_callback(event: LLMEvent) -> None:
        try:
            import mlflow
        except ImportError:
            return

        if event.event_type == "request_end" and event.extra:
            mlflow.log_metrics({
                f"llm.{event.provider}.latency_ms": event.extra.get("latency_ms", 0),
                f"llm.{event.provider}.input_tokens": event.extra.get("input_tokens", 0),
                f"llm.{event.provider}.output_tokens": event.extra.get("output_tokens", 0),
                f"llm.{event.provider}.cost_usd": event.extra.get("cost", 0),
            })

    return mlflow_callback


def create_prometheus_callback() -> TelemetryCallback:
    """Create a callback that updates Prometheus metrics.

    Returns:
        Callback function for Prometheus integration.

    Example:
        from penguiflow.llm.telemetry import get_telemetry_hooks, create_prometheus_callback
        hooks = get_telemetry_hooks()
        hooks.register(create_prometheus_callback())
    """
    try:
        from prometheus_client import Counter, Histogram

        REQUEST_COUNT = Counter(
            "llm_requests_total",
            "Total LLM requests",
            ["provider", "model", "status"]
        )
        REQUEST_LATENCY = Histogram(
            "llm_request_latency_seconds",
            "LLM request latency",
            ["provider"]
        )
        TOKEN_COUNTER = Counter(
            "llm_tokens_total",
            "Total tokens used",
            ["provider", "direction"]
        )
    except ImportError:
        REQUEST_COUNT = None
        REQUEST_LATENCY = None
        TOKEN_COUNTER = None

    def prometheus_callback(event: LLMEvent) -> None:
        if REQUEST_COUNT is None:
            return

        if event.event_type == "request_end" and event.extra:
            REQUEST_COUNT.labels(
                provider=event.provider,
                model=event.model,
                status="success"
            ).inc()

            REQUEST_LATENCY.labels(
                provider=event.provider
            ).observe(event.extra.get("latency_ms", 0) / 1000)

            TOKEN_COUNTER.labels(
                provider=event.provider,
                direction="input"
            ).inc(event.extra.get("input_tokens", 0))

            TOKEN_COUNTER.labels(
                provider=event.provider,
                direction="output"
            ).inc(event.extra.get("output_tokens", 0))

        elif event.event_type == "error":
            REQUEST_COUNT.labels(
                provider=event.provider,
                model=event.model,
                status="error"
            ).inc()

    return prometheus_callback


# ---------------------------------------------------------------------------
# Context Manager for Timing
# ---------------------------------------------------------------------------


class TimingContext:
    """Context manager for timing LLM operations."""

    def __init__(self, provider: str, model: str, trace_id: str | None = None):
        self.provider = provider
        self.model = model
        self.trace_id = trace_id
        self.start_time: float = 0
        self.end_time: float = 0

    def __enter__(self) -> TimingContext:
        self.start_time = time.perf_counter()
        get_telemetry_hooks().emit(
            LLMEvent.create(
                "request_start",
                self.provider,
                self.model,
                trace_id=self.trace_id,
            )
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.end_time = time.perf_counter()
        latency_ms = (self.end_time - self.start_time) * 1000

        if exc_val is not None:
            get_telemetry_hooks().emit(
                LLMEvent.create(
                    "error",
                    self.provider,
                    self.model,
                    trace_id=self.trace_id,
                    latency_ms=latency_ms,
                    error=str(exc_val),
                )
            )
        # Success event is emitted by the caller with full details

    @property
    def latency_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.perf_counter() - self.start_time) * 1000
