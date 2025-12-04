"""Prompt templates supporting the planner reflection loop."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .react import ReflectionCriteria, Trajectory

if TYPE_CHECKING:  # pragma: no cover - type checking helper
    from .react import ReflectionCritique


def build_critique_system_prompt(criteria: ReflectionCriteria) -> str:
    """Build the system prompt instructing the critique LLM."""

    return f"""You are a quality assessor for AI-generated answers.

Your task is to evaluate whether an answer adequately addresses the user's query.

## Evaluation Criteria

1. **Completeness**: {criteria.completeness}
2. **Accuracy**: {criteria.accuracy}
3. **Clarity**: {criteria.clarity}

## Instructions

- Review the user's original query
- Examine the trajectory of tool calls and observations
- Assess the candidate answer against all criteria
- Assign a score from 0.0 (terrible) to 1.0 (perfect)
- Provide constructive feedback for improvement

## Output Format

Return JSON only:
{{
    "score": <float between 0.0 and 1.0>,
    "passed": <boolean>,
    "feedback": "<brief assessment>",
    "issues": ["<issue 1>", "<issue 2>"],
    "suggestions": ["<suggestion 1>", "<suggestion 2>"]
}}

Be critical but fair. An answer must address ALL parts of the query to pass.
"""


def build_critique_user_prompt(
    query: str,
    candidate_answer: Any,
    trajectory: Trajectory,
) -> str:
    """Build the user prompt containing query, trajectory and answer."""

    trajectory_summary = _summarize_trajectory_for_critique(trajectory)

    return f"""## Original Query
{query}

## Trajectory Summary
{trajectory_summary}

## Candidate Answer
{_format_answer(candidate_answer)}

## Task
Evaluate this candidate answer.
Does it fully address the query based on the information gathered?
"""


def _summarize_trajectory_for_critique(trajectory: Trajectory) -> str:
    """Render a compact textual summary of recent trajectory steps."""

    if not trajectory.steps:
        return "No tool calls were made."

    lines: list[str] = []
    for index, step in enumerate(trajectory.steps[-5:], 1):
        action = step.action
        if action.next_node:
            status = "error" if step.error else "success"
            lines.append(f"{index}. Called {action.next_node} → {status}")
    return "\n".join(lines) if lines else "No recent tool usage recorded."


def _format_answer(answer: Any) -> str:
    """Format the candidate answer for inclusion in a prompt."""

    if isinstance(answer, BaseModel):
        return json.dumps(answer.model_dump(mode="json"), indent=2, ensure_ascii=False)
    if isinstance(answer, dict):
        for key in ("answer", "result", "response", "output"):
            if key in answer:
                return str(answer[key])
        return json.dumps(answer, indent=2, ensure_ascii=False)
    if isinstance(answer, (list, tuple)):
        return json.dumps(answer, indent=2, ensure_ascii=False)
    return str(answer)


def build_revision_prompt(
    original_thought: str,
    critique: ReflectionCritique,
) -> str:
    """Build the prompt requesting a revised answer from the planner LLM."""

    suggestions = critique.suggestions or []
    if suggestions:
        suggestion_lines = "\n".join(f"- {item}" for item in suggestions)
    else:
        suggestion_lines = "- Address the critique feedback above"

    thought_section = f"Previous reasoning: {original_thought}\n\n" if original_thought else ""

    instructions = (
        "Please revise your answer to address these concerns. "
        "Your revised answer must follow the same JSON schema as your "
        "earlier responses."
    )

    return f"""Your previous answer received this feedback:

**Score**: {critique.score:.2f}
**Issues**: {", ".join(critique.issues) if critique.issues else "None noted"}
**Suggestions**:
{suggestion_lines}

{thought_section}{instructions}
"""
