"""Baseline eval metrics for planner_enterprise_agent_v2.

Why: keep metric logic local to the example so the eval flow is fully
self-contained and reproducible from query suite to report.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from penguiflow.evals.api import metric


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


@metric(
    name="Policy Compliance",
    criteria=(
        {"id": "starts_with_triage", "label": "Starts with triage"},
        {"id": "uses_expected_terminal_tool", "label": "Uses expected terminal tool"},
        {"id": "stays_within_tool_budget", "label": "Stays within tool budget"},
    ),
)
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

    checks = {
        "starts_with_triage": tools[0] == "triage_query",
        "uses_expected_terminal_tool": expected_terminal in tools,
    }

    max_calls = 4 if route == "general" else 8
    checks["stays_within_tool_budget"] = len(tools) <= max_calls

    score = sum(1.0 if passed else 0.0 for passed in checks.values()) / len(checks)
    feedback = f"route={route}; tools={tools}"
    return {"score": float(score), "feedback": feedback, "checks": checks}


@metric(
    name="Failure Demo",
    criteria=(
        {"id": "starts_with_triage", "label": "Starts with triage"},
        {"id": "uses_expected_terminal_tool", "label": "Uses expected terminal tool"},
        {"id": "stays_within_demo_budget", "label": "Stays within demo budget"},
    ),
)
def fail_metric_demo(
    gold: object,
    pred: object,
    trace: object | None = None,
    pred_name: str | None = None,
    pred_trace: object | None = None,
) -> dict[str, Any]:
    """Deliberately stricter policy metric to create reviewable failing cases.

    Why: the example needs a deterministic metric that surfaces failed
    criteria in Playground for UX inspection and debugging walkthroughs.
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

    checks = {
        "starts_with_triage": tools[0] == "triage_query",
        "uses_expected_terminal_tool": expected_terminal in tools,
    }

    demo_budget = 2 if route == "general" else 4
    checks["stays_within_demo_budget"] = len(tools) <= demo_budget

    score = sum(1.0 if passed else 0.0 for passed in checks.values()) / len(checks)
    failures: list[str] = []
    if not checks["starts_with_triage"]:
        failures.append("Run did not start with triage.")
    if not checks["uses_expected_terminal_tool"]:
        failures.append(f"Expected terminal tool '{expected_terminal}' was not used.")
    if not checks["stays_within_demo_budget"]:
        failures.append(f"Used {len(tools)} steps; {route} demo budget allows {demo_budget}.")
    feedback = " ".join(failures) if failures else None
    return {"score": float(score), "feedback": feedback, "checks": checks}
