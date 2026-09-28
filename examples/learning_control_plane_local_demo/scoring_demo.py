"""Run a direction-aware local score comparison without an agent framework."""

from __future__ import annotations

import asyncio

from learning_control_plane import (
    AdvisorySkillCandidate,
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    EvidenceContext,
    LearningControlPlane,
    LocalEvaluationBackend,
    MetricSpecification,
    PromotionPolicy,
)


async def main() -> None:
    """Compare one advisory skill on quality, latency, and tool-error rate."""

    dataset = EvaluationDataset(
        dataset_id="planner-heldout",
        version="v1",
        cases=(
            EvaluationCase(case_id="case-1", inputs={"request": "plan a launch"}),
            EvaluationCase(case_id="case-2", inputs={"request": "plan a migration"}),
        ),
    )
    policy = PromotionPolicy(
        policy_version="scoring-demo-v1",
        primary_metric="task_success",
        metric_specifications=(
            MetricSpecification("task_success"),
            MetricSpecification("latency_ms", direction="lower_is_better"),
            MetricSpecification("tool_error_rate", direction="lower_is_better"),
        ),
        minimum_primary_improvement=0.1,
        protected_metrics=("latency_ms", "tool_error_rate"),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    candidate = AdvisorySkillCandidate(
        candidate_id="candidate-planner-tip",
        advisory_skill="Before planning, identify dependencies and verify their order.",
    )
    context = EvidenceContext(agent_id="planner-demo", deployment_digest="sha256:demo")
    plane.register_candidate(candidate, context)
    job = plane.create_job(
        candidate_id=candidate.candidate_id,
        evaluation_id="evaluation-scoring-demo",
        context=context,
        dataset=dataset,
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        if output == "candidate":
            return {"task_success": 1.0, "latency_ms": 80.0, "tool_error_rate": 0.0}
        return {"task_success": 0.5, "latency_ms": 120.0, "tool_error_rate": 0.0}

    completed = await plane.run_job(job.job_id, run_one, score)
    assert completed.decision is not None
    for summary in completed.decision.metric_summaries:
        print(
            {
                "metric": summary.specification.name,
                "direction": summary.specification.direction,
                "baseline_mean": summary.baseline_mean,
                "candidate_mean": summary.candidate_mean,
                "mean_improvement": summary.mean_improvement,
                "paired_values": [
                    {"case_id": value.case_id, "improvement": value.improvement}
                    for value in summary.paired_values
                ],
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
