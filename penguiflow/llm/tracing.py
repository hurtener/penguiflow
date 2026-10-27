"""Pluggable tracing for LLM completion calls.

This module provides a transport-agnostic tracing seam for the native LLM
layer: every ``NativeLLMAdapter.complete()`` call is wrapped in a span that is
emitted to a configurable :class:`LLMTraceSink`. Because the seam sits above
the ``Provider`` abstraction it covers every transport uniformly — the
built-in native providers today and additional transports in the future — and
every adapter built by ``FallbackLLMClient``, so rate-limit failover shows up
as spans attributed to each model actually called.

Enablement is opt-in:

- Explicitly::

      create_native_adapter(model, trace_sink=MlflowLLMTraceSink())

- Transparently via environment (no code changes in the agent)::

      export PENGUIFLOW_LLM_TRACING=mlflow   # or "log"

Spans carry metadata only (provider, model, token counts, cost, latency,
errors). Message contents are never captured — they routinely contain
sensitive data.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("penguiflow.llm.tracing")

TRACING_ENV_VAR = "PENGUIFLOW_LLM_TRACING"


@dataclass(slots=True)
class LLMCallSpan:
    """Mutable record describing one LLM completion call.

    Request metadata is set when the span opens; outcome fields are filled in
    before it closes. ``error_*`` fields are populated when the call raises.
    """

    provider: str
    model: str
    stream: bool = False
    response_format_kind: str | None = None
    attempts: int | None = None
    content_chars: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    # The provider's stop reason. "length" on a final answer is a truncation, which was otherwise
    # invisible in a recorded run.
    finish_reason: str | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    error_type: str | None = None
    error_message: str | None = None

    def outcome_attributes(self) -> dict[str, Any]:
        """Outcome fields that have a value, as a flat attribute dict."""
        pairs = {
            "attempts": self.attempts,
            "content_chars": self.content_chars,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }
        return {key: value for key, value in pairs.items() if value is not None}


@runtime_checkable
class LLMTraceSink(Protocol):
    """Receives one span per LLM completion call."""

    def span(self, call: LLMCallSpan) -> AbstractContextManager[Any]:
        """Open a span for ``call``. Outcome fields are set before exit."""
        ...


@contextmanager
def _measured(call: LLMCallSpan) -> Iterator[None]:
    """Record latency and error metadata on ``call`` around the wrapped block."""
    started = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        call.error_type = type(exc).__name__
        call.error_message = str(exc)[:500]
        raise
    finally:
        call.latency_ms = round((time.perf_counter() - started) * 1000, 3)


class LoggingLLMTraceSink:
    """Zero-dependency sink emitting one structured log line per LLM call."""

    def __init__(
        self,
        *,
        level: int = logging.INFO,
        logger_name: str = "penguiflow.llm.trace",
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level

    @contextmanager
    def span(self, call: LLMCallSpan) -> Iterator[None]:
        try:
            with _measured(call):
                yield
        finally:
            self._logger.log(
                self._level,
                "llm_call",
                extra={
                    "llm_provider": call.provider,
                    "llm_model": call.model,
                    "llm_stream": call.stream,
                    **{f"llm_{key}": value for key, value in call.outcome_attributes().items()},
                },
            )


class MlflowLLMTraceSink:
    """Sink emitting MLflow Tracing spans (``span_type="LLM"``).

    Requires ``mlflow>=2.14`` (the tracing fluent API). mlflow is imported
    lazily on first use; if it is missing or too old the sink degrades to a
    no-op with a single warning — it never fails the LLM call. Spans nest
    under any active MLflow trace (e.g. an autologged agent run) or start a
    new trace when none is active.
    """

    def __init__(self, *, span_name: str = "llm.complete") -> None:
        self._span_name = span_name
        self._mlflow: Any | None = None
        self._unavailable = False

    def _module(self) -> Any | None:
        if self._unavailable:
            return None
        if self._mlflow is None:
            try:
                import mlflow
            except ImportError as exc:
                self._unavailable = True
                logger.warning(
                    "MlflowLLMTraceSink disabled: mlflow is not installed (%s)",
                    exc,
                )
                return None
            if not hasattr(mlflow, "start_span"):
                self._unavailable = True
                logger.warning(
                    "MlflowLLMTraceSink disabled: mlflow %s lacks the tracing API (need >= 2.14)",
                    getattr(mlflow, "__version__", "unknown"),
                )
                return None
            self._mlflow = mlflow
        return self._mlflow

    @contextmanager
    def span(self, call: LLMCallSpan) -> Iterator[Any]:
        mlflow = self._module()
        if mlflow is None:
            with _measured(call):
                yield None
            return

        with mlflow.start_span(name=self._span_name, span_type="LLM") as span:
            try:
                attributes: dict[str, Any] = {
                    "provider": call.provider,
                    "model": call.model,
                    "stream": call.stream,
                }
                if call.response_format_kind:
                    attributes["response_format"] = call.response_format_kind
                span.set_attributes(attributes)
            except Exception:  # pragma: no cover - defensive
                logger.debug("mlflow span attribute error", exc_info=True)
            try:
                with _measured(call):
                    yield span
            finally:
                try:
                    span.set_attributes(call.outcome_attributes())
                except Exception:  # pragma: no cover - defensive
                    logger.debug("mlflow span attribute error", exc_info=True)


def resolve_trace_sink_from_env() -> LLMTraceSink | None:
    """Build a trace sink from ``PENGUIFLOW_LLM_TRACING`` (transparent opt-in).

    Supported values: ``mlflow`` (MLflow Tracing spans) and ``log`` /
    ``logging`` (structured log lines). Unset or falsy values disable
    tracing. Unknown values are ignored with a warning so a typo can never
    break a production run.
    """
    raw = os.environ.get(TRACING_ENV_VAR, "").strip().lower()
    if not raw or raw in ("0", "false", "off", "none", "disabled"):
        return None
    if raw == "mlflow":
        return MlflowLLMTraceSink()
    if raw in ("log", "logging"):
        return LoggingLLMTraceSink()
    logger.warning(
        "Ignoring unknown %s value %r (expected 'mlflow' or 'log')",
        TRACING_ENV_VAR,
        raw,
    )
    return None


__all__ = [
    "TRACING_ENV_VAR",
    "LLMCallSpan",
    "LLMTraceSink",
    "LoggingLLMTraceSink",
    "MlflowLLMTraceSink",
    "resolve_trace_sink_from_env",
]
