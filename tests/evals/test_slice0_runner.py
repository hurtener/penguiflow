from __future__ import annotations

import json

import pytest

from penguiflow.evals.api import wrap_metric
from penguiflow.evals.runner import run_harness_eval


@pytest.mark.asyncio
async def test_run_harness_eval_writes_baseline_predictions_and_results(tmp_path) -> None:
    view_path = tmp_path / "view.jsonl"
    examples = [
        {"example_id": "e1", "split": "val", "question": "q1", "answer": "A"},
        {"example_id": "e2", "split": "val", "question": "q2", "answer": "B"},
    ]
    view_path.write_text("\n".join(json.dumps(row) for row in examples) + "\n", encoding="utf-8")

    async def run_one(
        gold: dict[str, object], patch_bundle: dict[str, object] | None = None
    ) -> tuple[str, dict[str, object]]:
        del patch_bundle
        return (str(gold["answer"]), {"trace": gold["example_id"]})

    def metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> dict[str, object]:
        del trace
        assert pred_name == "baseline"
        assert pred_trace is not None
        score = 1.0 if isinstance(gold, dict) and pred == gold.get("answer") else 0.0
        return {"score": score, "feedback": "ok"}

    result = await run_harness_eval(
        dataset_path=view_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        mode="baseline",
        pred_name="baseline",
    )

    assert result["example_count"] == 2
    assert (tmp_path / "predictions.baseline.jsonl").exists()
    assert (tmp_path / "results.baseline.jsonl").exists()
    report = json.loads((tmp_path / "report.harness.json").read_text(encoding="utf-8"))
    assert report["primary_metric"] == "score"
    assert report["modes"]["baseline"]["mean_score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_run_harness_eval_reports_context_stability(tmp_path) -> None:
    view_path = tmp_path / "view.jsonl"
    examples = [
        {
            "example_id": "e1",
            "split": "val",
            "question": "q1",
            "gold_trace": {"inputs": {"llm_context": {"tenant": "a"}, "tool_context": {"req": "1"}}},
        },
        {
            "example_id": "e2",
            "split": "val",
            "question": "q2",
            "gold_trace": {"inputs": {"llm_context": {"tenant": "a"}, "tool_context": {"req": "2"}}},
        },
    ]
    view_path.write_text("\n".join(json.dumps(row) for row in examples) + "\n", encoding="utf-8")

    async def run_one(
        gold: dict[str, object], patch_bundle: dict[str, object] | None = None
    ) -> tuple[str, dict[str, object]]:
        del patch_bundle
        if gold.get("example_id") == "e1":
            return (
                "ok",
                {
                    "llm_context": {"tenant": "a"},
                    "tool_context": {"req": "1"},
                },
            )
        return (
            "ok",
            {
                "llm_context": {"tenant": "a"},
                "tool_context": {"req": "DIFF"},
            },
        )

    def metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> float:
        del gold, pred, trace, pred_name, pred_trace
        return 1.0

    await run_harness_eval(
        dataset_path=view_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        mode="baseline",
        pred_name="baseline",
    )

    report = json.loads((tmp_path / "report.harness.json").read_text(encoding="utf-8"))
    mode = report["modes"]["baseline"]
    assert mode["context_match_rate"] == pytest.approx(0.5)
    assert mode["context_stability_pass"] is False


@pytest.mark.asyncio
async def test_run_harness_eval_ignores_ephemeral_context_keys_by_default(tmp_path) -> None:
    view_path = tmp_path / "view.jsonl"
    examples = [
        {
            "example_id": "e1",
            "split": "val",
            "question": "q1",
            "gold_trace": {
                "inputs": {
                    "llm_context": {"tenant": "a"},
                    "tool_context": {"tenant": "a", "trace_id": "g1", "session_id": "s1"},
                }
            },
        }
    ]
    view_path.write_text("\n".join(json.dumps(row) for row in examples) + "\n", encoding="utf-8")

    async def run_one(
        gold: dict[str, object], patch_bundle: dict[str, object] | None = None
    ) -> tuple[str, dict[str, object]]:
        del gold, patch_bundle
        return (
            "ok",
            {
                "llm_context": {"tenant": "a"},
                "tool_context": {"tenant": "a", "trace_id": "p9", "session_id": "s9", "__pf_patch_bundle": {"id": "x"}},
            },
        )

    await run_harness_eval(
        dataset_path=view_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=lambda *args, **kwargs: 1.0,
        mode="baseline",
        pred_name="baseline",
    )

    report = json.loads((tmp_path / "report.harness.json").read_text(encoding="utf-8"))
    mode = report["modes"]["baseline"]
    assert mode["context_match_rate"] == pytest.approx(1.0)
    assert mode["context_stability_pass"] is True


@pytest.mark.asyncio
async def test_run_harness_eval_ignore_keys_can_be_overridden(tmp_path) -> None:
    view_path = tmp_path / "view.jsonl"
    examples = [
        {
            "example_id": "e1",
            "split": "val",
            "question": "q1",
            "gold_trace": {
                "inputs": {
                    "llm_context": {"tenant": "a"},
                    "tool_context": {"tenant": "a", "trace_id": "g1"},
                }
            },
        }
    ]
    view_path.write_text("\n".join(json.dumps(row) for row in examples) + "\n", encoding="utf-8")

    async def run_one(
        gold: dict[str, object], patch_bundle: dict[str, object] | None = None
    ) -> tuple[str, dict[str, object]]:
        del gold, patch_bundle
        return ("ok", {"llm_context": {"tenant": "a"}, "tool_context": {"tenant": "a", "trace_id": "DIFF"}})

    await run_harness_eval(
        dataset_path=view_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=lambda *args, **kwargs: 1.0,
        mode="baseline",
        pred_name="baseline",
        ignore_keys=(),
    )

    report = json.loads((tmp_path / "report.harness.json").read_text(encoding="utf-8"))
    mode = report["modes"]["baseline"]
    assert mode["context_match_rate"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_run_harness_eval_awaits_async_metric(tmp_path) -> None:
    """An async metric must be awaited, not scored as a coroutine.

    Without the await, ``_as_score_payload`` receives a coroutine, matches none of
    its branches and silently returns ``(0.0, None)`` -- wrong scores with no error.
    """

    view_path = tmp_path / "view.jsonl"
    examples = [
        {"example_id": "e1", "split": "val", "question": "q1", "answer": "A"},
        {"example_id": "e2", "split": "val", "question": "q2", "answer": "B"},
    ]
    view_path.write_text("\n".join(json.dumps(row) for row in examples) + "\n", encoding="utf-8")

    async def run_one(
        gold: dict[str, object], patch_bundle: dict[str, object] | None = None
    ) -> tuple[str, dict[str, object]]:
        del patch_bundle
        return (str(gold["answer"]), {"trace": gold["example_id"]})

    async def async_metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> dict[str, object]:
        del trace, pred_name, pred_trace
        score = 1.0 if isinstance(gold, dict) and pred == gold.get("answer") else 0.0
        return {"score": score, "feedback": "ok"}

    # wrap_metric is the real production composition; it is a sync wrapper that
    # hands back whatever the metric returned -- a coroutine for an async metric.
    await run_harness_eval(
        dataset_path=view_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=wrap_metric(async_metric),
        mode="baseline",
        pred_name="baseline",
    )

    report = json.loads((tmp_path / "report.harness.json").read_text(encoding="utf-8"))
    assert report["modes"]["baseline"]["mean_score"] == pytest.approx(1.0)

    result_lines = (tmp_path / "results.baseline.jsonl").read_text(encoding="utf-8").splitlines()
    results = [json.loads(line) for line in result_lines if line.strip()]
    assert [row["score"] for row in results] == [pytest.approx(1.0), pytest.approx(1.0)]
    assert {row["feedback"] for row in results} == {"ok"}


def test_wrap_metric_supports_positional_args_with_var_kwargs() -> None:
    """A metric that names only gold/pred and absorbs the rest via **kwargs.

    The adapter already handles the all-**kwargs and the exact five-arg forms;
    the mixed form used to bind gold/pred twice and raise TypeError.
    """

    seen: dict[str, object] = {}

    def metric(gold: object, pred: object, **kwargs: object) -> float:
        seen["gold"] = gold
        seen["pred"] = pred
        seen.update(kwargs)
        return 1.0

    assert wrap_metric(metric)("g", "p", "t", "pred-name", "pt") == 1.0
    assert seen == {
        "gold": "g",
        "pred": "p",
        "trace": "t",
        "pred_name": "pred-name",
        "pred_trace": "pt",
    }
