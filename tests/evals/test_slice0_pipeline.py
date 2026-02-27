from __future__ import annotations

import json

import pytest

from penguiflow.evals.workflow import run_eval_workflow
from penguiflow.planner import Trajectory
from penguiflow.state.in_memory import InMemoryStateStore
from penguiflow.state.models import StoredEvent


@pytest.mark.asyncio
async def test_run_eval_workflow_produces_required_outputs_and_passes_holdout(tmp_path) -> None:
    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps(
            {
                "suite_id": "eval-suite",
                "workload": "planner_enterprise_agent_v2",
                "queries": [
                    {"query_id": "q-val", "text": "Question val", "answer": "A", "split": "val"},
                    {"query_id": "q-test", "text": "Question test", "answer": "B", "split": "test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trace_ids_path = tmp_path / "trace_ids.txt"
    trace_ids_path.write_text("trace-val\ntrace-test\n", encoding="utf-8")

    state_store = InMemoryStateStore()
    await state_store.save_event(
        StoredEvent(
            trace_id="trace-val",
            ts=1.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await state_store.save_event(
        StoredEvent(
            trace_id="trace-test",
            ts=2.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await state_store.save_trajectory(
        "trace-val",
        "session-1",
        Trajectory(
            query="Question val",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-1"},
            metadata={"tags": ["split:val", "dataset:eval"]},
        ),
    )
    await state_store.save_trajectory(
        "trace-test",
        "session-1",
        Trajectory(
            query="Question test",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-2"},
            metadata={"tags": ["split:test", "dataset:eval"]},
        ),
    )

    async def run_one(gold: dict[str, object], patch_bundle: dict[str, object] | None = None) -> str:
        prompt = None
        if isinstance(patch_bundle, dict):
            patches = patch_bundle.get("patches", {})
            if isinstance(patches, dict):
                prompt = patches.get("planner.system_prompt_extra")
        if prompt == "good":
            return str(gold.get("answer", ""))
        return "wrong"

    def metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> float:
        del trace, pred_name, pred_trace
        return 1.0 if isinstance(gold, dict) and pred == gold.get("answer") else 0.0

    result = await run_eval_workflow(
        state_store=state_store,
        query_suite_path=query_suite_path,
        trace_ids_path=trace_ids_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        candidates=[
            {"id": "bad", "patches": {"planner.system_prompt_extra": "bad"}},
            {"id": "good", "patches": {"planner.system_prompt_extra": "good"}},
        ],
        session_id="session-1",
    )

    assert result["winner_id"] == "good"
    assert (tmp_path / "trace.jsonl").exists()
    assert (tmp_path / "view.val.jsonl").exists()
    assert (tmp_path / "report.analyze.json").exists()
    assert (tmp_path / "report.harness.json").exists()
    assert (tmp_path / "best.patchbundle.json").exists()
    assert (tmp_path / "report.test.json").exists()
    holdout = json.loads((tmp_path / "report.test.json").read_text(encoding="utf-8"))
    assert holdout["winner_score"] >= holdout["baseline_score"]

    view_row = json.loads((tmp_path / "view.val.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "gold_trace_features" in view_row
    assert view_row["gold_trace_features"] is None
    assert "gold_policy" not in view_row
    assert "gold_trace" in view_row
    assert view_row["gold_trace"]["trace_id"] == view_row["__pf.trace_id"]
    assert view_row["gold_trace"]["inputs"]["llm_context"]["tenant_id"] == "tenant-a"
    assert "request_id" in view_row["gold_trace"]["inputs"]["tool_context"]
    assert "__pf.trace_id" in view_row


@pytest.mark.asyncio
async def test_run_eval_workflow_baseline_only_skips_duplicate_holdout_run(tmp_path) -> None:
    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps(
            {
                "suite_id": "eval-suite",
                "queries": [
                    {"query_id": "q-val", "text": "Question val", "answer": "A", "split": "val"},
                    {"query_id": "q-test", "text": "Question test", "answer": "B", "split": "test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trace_ids_path = tmp_path / "trace_ids.txt"
    trace_ids_path.write_text("trace-val\ntrace-test\n", encoding="utf-8")

    state_store = InMemoryStateStore()
    await state_store.save_event(
        StoredEvent(
            trace_id="trace-val",
            ts=1.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await state_store.save_event(
        StoredEvent(
            trace_id="trace-test",
            ts=2.0,
            kind="node_succeeded",
            node_name="triage_query",
            node_id="triage_query",
            payload={"ok": True},
        )
    )
    await state_store.save_trajectory(
        "trace-val",
        "session-1",
        Trajectory(
            query="Question val",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-1"},
            metadata={"tags": ["split:val", "dataset:eval"]},
        ),
    )
    await state_store.save_trajectory(
        "trace-test",
        "session-1",
        Trajectory(
            query="Question test",
            llm_context={"tenant_id": "tenant-a"},
            tool_context={"request_id": "r-2"},
            metadata={"tags": ["split:test", "dataset:eval"]},
        ),
    )

    run_one_calls = 0

    async def run_one(gold: dict[str, object], patch_bundle: dict[str, object] | None = None) -> str:
        del patch_bundle
        nonlocal run_one_calls
        run_one_calls += 1
        return str(gold.get("answer", ""))

    def metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> float:
        del trace, pred_name, pred_trace
        return 1.0 if isinstance(gold, dict) and pred == gold.get("answer") else 0.0

    result = await run_eval_workflow(
        state_store=state_store,
        query_suite_path=query_suite_path,
        trace_ids_path=trace_ids_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        candidates=[],
        session_id="session-1",
    )

    assert result["winner_id"] == "baseline"
    assert run_one_calls == 2
