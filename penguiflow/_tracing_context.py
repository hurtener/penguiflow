"""Keep background work independent of a trace that may already have ended."""

from __future__ import annotations

import sys
from contextvars import Context, copy_context


def detached_trace_context() -> Context:
    """Copy application context without inheriting the active tracing span."""
    context = copy_context()
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import trace
    except ImportError:
        return context

    # Preserve request/tenant variables and OTel baggage while clearing the parent span.
    context.run(lambda: otel_context.attach(trace.set_span_in_context(trace.INVALID_SPAN)))
    # Newer MLflow versions keep a separate span context from global OTel.
    provider = sys.modules.get("mlflow.tracing.provider")
    runtime = getattr(provider, "mlflow_runtime_context", None)
    if runtime is not None:
        context.run(
            lambda: runtime.attach(trace.set_span_in_context(trace.INVALID_SPAN, context=runtime.get_current()))
        )
    return context
