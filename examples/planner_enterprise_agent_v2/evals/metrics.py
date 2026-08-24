"""Baseline eval metrics for planner_enterprise_agent_v2.

Why: keep metric logic local to the example so the eval flow is fully
self-contained and reproducible from query suite to report.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from penguiflow.evals.api import metric
from penguiflow.evals.helpers import extract_node_sequence, llm_judge, sequence_match, trajectory_subset_match


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
    tools = extract_node_sequence(pred_trace)
    if not tools:
        return {"score": 0.0, "feedback": "pred_trace has no tool sequence"}

    checks = {
        "starts_with_triage": sequence_match(tools[:1], ["triage_query"], mode="strict"),
        "uses_expected_terminal_tool": trajectory_subset_match(
            pred_trace,
            {"node_sequence": [expected_terminal]},
            mode="subset",
        ),
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
async def fail_metric_demo(
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
    tools = extract_node_sequence(pred_trace)
    if not tools:
        return {"score": 0.0, "feedback": "pred_trace has no tool sequence"}

    checks = {
        "starts_with_triage": sequence_match(tools[:1], ["triage_query"], mode="strict"),
        "uses_expected_terminal_tool": trajectory_subset_match(
            pred_trace,
            {"node_sequence": [expected_terminal]},
            mode="subset",
        ),
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


@metric(
    name="User Satisfaction",
    criteria=(
        {"id": "helpful_outcome", "label": "Helpful outcome"},
    ),
)
async def satisfaction_metric(
    gold: object,
    pred: object,
    trace: object | None = None,
    pred_name: str | None = None,
    pred_trace: object | None = None,
) -> dict[str, Any]:
    """Judge whether the predicted answer looks helpful for the user query.

    Why: this example shows how a qualitative metric can stay tiny by delegating
    prompt packaging and structured result parsing to ``llm_judge``.
    """

    del trace, pred_name, pred_trace
    question = str(gold.get("question") or "") if isinstance(gold, Mapping) else ""
    answer = str(pred or "")
    return await llm_judge(
        prompt=(
            "You are grading answer helpfulness for an enterprise support agent. "
            "Score from 0.0 to 1.0 using this rubric: 1.0 = directly solves or clearly advances the user's request; "
            "0.7 = mostly helpful but missing an important detail or action; "
            "0.4 = somewhat relevant but vague, incomplete, or weakly actionable; "
            "0.0 = off-topic, misleading, or empty. Prefer lower scores when the answer is generic, "
            "does not address the specific request, or lacks actionable guidance. "
            "Return brief feedback explaining the main reason."
        ),
        inputs={"question": question},
        outputs={"answer": answer},
        reference_outputs=None,
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )
