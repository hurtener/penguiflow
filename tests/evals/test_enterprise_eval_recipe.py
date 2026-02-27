from __future__ import annotations

import json
from pathlib import Path


def test_enterprise_dataset_recipe_files_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    dataset_dir = root / "examples/planner_enterprise_agent_v2/datasets/eval_v1"

    assert (dataset_dir / "query_suite.json").exists()
    assert (dataset_dir / "candidates.json").exists()
    assert (dataset_dir / "eval.spec.json").exists()


def test_enterprise_spec_points_to_metric_and_dataset_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "examples/planner_enterprise_agent_v2/datasets/eval_v1/eval.spec.json"
    payload = json.loads(spec_path.read_text(encoding="utf-8"))

    assert payload["metric_spec"] == "examples.planner_enterprise_agent_v2.evals.metrics:policy_metric"
    assert payload["dataset_tag"].startswith("dataset:")
    assert payload["query_suite_path"] == "query_suite.json"
    assert payload["candidates_path"] == "candidates.json"
    assert payload["agent_package"] == "planner_enterprise_agent_v2"
    assert payload["env_files"] == ["../../.env-values"]
    resolved_output = (spec_path.parent / payload["output_dir"]).resolve()
    expected_prefix = (root / "examples/planner_enterprise_agent_v2/artifacts").resolve()
    assert str(resolved_output).startswith(str(expected_prefix))
