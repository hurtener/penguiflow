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

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from queue import SimpleQueue
from threading import BoundedSemaphore, Lock, Thread
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

logger = logging.getLogger("penguiflow.llm.tracing")

TRACING_ENV_VAR = "PENGUIFLOW_LLM_TRACING"


@dataclass
class _MlflowResolution:
    future: Future[Any]
    wait_timed_out: bool = False
    remote: bool = False
    slots: BoundedSemaphore = field(default_factory=lambda: BoundedSemaphore(32))


@dataclass
class _RemoteTraceCloser:
    trace_id: str
    resolution: _MlflowResolution
    pending: SimpleQueue[tuple[Any, bool, int]] = field(default_factory=SimpleQueue)
    finished: Future[None] = field(default_factory=Future)

    def run(self) -> None:
        while True:
            span, last, end_time_ns = self.pending.get()
            try:
                span.end(end_time_ns=end_time_ns)
            except Exception:
                logger.debug("MLflow span close failed", exc_info=True)
            finally:
                if last:
                    self.resolution.slots.release()
                    self.finished.set_result(None)
            if last:
                return


_remote_closer: ContextVar[_RemoteTraceCloser | None] = ContextVar("penguiflow_mlflow_closer", default=None)
_last_remote_closer: ContextVar[_RemoteTraceCloser | None] = ContextVar("penguiflow_mlflow_last_closer", default=None)
_mlflow_resolutions: dict[tuple[Any, ...], _MlflowResolution] = {}
_mlflow_resolution_lock = Lock()


def _mlflow_resolution(mlflow: Any) -> _MlflowResolution:
    fluent = sys.modules.get("mlflow.tracking.fluent")
    provider = sys.modules.get("mlflow.tracing.provider")
    destinations = getattr(provider, "_MLFLOW_TRACE_USER_DESTINATION", None)
    destination = destinations.get() if destinations is not None else None
    run = getattr(fluent, "_get_latest_active_run", lambda: None)()
    key = (
        id(mlflow),
        mlflow.get_tracking_uri(),
        os.environ.get("MLFLOW_EXPERIMENT_NAME"),
        os.environ.get("MLFLOW_EXPERIMENT_ID"),
        getattr(fluent, "_active_experiment_id", None),
        repr(destination),
        run.info.experiment_id if run is not None else None,
    )
    with _mlflow_resolution_lock:
        resolution = _mlflow_resolutions.get(key)
        if resolution is not None:
            return resolution
        resolution = _MlflowResolution(Future(), remote=urlparse(key[1]).scheme not in {"", "file", "sqlite"})
        _mlflow_resolutions[key] = resolution

    def resolve() -> None:
        try:
            from mlflow.entities import MlflowExperimentLocation
            from mlflow.tracing.provider import _get_tracer
            from mlflow.tracking.fluent import _get_experiment_id

            experiment_id = getattr(destination, "experiment_id", None)
            if experiment_id is None:
                experiment_id = run.info.experiment_id if run is not None else _get_experiment_id()
            if experiment_id is None:
                raise ValueError("MLflow experiment is not configured")
            resolved_destination = MlflowExperimentLocation(experiment_id=experiment_id)
            _get_tracer(__name__)
        except Exception:
            logger.warning("MLflow tracing disabled: experiment initialization failed", exc_info=True)
            resolution.future.set_result(None)
        else:
            resolution.future.set_result(resolved_destination)

    # SDK retries can last minutes; a stuck lookup must not hold up process shutdown.
    Thread(target=copy_context().run, args=(resolve,), name="penguiflow-mlflow-init", daemon=True).start()
    return resolution


async def prepare_mlflow(mlflow: Any) -> None:
    """Resolve the configured experiment once, off the event loop, with a bounded wait."""
    try:
        if mlflow.get_current_active_span() is not None:
            return
        resolution = _mlflow_resolution(mlflow)
        if resolution.wait_timed_out or resolution.future.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(resolution.future)),
                timeout=0.1 if resolution.remote else None,
            )
        except TimeoutError:
            resolution.wait_timed_out = True
            logger.warning("MLflow experiment initialization is still pending; continuing without tracing")
    except Exception:
        logger.warning("MLflow tracing initialization failed", exc_info=True)


@contextmanager
def safe_mlflow_span(mlflow: Any, *, name: str, span_type: str) -> Iterator[Any | None]:
    """Start only prepared roots; tracing failures never replace application results."""
    span = None
    resolution = None
    reserved = False
    closer = None
    closer_token = None
    try:
        from mlflow.tracing.provider import safe_set_span_in_context

        parent = mlflow.get_current_active_span()
        kwargs: dict[str, Any] = {"name": name, "span_type": span_type, "parent_span": parent}
        if parent is not None:
            closer = _remote_closer.get()
            if closer is not None and closer.trace_id != parent.trace_id:
                closer = None
        if parent is None:
            resolution = _mlflow_resolution(mlflow)
            if resolution.future.done() and resolution.future.result() is not None:
                # Bound pending remote exports when the SDK's retry loop stalls.
                reserved = resolution.remote and resolution.slots.acquire(blocking=False)
                if not resolution.remote or reserved:
                    kwargs["experiment_id"] = resolution.future.result().experiment_id
                    span = mlflow.start_span_no_context(**kwargs)
        elif closer is None and urlparse(mlflow.get_tracking_uri()).scheme not in {"", "file", "sqlite"}:
            resolution = _mlflow_resolution(mlflow)
            reserved = resolution.slots.acquire(blocking=False)
            if reserved:
                span = mlflow.start_span_no_context(**kwargs)
        else:
            span = mlflow.start_span_no_context(**kwargs)
        if span is not None and reserved and resolution is not None:
            closer = _RemoteTraceCloser(span.trace_id, resolution)
            Thread(target=closer.run, name="penguiflow-mlflow-export", daemon=True).start()
            closer_token = _remote_closer.set(closer)
    except Exception:
        logger.debug("MLflow span start failed", exc_info=True)

    if span is None:
        if reserved and resolution is not None:
            resolution.slots.release()
        yield None
        return

    try:
        with safe_set_span_in_context(span):
            try:
                yield span
            except BaseException:
                try:
                    span.set_status("ERROR")
                except Exception:
                    logger.debug("MLflow span status failed", exc_info=True)
                raise
    finally:
        if closer_token is not None:
            _remote_closer.reset(closer_token)
        if closer is not None:
            # Child spans must finish before the root computes its final token totals.
            closer.pending.put((span, reserved, time.time_ns()))
            if reserved:
                _last_remote_closer.set(closer)
        else:
            try:
                span.end()
            except Exception:
                logger.debug("MLflow span close failed", exc_info=True)


async def finish_mlflow_spans(mlflow: Any | None) -> None:
    """Complete children before returning control to an application-owned root."""
    closer = _last_remote_closer.get()
    if closer is None or mlflow is None:
        return
    parent = mlflow.get_current_active_span()
    if parent is not None and parent.trace_id == closer.trace_id:
        _last_remote_closer.set(None)
        await asyncio.shield(asyncio.wrap_future(closer.finished))


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

    Requires ``mlflow>=3.18.0`` (install ``penguiflow[mlflow]``). mlflow is imported
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
            if not hasattr(mlflow, "start_span_no_context"):
                self._unavailable = True
                logger.warning(
                    "MlflowLLMTraceSink disabled: mlflow %s lacks the tracing API (need >= 3.18.0)",
                    getattr(mlflow, "__version__", "unknown"),
                )
                return None
            self._mlflow = mlflow
        return self._mlflow

    async def prepare(self) -> None:
        mlflow = self._mlflow
        if mlflow is None and not self._unavailable:
            mlflow = await asyncio.to_thread(self._module)
        if mlflow is not None:
            await prepare_mlflow(mlflow)

    @contextmanager
    def span(self, call: LLMCallSpan) -> Iterator[Any]:
        mlflow = self._module()
        if mlflow is None:
            with _measured(call):
                yield None
            return

        with safe_mlflow_span(mlflow, name=self._span_name, span_type="LLM") as span:
            if span is None:
                with _measured(call):
                    yield None
                return
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
                    if call.error_type is not None:
                        span.set_status("ERROR")
                    if call.input_tokens is not None and call.output_tokens is not None:
                        token_usage = {
                            "input_tokens": call.input_tokens,
                            "output_tokens": call.output_tokens,
                            "total_tokens": call.input_tokens + call.output_tokens,
                        }
                        span.set_attribute("mlflow.chat.tokenUsage", token_usage)
                except Exception:  # pragma: no cover - defensive
                    logger.debug("mlflow span attribute error", exc_info=True)


@asynccontextmanager
async def trace_llm_call(sink: LLMTraceSink, call: LLMCallSpan) -> AsyncIterator[Any]:
    try:
        with sink.span(call) as span:
            yield span
    finally:
        if isinstance(sink, MlflowLLMTraceSink):
            await finish_mlflow_spans(sink._mlflow)


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
