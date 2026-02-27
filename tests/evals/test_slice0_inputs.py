from __future__ import annotations

import json

import pytest

from penguiflow.evals.inputs import load_query_suite, load_trace_ids


def test_loaders_validate_splits_and_dedupe_trace_ids(tmp_path) -> None:
    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps(
            {
                "suite_id": "suite-1",
                "workload": "planner_enterprise_agent_v2",
                "queries": [
                    {"query_id": "q1", "text": "alpha", "split": "val"},
                    {"query_id": "q2", "text": "beta", "split": "test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trace_ids_path = tmp_path / "trace_ids.txt"
    trace_ids_path.write_text("trace-a\ntrace-a\n\ntrace-b\n", encoding="utf-8")

    suite = load_query_suite(query_suite_path)
    assert [item["split"] for item in suite["queries"]] == ["val", "test"]

    trace_ids = load_trace_ids(trace_ids_path)
    assert trace_ids == ["trace-a", "trace-b"]

    bad_path = tmp_path / "bad_query_suite.json"
    bad_path.write_text(
        json.dumps(
            {
                "suite_id": "suite-2",
                "workload": "planner_enterprise_agent_v2",
                "queries": [{"query_id": "q3", "text": "gamma", "split": "train"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="split"):
        load_query_suite(bad_path)
