"""Local-only host integration for inspecting the end-to-end Planner V2 command."""

from __future__ import annotations

from collections.abc import Mapping

from examples.planner_enterprise_agent_v2.learning_control_plane import EnterpriseEvaluationOutput
from learning_control_plane.evaluation import EvaluationCase
from learning_control_plane.mining import TraceLearningRecord, TracePattern


def draft_skill(pattern: TracePattern) -> str:
    """Return a deterministic local draft from the already-safe repeated pattern."""

    return f"For the {pattern.pattern_key} workflow, verify dependencies before planning the final response."


def build_case(record: TraceLearningRecord) -> EvaluationCase:
    """Map a safe local record to a local test input; production replaces this lookup."""

    return EvaluationCase(
        case_id=f"case-{record.trace_id}",
        inputs={"query": "Create a concise dependency-aware plan.", "tenant_id": "local-e2e"},
        expected={"outcome_ref": f"local-{record.trace_id}"},
    )


class LocalOutcomeProvider:
    """Provide deterministic local outcomes so the command can exercise the approval path."""

    def metrics_for(self, case: EvaluationCase, output: EnterpriseEvaluationOutput) -> Mapping[str, float]:
        """Treat the candidate variant as successful only for local end-to-end inspection."""

        del case
        tool_context = output.trace.get("tool_context")
        trace_id = tool_context.get("trace_id") if isinstance(tool_context, Mapping) else ""
        is_candidate = isinstance(trace_id, str) and "candidate_" in trace_id
        return {
            "task_success": 1.0 if is_candidate else 0.0,
            "customer_correction_rate": 0.0,
            "human_feedback_score": 1.0 if is_candidate else 0.0,
        }


outcome_provider = LocalOutcomeProvider()
