from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import TraceSelector, export_dataset
from penguiflow.planner import PlannerAction, Trajectory, TrajectoryStep
from penguiflow.state import InMemoryStateStore
from penguiflow.state.models import StoredEvent


@pytest.mark.asyncio
async def test_export_dataset_selects_by_all_tags(tmp_path: Path) -> None:
    store = InMemoryStateStore()
    await store.save_event(
        StoredEvent(
            trace_id="trace-val",
            ts=1.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await store.save_event(
        StoredEvent(
            trace_id="trace-test",
            ts=2.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await store.save_trajectory(
        "trace-val",
        "session-a",
        Trajectory(
            query="Question val",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-1"},
            metadata={"tags": ["dataset:v1", "split:val"]},
            steps=[
                TrajectoryStep(
                    action=PlannerAction(next_node="na_turn", args={"query": "Question val"}),
                    observation={"action_required": "clarification_required"},
                )
            ],
        ),
    )
    await store.save_trajectory(
        "trace-test",
        "session-b",
        Trajectory(
            query="Question test",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-2"},
            metadata={"tags": ["dataset:v1", "split:test"]},
        ),
    )

    result = await export_dataset(
        state_store=store,
        output_dir=tmp_path,
        selector=TraceSelector(include_tags=("dataset:v1", "split:val")),
    )

    assert result["trace_count"] == 1
    dataset_path = Path(result["dataset_path"])
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["split"] == "val"
    assert rows[0]["question"] == "Question val"
    assert rows[0]["gold_trace_features"] is None
    assert isinstance(rows[0]["gold_trace"]["trajectory_full"]["steps"], list)
    assert (
        rows[0]["gold_trace"]["trajectory_full"]["steps"][0]["observation"]["action_required"]
        == "clarification_required"
    )


@pytest.mark.asyncio
async def test_export_dataset_defaults_to_all_traces(tmp_path: Path) -> None:
    store = InMemoryStateStore()
    await store.save_event(
        StoredEvent(
            trace_id="trace-1",
            ts=1.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await store.save_trajectory("trace-1", "session-a", Trajectory(query="Q1", metadata={"tags": ["a"]}))
    await store.save_event(
        StoredEvent(
            trace_id="trace-2",
            ts=2.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await store.save_trajectory("trace-2", "session-b", Trajectory(query="Q2", metadata={"tags": ["b"]}))

    result = await export_dataset(state_store=store, output_dir=tmp_path)

    assert result["trace_count"] == 2
