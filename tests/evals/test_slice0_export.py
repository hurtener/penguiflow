from __future__ import annotations

import json

import pytest

from penguiflow.evals.export import export_trace_dataset
from penguiflow.planner import PlannerEvent, Trajectory
from penguiflow.state.in_memory import InMemoryStateStore
from penguiflow.state.models import StoredEvent


@pytest.mark.asyncio
async def test_export_trace_dataset_writes_minimal_traceexample_row(tmp_path) -> None:
    store = InMemoryStateStore()
    trace_id = "trace-001"
    await store.save_event(
        StoredEvent(
            trace_id=trace_id,
            ts=1.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )

    result = await export_trace_dataset(
        state_store=store,
        trace_ids=[trace_id],
        output_dir=tmp_path,
    )

    assert result["trace_count"] == 1
    trace_path = tmp_path / "trace.jsonl"
    assert trace_path.exists()
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == "TraceExampleV1"
    assert row["trace_id"] == trace_id
    assert row["outputs"]["status"] == "ok"
    assert row["redaction"]["profile"] == "internal_safe"
    assert "provenance" in row


@pytest.mark.asyncio
async def test_export_prefers_trajectory_then_planner_events_and_writes_manifest(tmp_path) -> None:
    store = InMemoryStateStore()
    trace_id = "trace-002"
    session_id = "session-002"

    await store.save_event(
        StoredEvent(
            trace_id=trace_id,
            ts=1.0,
            kind="node_failed",
            node_name="collect_logs",
            node_id="collect_logs",
            payload={"error": "boom"},
        )
    )
    await store.save_planner_event(
        trace_id,
        PlannerEvent(
            event_type="step_complete",
            ts=2.0,
            trajectory_step=1,
            node_name="triage_query",
            latency_ms=120.0,
        ),
    )
    await store.save_trajectory(
        trace_id,
        session_id,
        Trajectory(
            query="Route this query",
            llm_context={"tenant_id": "acme", "conversation_memory": {"summary": "prior"}},
            tool_context={"request_id": "req-123", "session_id": session_id},
            metadata={"tags": ["dataset:eval", "split:val"]},
        ),
    )

    await export_trace_dataset(
        state_store=store,
        trace_ids=[trace_id],
        output_dir=tmp_path,
        session_id=session_id,
        workload="examples.planner_enterprise_agent_v2",
    )

    row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["query"] == "Route this query"
    assert row["outputs"]["status"] == "ok"
    assert row["events"]["planner_events"][0]["event_type"] == "step_complete"
    assert row["inputs"]["llm_context"]["tenant_id"] == "acme"
    assert row["inputs"]["tool_context"]["request_id"] == "req-123"
    assert row["provenance"]["state_store"]["source_priority"] == "trajectory"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["total"] == 1
    assert manifest["workload"] == "examples.planner_enterprise_agent_v2"
    assert manifest["source"]["source_priority"] == ["trajectory", "planner_events", "history"]
    assert manifest["redaction_policy"] == "internal_safe"
