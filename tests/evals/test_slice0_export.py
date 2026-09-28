from __future__ import annotations

import json

import pytest

from penguiflow.evals.export import collect_trace_rows, export_trace_dataset
from penguiflow.planner import PlannerAction, PlannerEvent, Trajectory, TrajectoryStep
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
            steps=[
                TrajectoryStep(
                    action=PlannerAction(next_node="na_turn", args={"query": "Route this query"}),
                    observation={"action_required": "clarification_required"},
                ),
                TrajectoryStep(
                    action=PlannerAction(next_node="final_response", args={"answer": "Need clarification"}),
                    observation={"action_required": "clarification_required", "workflow_complete": False},
                ),
            ],
        ),
    )

    await export_trace_dataset(
        state_store=store,
        trace_ids=[trace_id],
        output_dir=tmp_path,
        session_id=session_id,
        workload="demo_workload",
    )

    row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["query"] == "Route this query"
    # The saved flow history contains a node_failed event, so the trace is a
    # failure even though the trajectory's own steps carry no error.
    assert row["outputs"]["status"] == "error"
    assert row["events"]["planner_events"][0]["event_type"] == "step_complete"
    assert row["inputs"]["llm_context"]["tenant_id"] == "acme"
    assert row["inputs"]["tool_context"]["request_id"] == "req-123"
    assert row["provenance"]["state_store"]["source_priority"] == "trajectory"
    assert isinstance(row.get("trajectory_full", {}).get("steps"), list)
    assert row["trajectory_full"]["steps"][-1]["observation"]["action_required"] == "clarification_required"
    assert "trajectory_full" in row["redaction"]["fields_included"]

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["total"] == 1
    assert manifest["workload"] == "demo_workload"
    assert manifest["source"]["source_priority"] == ["trajectory", "planner_events", "history"]
    assert manifest["redaction_policy"] == "internal_safe"


async def _row_for_trajectory(store: InMemoryStateStore, trajectory: Trajectory) -> dict:
    """Save one trajectory and return its collected TraceExampleV1 row."""

    trace_id, session_id = "trace-final", "session-final"
    await store.save_trajectory(trace_id, session_id, trajectory)
    collected = await collect_trace_rows(
        state_store=store,
        trace_ids=[trace_id],
        trace_refs=[{"trace_id": trace_id, "session_id": session_id}],
    )
    return collected["rows"][0]


@pytest.mark.asyncio
async def test_collect_trace_rows_populates_final_from_terminal_step() -> None:
    row = await _row_for_trajectory(
        InMemoryStateStore(),
        Trajectory(
            query="What is the answer?",
            steps=[
                TrajectoryStep(action=PlannerAction(next_node="lookup", args={"q": "answer"})),
                TrajectoryStep(action=PlannerAction(next_node="final_response", args={"answer": "42"})),
            ],
        ),
    )

    assert row["outputs"]["final"] == "42"
    assert "outputs.final" in row["redaction"]["fields_included"]


@pytest.mark.asyncio
async def test_collect_trace_rows_populates_final_from_legacy_raw_answer() -> None:
    # The on-disk legacy shape: terminal steps were dumped with next_node=None and
    # the answer under args.raw_answer.
    legacy_payload = {
        "query": "What is the answer?",
        "steps": [
            {
                "action": {"thought": "done", "next_node": None, "args": {"raw_answer": "legacy"}},
                "observation": None,
                "error": None,
                "failure": None,
            }
        ],
    }
    row = await _row_for_trajectory(InMemoryStateStore(), Trajectory.from_serialised(legacy_payload))

    assert row["outputs"]["final"] == "legacy"


@pytest.mark.asyncio
async def test_collect_trace_rows_final_is_none_without_terminal_step() -> None:
    row = await _row_for_trajectory(
        InMemoryStateStore(),
        Trajectory(
            query="Still working",
            steps=[TrajectoryStep(action=PlannerAction(next_node="lookup", args={"q": "answer"}))],
        ),
    )

    assert row["outputs"]["final"] is None


@pytest.mark.asyncio
async def test_collect_trace_rows_prefers_stored_final_answer() -> None:
    """The planner-recorded answer wins, even with no terminal step present.

    The ordinary answer path never appends a terminal step, so this is the only
    route by which a production trace carries its answer.
    """
    row = await _row_for_trajectory(
        InMemoryStateStore(),
        Trajectory(
            query="What is the answer?",
            steps=[TrajectoryStep(action=PlannerAction(next_node="lookup", args={"q": "answer"}))],
            final_answer="stored answer",
        ),
    )

    assert row["outputs"]["final"] == "stored answer"


@pytest.mark.asyncio
async def test_collect_trace_rows_status_reflects_finish_reason() -> None:
    """A run that ended without answering must not export as a success.

    Steps can all succeed individually while the run still ends on an exhausted
    budget or iteration limit, so step errors alone cannot decide status.
    """
    exhausted = await _row_for_trajectory(
        InMemoryStateStore(),
        Trajectory(
            query="Still working",
            steps=[TrajectoryStep(action=PlannerAction(next_node="lookup", args={"q": "answer"}))],
            finish_reason="budget_exhausted",
        ),
    )

    assert exhausted["outputs"]["status"] == "error"
    assert exhausted["trajectory"]["finish_reason"] == "budget_exhausted"
    assert "trajectory.finish_reason" in exhausted["redaction"]["fields_included"]

    answered = await _row_for_trajectory(
        InMemoryStateStore(),
        Trajectory(
            query="What is the answer?",
            steps=[TrajectoryStep(action=PlannerAction(next_node="lookup", args={"q": "answer"}))],
            finish_reason="answer_complete",
            final_answer="42",
        ),
    )

    assert answered["outputs"]["status"] == "ok"


@pytest.mark.asyncio
async def test_collect_trace_rows_status_reflects_flow_history_failure() -> None:
    """A flow-level failure must not be masked by a clean trajectory.

    Step errors and flow events are two independent failure signals; reading
    only the first reports a trace that timed out mid-flow as a success.
    """
    store = InMemoryStateStore()
    trace_id, session_id = "trace-final", "session-final"
    await store.save_event(
        StoredEvent(
            trace_id=trace_id,
            ts=1.0,
            kind="node_timeout",
            node_name="slow_tool",
            node_id="slow_tool",
            payload={"error": "timed out"},
        )
    )
    await store.save_trajectory(
        trace_id,
        session_id,
        Trajectory(
            query="What is the answer?",
            steps=[TrajectoryStep(action=PlannerAction(next_node="slow_tool", args={"q": "answer"}))],
            finish_reason="answer_complete",
            final_answer="42",
        ),
    )

    collected = await collect_trace_rows(
        state_store=store,
        trace_ids=[trace_id],
        trace_refs=[{"trace_id": trace_id, "session_id": session_id}],
    )

    assert collected["rows"][0]["outputs"]["status"] == "error"
