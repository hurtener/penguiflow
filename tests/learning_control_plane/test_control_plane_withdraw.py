"""An approval that was never delivered can be withdrawn, and the approval stays on the record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from learning_control_plane.contracts.evidence import EvidenceContext
from learning_control_plane.control_plane import AdvisorySkillCandidate, LearningControlPlane, PromotionPolicy
from learning_control_plane.control_plane.persistence import SQLiteControlPlaneRepository
from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    LocalEvaluationBackend,
    MetricSpecification,
)


def _plane(tmp_path) -> LearningControlPlane:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(MetricSpecification("quality_score"),),
        minimum_primary_improvement=0.1,
    )
    return LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(),
        repository=SQLiteControlPlaneRepository(tmp_path / "cp.db"),
    )


async def _job(plane: LearningControlPlane, candidate_score: float):
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:agent-v1")
    candidate_id = f"candidate-{candidate_score}"
    plane.register_candidate(
        AdvisorySkillCandidate(
            candidate_id=candidate_id, advisory_skill="Use the steps.", source_trace_ids=("t1", "t2")
        ),
        context,
    )
    dataset = EvaluationDataset(
        dataset_id="heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"q": "one"}, expected="ok"),),
    )
    job = plane.create_job(
        candidate_id=candidate_id, evaluation_id=f"ev-{candidate_score}", context=context, dataset=dataset
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": candidate_score if output == "candidate" else 0.5}

    return await plane.run_job(job.job_id, run_one, score)


async def test_withdrawing_an_override_approval_keeps_the_override_and_records_the_withdrawal(tmp_path) -> None:
    plane = _plane(tmp_path)
    rejected = await _job(plane, 0.55)
    approved = plane.override_gate_rejection(rejected.job_id, reviewer_id="owner", reason="v1 override")

    withdrawn = plane.withdraw_approval(approved.job_id, reviewer_id="owner", reason="superseded by the first draft")

    assert withdrawn.state == "rejected"
    assert withdrawn.review.approved and withdrawn.review.overrode_gate and withdrawn.review.reason == "v1 override"
    assert (
        withdrawn.review.withdrawn_by == "owner"
        and withdrawn.review.withdrawn_reason == "superseded by the first draft"
    )
    reloaded = next(
        job for job in SQLiteControlPlaneRepository(tmp_path / "cp.db").load().jobs if job.job_id == approved.job_id
    )
    assert reloaded.review.withdrawn_at is not None and reloaded.review.overrode_gate
    with pytest.raises(ValueError, match="only an approved job"):
        plane.withdraw_approval(approved.job_id, reviewer_id="owner", reason="again")


async def test_a_delivered_approval_must_be_revoked_not_withdrawn(tmp_path) -> None:
    plane = _plane(tmp_path)
    passed = await _job(plane, 0.9)
    plane.review_job(passed.job_id, reviewer_id="owner", approved=True, reason="good")
    plane.authorize_delivery(passed.job_id, scope_ref="tenant:A", expires_at=datetime.now(UTC) + timedelta(days=1))

    with pytest.raises(ValueError, match="revoke the delivery instead"):
        plane.withdraw_approval(passed.job_id, reviewer_id="owner", reason="changed my mind")


async def test_an_approved_gate_passing_job_can_be_reopened_for_review_but_not_an_override(tmp_path) -> None:
    plane = _plane(tmp_path)
    passed = await _job(plane, 0.9)
    plane.review_job(passed.job_id, reviewer_id="owner", approved=True, reason="recorded on my behalf")

    reopened = plane.reopen_review(passed.job_id, reviewer_id="owner", reason="I will decide on the page")

    assert reopened.state == "ready_for_review" and reopened.review is None
    again = plane.review_job(passed.job_id, reviewer_id="owner", approved=True, reason="now my own decision")
    assert again.state == "approved"

    overridden = plane.override_gate_rejection((await _job(plane, 0.55)).job_id, reviewer_id="owner", reason="v1")
    with pytest.raises(ValueError, match="withdrawn, not reopened"):
        plane.reopen_review(overridden.job_id, reviewer_id="owner", reason="x")
