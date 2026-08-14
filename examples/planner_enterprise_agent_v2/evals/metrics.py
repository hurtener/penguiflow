"""Baseline eval metrics for planner_enterprise_agent_v2.

Why: keep metric logic local to the example so the eval flow is fully
self-contained and reproducible from query suite to report.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _expected_route(question: str) -> str:
    text = question.lower()
    if any(token in text for token in ("bug", "error", "crash", "traceback")):
        return "bug"
    if any(token in text for token in ("document", "file", "report", "analyze")):
        return "documents"
    return "general"


def _expected_terminal_tool(route: str) -> str:
    if route == "documents":
        return "analyze_documents"
    if route == "bug":
        return "recommend_fix"
    return "answer_general"


def _tool_sequence(pred_trace: object) -> list[str]:
    if not isinstance(pred_trace, Mapping):
        return []
    steps = pred_trace.get("steps")
    if not isinstance(steps, list):
        return []

    sequence: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        action = step.get("action")
        if not isinstance(action, Mapping):
            continue
        next_node = action.get("next_node")
        if isinstance(next_node, str) and next_node:
            sequence.append(next_node)
    return sequence


def policy_metric(
    gold: object,
    pred: object,
    trace: object | None = None,
    pred_name: str | None = None,
    pred_trace: object | None = None,
) -> dict[str, Any]:
    """Score route-discipline compliance from execution traces.

    Why: a cheap deterministic policy metric gives immediate regression signal
    for this example without requiring additional labels.
    """

    del pred, trace, pred_name
    if not isinstance(gold, Mapping):
        return {"score": 0.0, "feedback": "gold row is not a mapping"}

    question = str(gold.get("question") or "")
    route = _expected_route(question)
    expected_terminal = _expected_terminal_tool(route)
    tools = _tool_sequence(pred_trace)
    if not tools:
        return {"score": 0.0, "feedback": "pred_trace has no tool sequence"}

    checks: list[float] = []
    checks.append(1.0 if tools[0] == "triage_query" else 0.0)
    checks.append(1.0 if expected_terminal in tools else 0.0)

    max_calls = 4 if route == "general" else 8
    checks.append(1.0 if len(tools) <= max_calls else 0.0)

    score = sum(checks) / len(checks)
    feedback = f"route={route}; tools={tools}"
    return {"score": float(score), "feedback": feedback}
