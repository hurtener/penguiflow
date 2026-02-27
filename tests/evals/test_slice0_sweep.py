from __future__ import annotations

import json

import pytest

from penguiflow.evals.sweep import run_manual_sweep


@pytest.mark.asyncio
async def test_run_manual_sweep_selects_best_candidate_and_writes_patchbundle(tmp_path) -> None:
    dataset_path = tmp_path / "view.jsonl"
    dataset_path.write_text(
        "\n".join(
            [
                json.dumps({"example_id": "e1", "split": "val", "answer": "A"}),
                json.dumps({"example_id": "e2", "split": "val", "answer": "B"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    async def run_one(gold: dict[str, object], patch_bundle: dict[str, object] | None = None) -> str:
        prompt = None
        if isinstance(patch_bundle, dict):
            patches = patch_bundle.get("patches", {})
            if isinstance(patches, dict):
                prompt = patches.get("planner.system_prompt_extra")
        if prompt == "good":
            return str(gold["answer"])
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

    candidates = [
        {"id": "c1", "patches": {"planner.system_prompt_extra": "bad"}},
        {"id": "c2", "patches": {"planner.system_prompt_extra": "good"}},
    ]

    result = await run_manual_sweep(
        dataset_path=dataset_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        candidates=candidates,
        workload="examples.planner_enterprise_agent_v2",
    )

    assert result["winner_id"] == "c2"
    bundle = json.loads((tmp_path / "best.patchbundle.json").read_text(encoding="utf-8"))
    assert bundle["schema_version"] == "PatchBundleV1"
    assert bundle["patches"]["planner.system_prompt_extra"] == "good"
    assert bundle["provenance"]["workload"] == "examples.planner_enterprise_agent_v2"
    report = json.loads((tmp_path / "report.candidates.json").read_text(encoding="utf-8"))
    assert report["winner"]["id"] == "c2"
    assert report["candidates"][0]["id"] == "c2"


@pytest.mark.asyncio
async def test_run_manual_sweep_accepts_baseline_only_mode(tmp_path) -> None:
    dataset_path = tmp_path / "view.jsonl"
    dataset_path.write_text(
        json.dumps({"example_id": "e1", "split": "val", "answer": "A"}) + "\n",
        encoding="utf-8",
    )

    async def run_one(gold: dict[str, object], patch_bundle: dict[str, object] | None = None) -> str:
        del patch_bundle
        return str(gold["answer"])

    def metric(
        gold: object,
        pred: object,
        trace: object | None = None,
        pred_name: str | None = None,
        pred_trace: object | None = None,
    ) -> float:
        del trace, pred_name, pred_trace
        return 1.0 if isinstance(gold, dict) and pred == gold.get("answer") else 0.0

    result = await run_manual_sweep(
        dataset_path=dataset_path,
        output_dir=tmp_path,
        run_one=run_one,
        metric=metric,
        candidates=[],
        workload="examples.media_planner",
    )

    assert result["winner_id"] == "baseline"
    assert result["winner_score"] == result["baseline_score"]
    bundle = json.loads((tmp_path / "best.patchbundle.json").read_text(encoding="utf-8"))
    assert bundle["patches"] == {}
    assert bundle["provenance"]["winner_id"] == "baseline"
    report = json.loads((tmp_path / "report.candidates.json").read_text(encoding="utf-8"))
    assert report["candidates"] == []
    assert report["winner"]["id"] == "baseline"
