"""Tests for penguiflow/middlewares.py edge cases."""

import logging

import pytest

from penguiflow.metrics import FlowEvent
from penguiflow.middlewares import log_flow_events


def make_event(event_type: str, **kwargs):
    """Helper to create FlowEvent with defaults."""
    defaults = {
        "event_type": event_type,
        "ts": 1234567890.0,
        "node_name": "test_node",
        "node_id": "node_123",
        "trace_id": "trace_abc",
        "attempt": 0,
        "latency_ms": 100.0,
        "queue_depth_in": 1,
        "queue_depth_out": 1,
        "outgoing_edges": 1,
        "queue_maxsize": 100,
        "trace_pending": None,
        "trace_inflight": 1,
        "trace_cancelled": False,
        "extra": {},
    }
    defaults.update(kwargs)
    return FlowEvent(**defaults)


@pytest.mark.asyncio
async def test_log_flow_events_ignores_other_events():
    """log_flow_events should ignore non-lifecycle events."""
    middleware = log_flow_events()
    event = make_event("other_event")

    # Should not raise, just return
    await middleware(event)


@pytest.mark.asyncio
async def test_log_flow_events_start_event(caplog):
    """log_flow_events should log start events."""
    logger = logging.getLogger("test_flow_events")
    middleware = log_flow_events(logger, start_level=logging.INFO)

    event = make_event("node_start")

    with caplog.at_level(logging.INFO):
        await middleware(event)

    assert "node_start" in caplog.text


@pytest.mark.asyncio
async def test_log_flow_events_success_event(caplog):
    """log_flow_events should log success events."""
    logger = logging.getLogger("test_flow_success")
    middleware = log_flow_events(logger, success_level=logging.DEBUG)

    event = make_event("node_success")

    with caplog.at_level(logging.DEBUG):
        await middleware(event)

    assert "node_success" in caplog.text


@pytest.mark.asyncio
async def test_log_flow_events_error_event_with_payload(caplog):
    """log_flow_events should include error_payload for error events."""
    logger = logging.getLogger("test_flow_error")
    middleware = log_flow_events(logger, error_level=logging.ERROR)

    event = make_event(
        "node_error",
        extra={"flow_error": {"code": "TEST_ERROR", "message": "Test error"}},
    )

    with caplog.at_level(logging.ERROR):
        await middleware(event)

    assert "node_error" in caplog.text


@pytest.mark.asyncio
async def test_log_flow_events_latency_callback():
    """log_flow_events should invoke latency callback."""
    callbacks = []

    def latency_cb(event_type: str, latency_ms: float, event: FlowEvent):
        callbacks.append((event_type, latency_ms))

    middleware = log_flow_events(latency_callback=latency_cb)

    event = make_event("node_success", latency_ms=50.0)
    await middleware(event)

    assert len(callbacks) == 1
    assert callbacks[0] == ("node_success", 50.0)


@pytest.mark.asyncio
async def test_log_flow_events_latency_callback_error(caplog):
    """log_flow_events should handle callback exceptions."""

    def failing_cb(event_type, latency_ms, event):
        raise RuntimeError("Callback failed")

    logger = logging.getLogger("test_flow_cb_error")
    middleware = log_flow_events(logger, latency_callback=failing_cb)

    event = make_event("node_success", latency_ms=50.0)

    with caplog.at_level(logging.ERROR):
        await middleware(event)

    assert "log_flow_events_latency_callback_error" in caplog.text


@pytest.mark.asyncio
async def test_log_flow_events_no_latency_ms():
    """log_flow_events should not invoke callback if latency_ms is None."""
    callbacks = []

    def latency_cb(event_type, latency_ms, event):
        callbacks.append((event_type, latency_ms))

    middleware = log_flow_events(latency_callback=latency_cb)

    event = make_event("node_success", latency_ms=None)
    await middleware(event)

    assert len(callbacks) == 0  # Callback should not be invoked
