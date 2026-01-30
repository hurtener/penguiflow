from __future__ import annotations

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.telemetry import AgentTelemetry
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
