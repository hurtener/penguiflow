from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import evaluate_dataset


@pytest.mark.asyncio
async def test_evaluate_dataset_runs_sweep_and_holdout(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        if patch_bundle and patch_bundle.get("id") == "winner":
            return str(gold.get("answer"))
        return "wrong"

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    result = await evaluate_dataset(
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
        run_one=run_one,
        metric=metric,
        candidates=[
            {"id": "baseline", "patches": {}},
            {"id": "winner", "patches": {}},
        ],
    )

    assert result["winner_id"] == "winner"
    assert result["passed"] is True
    assert not (tmp_path / "out").exists()


@pytest.mark.asyncio
async def test_evaluate_dataset_baseline_only_skips_duplicate_holdout_run(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    run_one_calls = 0

    async def run_one(gold, patch_bundle=None):
        del patch_bundle
        nonlocal run_one_calls
        run_one_calls += 1
        return str(gold.get("answer"))

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    result = await evaluate_dataset(
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
        run_one=run_one,
        metric=metric,
        candidates=[],
    )

    assert result["winner_id"] == "baseline"
    assert run_one_calls == 2


@pytest.mark.asyncio
async def test_evaluate_dataset_writes_single_report_when_requested(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace_features": None,
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        if patch_bundle and patch_bundle.get("id") == "winner":
            return str(gold.get("answer"))
        return "wrong"

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    report_path = tmp_path / "report.json"
    result = await evaluate_dataset(
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
        run_one=run_one,
        metric=metric,
        candidates=[
            {"id": "baseline", "patches": {}},
            {"id": "winner", "patches": {}},
        ],
        report_path=report_path,
    )

    assert report_path.exists()
    assert result["report_path"] == str(report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["winner_id"] == "winner"
    assert payload["passed"] is True
