from __future__ import annotations

import json

import pytest

from learning_control_plane.control_plane import (
    AdvisorySkillCandidate,
    ConfidenceIntervalRequirement,
    LearningControlPlane,
    PromotionPolicy,
)
from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    LocalEvaluationBackend,
    MetricSpecification,
    PairedCaseResult,
    PairedEvaluationResult,
    VariantCaseResult,
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


def test_policy_rejects_a_negative_relative_regression_allowance() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        PromotionPolicy(
            policy_version="policy-v1",
            primary_metric="quality_score",
            maximum_relative_mean_regressions={"latency_ms": -0.1},
        )


def test_gate_event_carries_the_evaluator_safe_evidence_for_mlflow_lineage() -> None:
    recorder = _EvidenceRecorder()
    policy = PromotionPolicy(policy_version="policy-v1", primary_metric="quality_score")
    dataset = EvaluationDataset(
        dataset_id="heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={}),),
    )
    plane = LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(),
        evidence_sink=recorder,
    )
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=dataset,
    )
    tool_contract = {
        "scorer_version": "campaign.aggregate_answer.v5",
        "tool_call_contract": {
            "expected": {"group_by": ["campaign"], "metric_names": ["clicks"]},
            "argument_mismatch_codes": ["missing_requested_metric"],
        },
    }
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=(
            PairedCaseResult(
                case_id="case-1",
                baseline=VariantCaseResult(
                    variant_id="baseline",
                    metrics={"quality_score": 0.0},
                    safe_evidence=tool_contract,
                ),
                candidate=VariantCaseResult(
                    variant_id="candidate-skill",
                    metrics={"quality_score": 1.0},
                    safe_evidence=tool_contract,
                ),
            ),
        ),
    )

    plane.record_evaluation(job.job_id, evaluation)

    gate_event = next(event for event in recorder.events if event.event_type == "gate.decided")
    safe_evidence = gate_event.record()["attributes"]["evaluation_score_evidence"]
    assert safe_evidence == [
        {
            "case_id": "case-1",
            "baseline": tool_contract,
            "candidate": tool_contract,
        }
    ]
    assert "Northstar" not in json.dumps(gate_event.record())


def test_gate_requires_the_accuracy_lower_confidence_bound_to_clear_the_threshold() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        required_source_case_ids=("question-a", "question-b"),
        minimum_complete_pairs_per_source_case=3,
        confidence_interval_requirements=(
            ConfidenceIntervalRequirement(
                metric_name="quality_score",
                statistic="mean_improvement",
                minimum_lower_bound=0.02,
            ),
        ),
        bootstrap_resamples=1_000,
    )
    dataset = EvaluationDataset(
        dataset_id="repeated-heldout",
        version="v1",
        cases=tuple(
            EvaluationCase(
                case_id=f"{source_case_id}-{sample_number}",
                inputs={"source_case_id": source_case_id},
            )
            for source_case_id in ("question-a", "question-b")
            for sample_number in range(3)
        ),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=dataset,
    )
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=tuple(
            PairedCaseResult(
                case_id=case.case_id,
                baseline=VariantCaseResult(variant_id="baseline", metrics={"quality_score": 0.5}),
                candidate=VariantCaseResult(
                    variant_id="candidate-skill",
                    metrics={
                        "quality_score": 0.9
                        if case.inputs["source_case_id"] == "question-a"
                        else 0.4
                    },
                ),
            )
            for case in dataset.cases
        ),
    )

    completed = plane.record_evaluation(job.job_id, evaluation)

    assert completed.state == "rejected"
    assert completed.decision is not None
    interval = completed.decision.confidence_intervals[0]
    assert interval.estimate == pytest.approx(0.15)
    assert interval.lower_bound < 0.02
    assert any("primary benefit was not established" in reason for reason in completed.decision.reasons)


def test_gate_requires_the_cost_regression_upper_confidence_bound_to_stay_within_limit() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(
            MetricSpecification("quality_score"),
            MetricSpecification("cost", direction="lower_is_better"),
        ),
        required_source_case_ids=("question-a", "question-b"),
        minimum_complete_pairs_per_source_case=3,
        confidence_interval_requirements=(
            ConfidenceIntervalRequirement(
                metric_name="cost",
                statistic="relative_mean_regression",
                maximum_upper_bound=0.06,
            ),
        ),
        bootstrap_resamples=1_000,
    )
    dataset = EvaluationDataset(
        dataset_id="repeated-heldout",
        version="v1",
        cases=tuple(
            EvaluationCase(
                case_id=f"{source_case_id}-{sample_number}",
                inputs={"source_case_id": source_case_id},
            )
            for source_case_id in ("question-a", "question-b")
            for sample_number in range(3)
        ),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=dataset,
    )
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=tuple(
            PairedCaseResult(
                case_id=case.case_id,
                baseline=VariantCaseResult(
                    variant_id="baseline",
                    metrics={"quality_score": 0.5, "cost": 100.0},
                ),
                candidate=VariantCaseResult(
                    variant_id="candidate-skill",
                    metrics={
                        "quality_score": 0.8,
                        "cost": 110.0 if case.inputs["source_case_id"] == "question-a" else 100.0,
                    },
                ),
            )
            for case in dataset.cases
        ),
    )

    completed = plane.record_evaluation(job.job_id, evaluation)

    assert completed.state == "rejected"
    assert completed.decision is not None
    interval = completed.decision.confidence_intervals[0]
    assert interval.estimate == pytest.approx(0.05)
    assert interval.upper_bound == pytest.approx(0.10)
    assert interval.required_upper_bound == pytest.approx(0.06)
    assert any("confidence interval upper bound exceeded" in reason for reason in completed.decision.reasons)


def test_gate_requires_repeated_results_for_every_frozen_source_case() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        required_source_case_ids=("question-a", "question-b"),
        minimum_complete_pairs_per_source_case=3,
        confidence_interval_requirements=(
            ConfidenceIntervalRequirement(
                metric_name="quality_score",
                statistic="mean_improvement",
                minimum_lower_bound=0.02,
            ),
        ),
        bootstrap_resamples=1_000,
    )
    dataset = EvaluationDataset(
        dataset_id="incomplete-heldout",
        version="v1",
        cases=tuple(
            EvaluationCase(
                case_id=f"question-a-{sample_number}",
                inputs={"source_case_id": "question-a"},
            )
            for sample_number in range(3)
        ),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=dataset,
    )
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=tuple(
            PairedCaseResult(
                case_id=case.case_id,
                baseline=VariantCaseResult(variant_id="baseline", metrics={"quality_score": 0.5}),
                candidate=VariantCaseResult(variant_id="candidate-skill", metrics={"quality_score": 0.9}),
            )
            for case in dataset.cases
        ),
    )

    completed = plane.record_evaluation(job.job_id, evaluation)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert completed.decision.confidence_intervals == ()
    assert "missing frozen source case: question-b" in completed.decision.reasons


def test_bootstrap_moves_every_repetition_of_a_selected_case_together() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        required_source_case_ids=("question-a", "question-b"),
        minimum_complete_pairs_per_source_case=3,
        confidence_interval_requirements=(
            ConfidenceIntervalRequirement(
                metric_name="quality_score",
                statistic="mean_improvement",
                minimum_lower_bound=-1.0,
            ),
        ),
        bootstrap_resamples=1_000,
    )
    improvements = {
        "question-a": (1.0, -1.0, 0.0),
        "question-b": (-0.5, 0.0, 0.5),
    }
    dataset = EvaluationDataset(
        dataset_id="clustered-heldout",
        version="v1",
        cases=tuple(
            EvaluationCase(
                case_id=f"{source_case_id}-{sample_number}",
                inputs={"source_case_id": source_case_id},
            )
            for source_case_id in improvements
            for sample_number in range(3)
        ),
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=dataset,
    )
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=tuple(
            PairedCaseResult(
                case_id=case.case_id,
                baseline=VariantCaseResult(variant_id="baseline", metrics={"quality_score": 0.0}),
                candidate=VariantCaseResult(
                    variant_id="candidate-skill",
                    metrics={
                        "quality_score": improvements[str(case.inputs["source_case_id"])][
                            int(case.case_id.rsplit("-", 1)[1])
                        ]
                    },
                ),
            )
            for case in dataset.cases
        ),
    )

    completed = plane.record_evaluation(job.job_id, evaluation)

    assert completed.decision is not None
    interval = completed.decision.confidence_intervals[0]
    assert interval.estimate == pytest.approx(0.0)
    assert interval.lower_bound == pytest.approx(0.0)
    assert interval.upper_bound == pytest.approx(0.0)


def test_external_evaluation_is_recorded_and_gated() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        minimum_primary_improvement=0.1,
    )
    plane = LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend())
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill",
        evaluation_id="evaluation-1",
        context=_context(),
        dataset=_dataset(),
    )
    evaluation = PairedEvaluationResult(
        request=job.evaluation_request,
        case_results=tuple(
            PairedCaseResult(
                case_id=case.case_id,
                baseline=VariantCaseResult(variant_id="baseline", metrics={"quality_score": 0.5}),
                candidate=VariantCaseResult(variant_id="candidate-skill", metrics={"quality_score": 0.8}),
            )
            for case in _dataset().cases
        ),
    )

    completed = plane.record_evaluation(job.job_id, evaluation)

    assert completed.state == "ready_for_review"
    assert completed.attempt_count == 1
    assert completed.evaluation == evaluation


@pytest.mark.asyncio
async def test_relative_cost_regression_within_allowance_passes() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(
            MetricSpecification("quality_score"),
            MetricSpecification("cost", direction="lower_is_better"),
        ),
        minimum_primary_improvement=0.1,
        maximum_relative_mean_regressions={"cost": 0.05},
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
            return {"quality_score": 0.8, "cost": 104.0}
        return {"quality_score": 0.5, "cost": 100.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "ready_for_review"


@pytest.mark.asyncio
async def test_relative_latency_regression_beyond_allowance_rejects() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(
            MetricSpecification("quality_score"),
            MetricSpecification("latency_ms", direction="lower_is_better"),
        ),
        minimum_primary_improvement=0.1,
        maximum_relative_mean_regressions={"latency_ms": 0.10},
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
            return {"quality_score": 0.8, "latency_ms": 115.0}
        return {"quality_score": 0.5, "latency_ms": 100.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert any("relative mean regression exceeded: latency_ms" in reason for reason in completed.decision.reasons)


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
async def test_candidate_absolute_threshold_rejects_a_high_scoring_but_failed_answer() -> None:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="final_answer_accuracy",
        metric_specifications=(
            MetricSpecification("final_answer_accuracy"),
            MetricSpecification("task_success"),
        ),
        minimum_primary_improvement=0.1,
        candidate_metric_thresholds={"final_answer_accuracy": 0.85, "task_success": 1.0},
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
            return {"final_answer_accuracy": 0.9, "task_success": 0.5}
        return {"final_answer_accuracy": 0.5, "task_success": 0.0}

    completed = await plane.run_job(job.job_id, run_one, score)

    assert completed.state == "rejected"
    assert completed.decision is not None
    assert any("task_success" in reason and "missed threshold" in reason for reason in completed.decision.reasons)


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
