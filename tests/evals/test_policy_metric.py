from __future__ import annotations

import json

from examples.planner_enterprise_agent_v2.evals.metrics import policy_compliance_metric


def test_policy_metric_scores_full_compliance_from_pred_trace_steps() -> None:
    gold = {
        "question": "Investigate bug in checkout service",
        "gold_trace_features": {
            "expected_route": "bug",
            "required_order": ["triage_query", "init_bug", "collect_logs", "run_diagnostics", "recommend_fix"],
            "allowed_tools": ["triage_query", "init_bug", "collect_logs", "run_diagnostics", "recommend_fix"],
            "max_tool_calls": 8,
        },
    }
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}, "observation": {"route": "bug"}},
            {"action": {"next_node": "init_bug"}},
            {"action": {"next_node": "collect_logs"}},
            {"action": {"next_node": "run_diagnostics"}},
            {"action": {"next_node": "recommend_fix"}},
        ]
    }

    result = policy_compliance_metric(gold, "final answer", pred_trace=pred_trace)

    assert isinstance(result, dict)
    assert result["score"] == 1.0
    feedback = json.loads(str(result["feedback"]))
    assert feedback["checks"]["route_policy_pass"] is True
    assert feedback["checks"]["workflow_order_pass"] is True


def test_policy_metric_ignores_legacy_gold_policy_field() -> None:
    gold = {
        "question": "Investigate bug in checkout service",
        "gold_policy": {
            "expected_route": "documents",
            "required_order": ["triage_query", "analyze_documents"],
            "allowed_tools": ["triage_query", "analyze_documents"],
            "max_tool_calls": 4,
        },
    }
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}, "observation": {"route": "bug"}},
            {"action": {"next_node": "init_bug"}},
            {"action": {"next_node": "collect_logs"}},
        ]
    }

    result = policy_compliance_metric(gold, "ok", pred_trace=pred_trace)

    assert isinstance(result, dict)
    assert isinstance(result["score"], (int, float))
    feedback = json.loads(str(result["feedback"]))
    assert feedback["checks"]["route_policy_pass"] is True


def test_policy_metric_penalizes_route_and_tool_policy_violations() -> None:
    gold = {
        "question": "Summarize architecture documents",
        "gold_trace_features": {
            "expected_route": "documents",
            "required_order": ["triage_query", "analyze_documents"],
            "allowed_tools": ["triage_query", "analyze_documents"],
            "max_tool_calls": 4,
        },
    }
    pred_trace = {
        "steps": [
            {"action": {"next_node": "answer_general"}},
            {"action": {"next_node": "triage_query"}, "observation": {"route": "general"}},
        ]
    }

    result = policy_compliance_metric(gold, "", pred_trace=pred_trace)

    assert isinstance(result, dict)
    assert isinstance(result["score"], (int, float))
    assert result["score"] < 0.5
    feedback = json.loads(str(result["feedback"]))
    assert feedback["checks"]["triage_first"] is False
    assert feedback["checks"]["route_policy_pass"] is False
    assert feedback["checks"]["allowed_tools_only"] is False


def test_policy_metric_can_derive_expectations_from_gold_trace() -> None:
    gold = {
        "question": "Investigate bug in checkout service",
        "gold_trace": {
            "query": "Investigate bug in checkout service",
            "events": {
                "planner_events": [
                    {"event_type": "tool_call_start", "extra": {"tool_name": "triage_query"}},
                    {
                        "event_type": "tool_call_result",
                        "extra": {"tool_name": "triage_query", "result_json": '{"route":"bug"}'},
                    },
                    {"event_type": "tool_call_start", "extra": {"tool_name": "init_bug"}},
                    {"event_type": "tool_call_start", "extra": {"tool_name": "collect_logs"}},
                ]
            },
        },
    }
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}, "observation": {"route": "bug"}},
            {"action": {"next_node": "init_bug"}},
            {"action": {"next_node": "collect_logs"}},
        ]
    }

    result = policy_compliance_metric(gold, "final answer", pred_trace=pred_trace)

    assert isinstance(result, dict)
    assert result["score"] == 1.0
