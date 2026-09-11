from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from learning_control_plane.control_plane import (
    ActivationReceipt,
    AdvisorySkillCandidate,
    LearningControlPlane,
    PromotionPolicy,
)
from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    LocalEvaluationBackend,
)
from learning_control_plane.evidence import EvidenceContext


async def _reviewed_job(*, approved: bool = True) -> tuple[LearningControlPlane, str]:
    dataset = EvaluationDataset(
        dataset_id="heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),),
    )
    plane = LearningControlPlane(
        policy=PromotionPolicy(policy_version="policy-v1", primary_metric="quality"),
        evaluation_backend=LocalEvaluationBackend(),
    )
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:v1")
    plane.register_candidate(AdvisorySkillCandidate("candidate-1", "Use verified steps."), context)
    job = plane.create_job(candidate_id="candidate-1", evaluation_id="eval-1", context=context, dataset=dataset)

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct" if variant.advisory_skill else "incorrect"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality": 1.0 if output == case.expected else 0.0}

    completed = await plane.run_job(job.job_id, run_one, score)
    assert completed.state == "ready_for_review"
    reviewed = plane.review_job(
        job.job_id,
        reviewer_id="reviewer-1",
        approved=approved,
        reason="Held-out improvement verified." if approved else "Human reviewer rejected the candidate.",
    )
    assert reviewed.state == ("approved" if approved else "rejected")
    return plane, job.job_id


@pytest.mark.asyncio
async def test_human_approval_authorizes_one_scope_and_records_matching_receipt() -> None:
    plane, job_id = await _reviewed_job()
    authorization = plane.authorize_delivery(
        job_id,
        scope_ref="tenant:acme",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    receipt = ActivationReceipt(
        receipt_id="receipt-1",
        authorization_id=authorization.authorization_id,
        candidate_id="candidate-1",
        scope_ref="tenant:acme",
        provider_ref="skills-store:asset-1",
        delivered_at=datetime.now(UTC),
    )

    assert plane.record_activation_receipt(receipt) == receipt


@pytest.mark.asyncio
async def test_rejected_review_cannot_authorize_delivery() -> None:
    plane, job_id = await _reviewed_job(approved=False)

    with pytest.raises(ValueError, match="human-approved"):
        plane.authorize_delivery(job_id, scope_ref="tenant:acme", expires_at=datetime.now(UTC) + timedelta(days=1))


@pytest.mark.asyncio
async def test_revoked_authorization_rejects_later_receipt() -> None:
    plane, job_id = await _reviewed_job()
    authorization = plane.authorize_delivery(
        job_id,
        scope_ref="tenant:acme",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    plane.revoke_delivery(authorization.authorization_id, revoked_by="reviewer-1", reason="Regression found.")
    receipt = ActivationReceipt(
        receipt_id="receipt-1",
        authorization_id=authorization.authorization_id,
        candidate_id="candidate-1",
        scope_ref="tenant:acme",
        provider_ref="skills-store:asset-1",
        delivered_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="expired or revoked"):
        plane.record_activation_receipt(receipt)
