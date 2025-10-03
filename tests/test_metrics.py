"""Unit tests for FlowEvent observability helpers."""

from __future__ import annotations

import pytest

from penguiflow.debug import format_flow_event
from penguiflow.metrics import FlowEvent


def test_flow_event_payload_and_metrics() -> None:
    event = FlowEvent(
        event_type="node_success",
        ts=1700000000.0,
        node_name="worker",
        node_id="node-1",
        trace_id="trace-123",
        attempt=1,
        latency_ms=12.5,
        queue_depth_in=2,
        queue_depth_out=1,
        outgoing_edges=3,
        queue_maxsize=64,
        trace_pending=5,
        trace_inflight=1,
        trace_cancelled=False,
        extra={"custom_tag": "alpha", "latency_ms": "21.0"},
    )

    payload = event.to_payload()
    assert payload["event"] == "node_success"
    assert payload["q_depth_total"] == 3
    assert payload["trace_pending"] == 5
    assert payload["custom_tag"] == "alpha"

    metrics = event.metric_samples()
    assert metrics["queue_depth_total"] == 3.0
    assert metrics["latency_ms"] == 21.0
    assert metrics["trace_pending"] == 5.0

    tags = event.tag_values()
    assert tags["event_type"] == "node_success"
    assert tags["node_name"] == "worker"
    assert tags["custom_tag"] == "alpha"


def test_flow_event_error_payload_property() -> None:
    event = FlowEvent(
        event_type="node_failed",
        ts=1700000001.0,
        node_name="worker",
        node_id="node-1",
        trace_id="trace-err",
        attempt=2,
        latency_ms=None,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=64,
        trace_pending=None,
        trace_inflight=0,
        trace_cancelled=False,
        extra={
            "flow_error": {
                "code": "NODE_EXCEPTION",
                "message": "boom",
                "trace_id": "trace-err",
                "node_name": "worker",
            }
        },
    )

    payload = event.error_payload
    assert payload is not None
    assert payload["code"] == "NODE_EXCEPTION"
    assert payload["message"] == "boom"

    with pytest.raises(TypeError):
        payload["code"] = "MUTATED"  # type: ignore[index]


def test_flow_event_error_payload_absent() -> None:
    event = FlowEvent(
        event_type="node_success",
        ts=1700000002.0,
        node_name="worker",
        node_id="node-1",
        trace_id="trace-ok",
        attempt=1,
        latency_ms=5.0,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=64,
        trace_pending=None,
        trace_inflight=0,
        trace_cancelled=False,
        extra={},
    )

    assert event.error_payload is None


def test_format_flow_event_flattens_error_payload() -> None:
    event = FlowEvent(
        event_type="node_failed",
        ts=1700000003.0,
        node_name="worker",
        node_id="node-1",
        trace_id="trace-err",
        attempt=1,
        latency_ms=None,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=64,
        trace_pending=None,
        trace_inflight=0,
        trace_cancelled=False,
        extra={
            "flow_error": {
                "code": "NODE_TIMEOUT",
                "message": "too slow",
            },
        },
    )

    formatted = format_flow_event(event)

    assert formatted["event"] == "node_failed"
    assert formatted["flow_error_code"] == "NODE_TIMEOUT"
    assert formatted["flow_error_message"] == "too slow"
    assert formatted["flow_error"]["code"] == "NODE_TIMEOUT"
