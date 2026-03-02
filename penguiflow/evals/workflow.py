"""End-to-end eval workflow orchestration for quick PoC validation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analyze import run_analyze_only
from .export import export_trace_dataset
from .inputs import load_query_suite, load_trace_ids
from .runner import run_harness_eval
from .sweep import run_manual_sweep


def _write_view_rows(
    path: Path,
    queries: list[dict[str, Any]],
    split: str,
    query_trace_map: dict[str, str],
    trace_rows_by_id: dict[str, dict[str, Any]],
) -> None:
    rows = []
    for query in queries:
        if query.get("split") != split:
            continue
        query_id = str(query.get("query_id"))
        trace_id = query_trace_map.get(query_id)
        custom_features = query.get("gold_trace_features")
        policy = dict(custom_features) if isinstance(custom_features, dict) else None
        rows.append(
            {
                "example_id": query_id,
                "split": split,
                "question": query.get("text"),
                "answer": query.get("answer"),
                "gold_trace_features": policy,
                "__pf.trace_id": trace_id,
                "gold_trace": trace_rows_by_id.get(trace_id) if isinstance(trace_id, str) else None,
            }
        )
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _mean_score(path: Path) -> float:
    scores: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        scores.append(float(payload.get("score", 0.0)))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _load_trace_rows_by_id(trace_path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        row = json.loads(stripped)
        trace_id = row.get("trace_id")
        if isinstance(trace_id, str):
            rows[trace_id] = row
    return rows


def _is_baseline_bundle(bundle: dict[str, Any]) -> bool:
    """Return whether the winner bundle represents baseline-only behavior.

    Baseline-only runs produce an empty patch set. Reusing the baseline score
    for holdout avoids re-running the same prediction path twice.
    """

    patches = bundle.get("patches")
    return isinstance(patches, dict) and not patches


async def run_eval_workflow(
    *,
    state_store: Any,
    query_suite_path: str | Path,
    trace_ids_path: str | Path,
    output_dir: str | Path,
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    candidates: list[dict[str, Any]],
    session_id: str | None = None,
    workload: str | None = None,
) -> dict[str, Any]:
    """Run eval workflow: export -> analyze -> sweep -> holdout gate."""

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    query_suite = load_query_suite(query_suite_path)
    trace_ids = load_trace_ids(trace_ids_path)

    export_result = await export_trace_dataset(
        state_store=state_store,
        trace_ids=trace_ids,
        output_dir=out_dir,
        session_id=session_id,
        workload=workload,
    )
    await run_analyze_only(trace_path=export_result["trace_path"], output_dir=out_dir)

    queries = query_suite.get("queries", [])
    query_trace_map = {
        str(query.get("query_id")): trace_id for query, trace_id in zip(queries, trace_ids, strict=False)
    }
    trace_rows_by_id = _load_trace_rows_by_id(export_result["trace_path"])
    val_view = out_dir / "view.val.jsonl"
    test_view = out_dir / "view.test.jsonl"
    _write_view_rows(val_view, queries, "val", query_trace_map, trace_rows_by_id)
    _write_view_rows(test_view, queries, "test", query_trace_map, trace_rows_by_id)

    sweep_result = await run_manual_sweep(
        dataset_path=val_view,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric,
        candidates=candidates,
        workload=workload,
    )

    bundle_path = Path(sweep_result["bundle_path"])
    winner_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    baseline_test = await run_harness_eval(
        dataset_path=test_view,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric,
        mode="test_baseline",
        pred_name="test_baseline",
        patch_bundle=None,
    )

    baseline_score = _mean_score(Path(baseline_test["results_path"]))
    if _is_baseline_bundle(winner_bundle):
        winner_score = baseline_score
    else:
        winner_test = await run_harness_eval(
            dataset_path=test_view,
            output_dir=out_dir,
            run_one=run_one,
            metric=metric,
            mode="test_winner",
            pred_name="test_winner",
            patch_bundle=winner_bundle,
        )
        winner_score = _mean_score(Path(winner_test["results_path"]))
    passed = winner_score >= baseline_score
    report_test = {
        "baseline_score": baseline_score,
        "winner_score": winner_score,
        "passed": passed,
        "winner_id": sweep_result["winner_id"],
    }
    report_test_path = out_dir / "report.test.json"
    report_test_path.write_text(json.dumps(report_test, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    if not passed:
        raise ValueError("holdout regression detected")

    return {
        "winner_id": sweep_result["winner_id"],
        "trace_path": export_result["trace_path"],
        "report_analyze_path": str(out_dir / "report.analyze.json"),
        "report_harness_path": str(out_dir / "report.harness.json"),
        "report_test_path": str(report_test_path),
        "bundle_path": str(bundle_path),
    }


__all__ = ["run_eval_workflow"]
