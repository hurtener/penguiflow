"""Manual candidate sweep runner for eval workflows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .runner import run_harness_eval


def _mean_score_from_results(path: Path) -> float:
    scores: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        row = json.loads(stripped)
        scores.append(float(row.get("score", 0.0)))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _patch_size(candidate: dict[str, Any]) -> int:
    patches = candidate.get("patches", {})
    if isinstance(patches, dict):
        return len(patches)
    return 0


async def run_manual_sweep(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    run_one: Callable[[dict[str, Any], dict[str, Any] | None], Any],
    metric: Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]],
    candidates: list[dict[str, Any]],
    workload: str | None = None,
) -> dict[str, Any]:
    """Evaluate baseline + candidates and persist winning PatchBundleV1.

    The sweep always executes the baseline first.
    When candidates are empty, baseline-only mode is valid and produces the
    same artifacts with a synthetic baseline winner. This keeps CI and local
    sanity checks consistent without forcing dummy patch candidates.
    """

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline = await run_harness_eval(
        dataset_path=dataset_path,
        output_dir=out_dir,
        run_one=run_one,
        metric=metric,
        mode="baseline",
        pred_name="baseline",
        patch_bundle=None,
    )
    baseline_score = _mean_score_from_results(Path(baseline["results_path"]))

    rankings: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("id", f"candidate-{index + 1}"))
        mode = f"candidate_{index + 1}"
        run = await run_harness_eval(
            dataset_path=dataset_path,
            output_dir=out_dir,
            run_one=run_one,
            metric=metric,
            mode=mode,
            pred_name=candidate_id,
            patch_bundle=candidate,
        )
        mean_score = _mean_score_from_results(Path(run["results_path"]))
        rankings.append(
            {
                "id": candidate_id,
                "mode": mode,
                "score": mean_score,
                "patch_size": _patch_size(candidate),
                "patches": dict(candidate.get("patches", {})),
            }
        )

    rankings.sort(key=lambda item: (-item["score"], item["patch_size"], item["id"]))
    winner = (
        rankings[0]
        if rankings
        else {
            "id": "baseline",
            "mode": "baseline",
            "score": baseline_score,
            "patch_size": 0,
            "patches": {},
        }
    )

    report = {
        "primary_metric": "score",
        "baseline": {"score": baseline_score},
        "candidates": rankings,
        "winner": winner,
    }
    report_path = out_dir / "report.candidates.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    bundle = {
        "schema_version": "PatchBundleV1",
        "patches": dict(winner["patches"]),
        "compat": {
            "planner": "ReactPlanner",
            "tool_catalog_hash": "unknown",
        },
        "provenance": {
            "optimizer": "manual_sweep",
            "dataset": str(dataset_path),
            "metric": "score",
            "score": winner["score"],
            "created_at": datetime.now(UTC).isoformat(),
            "workload": str(workload or "unknown"),
            "winner_id": winner["id"],
        },
    }
    bundle_path = out_dir / "best.patchbundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    return {
        "winner_id": winner["id"],
        "winner_score": winner["score"],
        "baseline_score": baseline_score,
        "report_path": str(report_path),
        "bundle_path": str(bundle_path),
    }


__all__ = ["run_manual_sweep"]
