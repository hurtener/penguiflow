from __future__ import annotations

import asyncio

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.main import _format_status_for_terminal
from examples.planner_enterprise_agent_v2.nodes import RoadmapStep, StatusUpdate
from examples.planner_enterprise_agent_v2.telemetry import AgentTelemetry
from penguiflow.metrics import FlowEvent
from penguiflow.planner import PlannerEvent


def test_agent_telemetry_counts_auto_seq_events() -> None:
    config = AgentConfig.from_env()
    telemetry = AgentTelemetry(config)

    telemetry.record_planner_event(
        PlannerEvent(
            event_type="auto_seq_detected_unique",
            ts=0.0,
            trajectory_step=1,
            extra={"payload_fingerprint": "abc"},
        )
    )
    telemetry.record_planner_event(
        PlannerEvent(
            event_type="auto_seq_executed",
            ts=1.0,
            trajectory_step=2,
            extra={"tool_name": "analyze_documents"},
        )
    )

    metrics = telemetry.get_metrics()
    assert metrics["auto_seq_detected_unique"] == 1
    assert metrics["auto_seq_executed"] == 1


def test_terminal_status_formatter_reports_roadmap_size() -> None:
    update = StatusUpdate(
        status="thinking",
        roadmap=[RoadmapStep(id=1, name="Plan", description="Prepare work")],
    )

    assert "Roadmap: 1 steps" in _format_status_for_terminal(update, "trace-1")


def test_telemetry_flow_middleware_returns_none() -> None:
    telemetry = AgentTelemetry(AgentConfig.from_env())
    event = FlowEvent(
        event_type="node_start",
        ts=0.0,
        node_name="node",
        node_id="node-1",
        trace_id="trace-1",
        attempt=1,
        latency_ms=None,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=0,
        trace_pending=None,
        trace_inflight=0,
        trace_cancelled=False,
    )

    assert asyncio.run(telemetry.record_flow_event(event)) is None
