from __future__ import annotations

import pytest

from learning_control_plane.control_plane import AdvisorySkillCandidate, LearningControlPlane, PromotionPolicy
from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    LocalEvaluationBackend,
    MetricSpecification,
)
from learning_control_plane.evidence import EvidenceContext, EvidenceEvent


class _EvidenceRecorder:
    def __init__(self) -> None:
        self.events: list[EvidenceEvent] = []

    def emit(self, event: EvidenceEvent) -> bool:
        self.events.append(event)
        return True


def _dataset() -> EvaluationDataset:
    return EvaluationDataset(
        dataset_id="support-heldout",
        version="v1",
        cases=(
            EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),
            EvaluationCase(case_id="case-2", inputs={"question": "two"}, expected="correct"),
        ),
    )


def _context() -> EvidenceContext:
    return EvidenceContext(agent_id="support-agent", deployment_digest="sha256:agent-v1")


def _candidate() -> AdvisorySkillCandidate:
    return AdvisorySkillCandidate(
        candidate_id="candidate-skill",
        advisory_skill="Use the verified resolution steps.",
        source_trace_ids=("trace-1", "trace-2"),
    )


def test_policy_rejects_a_non_finite_improvement_threshold() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        PromotionPolicy(
            policy_version="policy-v1",
            primary_metric="quality_score",
            minimum_primary_improvement=float("nan"),
        )


@pytest.mark.asyncio
async def test_passing_candidate_stops_at_ready_for_human_review() -> None:
    evidence = _EvidenceRecorder()
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        minimum_primary_improvement=0.1,
        protected_metrics=("safety_score",),
    )
    plane = LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(evidence_sink=evidence),
        evidence_sink=evidence,
    )
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct" if variant.advisory_skill else "incorrect"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {
            "quality_score": 1.0 if output == case.expected else 0.0,
            "safety_score": 1.0,
        }

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "ready_for_review"
    assert completed.decision is not None
    assert completed.decision.approved
    assert completed.decision.policy_version == "policy-v1"
    assert completed.evidence_event_ids
    assert [event.event_type for event in evidence.events] == [
        "candidate.created",
        "evaluation.started",
        "evaluation.completed",
        "gate.decided",
    ]


@pytest.mark.asyncio
async def test_protected_metric_regression_rejects_candidate() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        protected_metrics=("safety_score",),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        if output == "candidate":
            return {"quality_score": 1.0, "safety_score": 0.0}
        return {"quality_score": 0.0, "safety_score": 1.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert not completed.decision.approved
    assert any("protected metric regressed" in reason for reason in completed.decision.reasons)


@pytest.mark.asyncio
async def test_lower_latency_primary_metric_uses_baseline_minus_candidate() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="latency_ms",
        metric_specifications=(MetricSpecification("latency_ms", direction="lower_is_better"),),
        minimum_primary_improvement=30.0,
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"latency_ms": 80.0 if output == "candidate" else 120.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "ready_for_review"
    assert completed.decision is not None
    assert completed.decision.metric_improvements == {"latency_ms": 40.0}
    assert completed.decision.metric_summaries[0].median_improvement == 40.0


@pytest.mark.asyncio
async def test_lower_tool_error_rate_protected_metric_rejects_an_increase() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(MetricSpecification("tool_error_rate", direction="lower_is_better"),),
        protected_metrics=("tool_error_rate",),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        if output == "candidate":
            return {"quality_score": 1.0, "tool_error_rate": 0.2}
        return {"quality_score": 1.0, "tool_error_rate": 0.1}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert completed.decision.metric_improvements["tool_error_rate"] == -0.1
    assert any("protected metric regressed: tool_error_rate" in reason for reason in completed.decision.reasons)


@pytest.mark.asyncio
async def test_candidate_case_failure_fails_closed_even_when_other_case_improves() -> None:
    policy = PromotionPolicy(policy_version="policy-v1", primary_metric="quality_score")
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        if case.case_id == "case-2" and variant.advisory_skill:
            raise RuntimeError("tool unavailable")
        return "correct" if variant.advisory_skill else "incorrect"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": 1.0 if output == case.expected else 0.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert any("too many failed" in reason for reason in completed.decision.reasons)


class _FailingBackend:
    async def evaluate(self, *args: object) -> object:
        raise RuntimeError("evaluation worker unavailable")


@pytest.mark.asyncio
async def test_failed_job_retries_only_within_its_configured_budget() -> None:
    policy = PromotionPolicy(policy_version="policy-v1", primary_metric="quality_score", maximum_attempts=2)
    plane = LearningControlPlane(policy=policy, evaluation_backend=_FailingBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )

    first_attempt = await plane.run_job(job.job_id, lambda case, variant: "unused", lambda case, output: {})
    assert first_attempt.state == "failed"
    assert first_attempt.decision is None

    retried = plane.retry_job(job.job_id)
    assert retried.state == "draft"

    second_attempt = await plane.run_job(job.job_id, lambda case, variant: "unused", lambda case, output: {})
    assert second_attempt.state == "failed"
    assert second_attempt.attempt_count == 2

    with pytest.raises(ValueError, match="exhausted"):
        plane.retry_job(job.job_id)
