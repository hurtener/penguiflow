"""Unit tests for FlowEvent observability helpers."""

from __future__ import annotations

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
