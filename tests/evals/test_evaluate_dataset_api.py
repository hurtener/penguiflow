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
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
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
        run_one=run_one,
        metric=metric,
        candidates=[
            {"id": "baseline", "patches": {}},
            {"id": "winner", "patches": {"planner.system_prompt_extra": "good"}},
        ],
    )

    assert result["mode"] == "candidates"
    assert result["winner_id"] == "winner"
    assert result["passed_holdout_regression"] is True
    assert result["test_baseline_score"] == 0.0
    assert result["test_winner_score"] == 1.0
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
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
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
        run_one=run_one,
        metric=metric,
        candidates=[],
    )

    assert result["mode"] == "baseline"
    assert "winner_id" not in result
    assert result["test_score"] == 1.0
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
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
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
        run_one=run_one,
        metric=metric,
        candidates=[
            {"id": "baseline", "patches": {}},
            {"id": "winner", "patches": {"planner.system_prompt_extra": "good"}},
        ],
        report_path=report_path,
    )

    assert report_path.exists()
    assert result["report_path"] == str(report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "candidates"
    assert payload["winner_id"] == "winner"
    assert payload["passed_holdout_regression"] is True


@pytest.mark.asyncio
async def test_evaluate_dataset_baseline_fails_below_min_test_score(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        del gold, patch_bundle
        return "wrong"

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    with pytest.raises(ValueError, match="min_test_score"):
        await evaluate_dataset(
            dataset_path=dataset_path,
            run_one=run_one,
            metric=metric,
            candidates=[],
            min_test_score=0.5,
        )


@pytest.mark.asyncio
async def test_evaluate_dataset_candidate_fails_threshold_even_if_beats_baseline(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q3",
            "split": "test",
            "question": "Q3",
            "answer": "A3",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        if patch_bundle and patch_bundle.get("id") == "winner" and gold.get("example_id") == "q2":
            return str(gold.get("answer"))
        return "wrong"

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    with pytest.raises(ValueError, match="min_test_score"):
        await evaluate_dataset(
            dataset_path=dataset_path,
            run_one=run_one,
            metric=metric,
            candidates=[{"id": "winner", "patches": {}}],
            min_test_score=0.9,
        )


@pytest.mark.asyncio
async def test_evaluate_dataset_allows_val_only_dataset_for_diagnostics(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        }
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        del patch_bundle
        return str(gold.get("answer"))

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    result = await evaluate_dataset(
        dataset_path=dataset_path,
        run_one=run_one,
        metric=metric,
        candidates=[],
    )

    assert result["mode"] == "baseline"
    assert result["val_score"] == 1.0
    assert result["test_score"] is None
    assert result["counts"] == {"val": 1, "test": 0, "total": 1}
    assert result["passed_threshold"] is True


@pytest.mark.asyncio
async def test_evaluate_dataset_rejects_test_only_dataset(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "test",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        }
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        del patch_bundle
        return str(gold.get("answer"))

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    with pytest.raises(ValueError, match="at least one val"):
        await evaluate_dataset(
            dataset_path=dataset_path,
            run_one=run_one,
            metric=metric,
            candidates=[],
        )


@pytest.mark.asyncio
async def test_evaluate_dataset_val_only_with_min_test_score_is_diagnostic(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        }
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        del patch_bundle
        return str(gold.get("answer"))

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    result = await evaluate_dataset(
        dataset_path=dataset_path,
        run_one=run_one,
        metric=metric,
        candidates=[],
        min_test_score=0.9,
    )

    assert result["test_score"] is None
    assert result["passed_threshold"] is None
    assert result["min_test_score"] == 0.9


@pytest.mark.asyncio
async def test_evaluate_dataset_awaits_async_metric(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    rows = [
        {
            "example_id": "q1",
            "split": "val",
            "question": "Q1",
            "answer": "A1",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
        {
            "example_id": "q2",
            "split": "test",
            "question": "Q2",
            "answer": "A2",
            "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
        },
    ]
    dataset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    async def run_one(gold, patch_bundle=None):
        del patch_bundle
        return str(gold.get("answer"))

    async def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        del trace, pred_name, pred_trace
        return 1.0 if pred == gold.get("answer") else 0.0

    result = await evaluate_dataset(
        dataset_path=dataset_path,
        run_one=run_one,
        metric=metric,
        candidates=[],
    )

    assert result["mode"] == "baseline"
    assert result["val_score"] == 1.0
    assert result["test_score"] == 1.0
