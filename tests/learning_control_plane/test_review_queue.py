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


async def _ready_for_review_job() -> tuple[LearningControlPlane, str]:
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle")
    plane = LearningControlPlane(
        policy=PromotionPolicy(policy_version="policy-v1", primary_metric="quality"),
        evaluation_backend=LocalEvaluationBackend(),
    )
    plane.register_candidate(AdvisorySkillCandidate("candidate-1", "Check the authoritative record."), context)
    job = plane.create_job(
        candidate_id="candidate-1",
        evaluation_id="eval-1",
        context=context,
        dataset=EvaluationDataset(
            dataset_id="heldout",
            version="v1",
            cases=(EvaluationCase(case_id="case-1", inputs={"query": "one"}, expected="correct"),),
        ),
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct" if variant.advisory_skill else "incorrect"

    def metric(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality": 1.0 if output == case.expected else 0.0}

    await plane.run_job(job.job_id, run_one, metric)
    return plane, job.job_id


@pytest.mark.asyncio
async def test_review_queue_contains_only_gate_passing_jobs_waiting_for_review() -> None:
    plane, job_id = await _ready_for_review_job()

    queue = plane.list_review_queue()

    assert len(queue) == 1
    assert queue[0].job.job_id == job_id
    assert queue[0].candidate.advisory_skill == "Check the authoritative record."


@pytest.mark.asyncio
async def test_job_audit_record_includes_reviewed_delivery_and_receipt_history() -> None:
    plane, job_id = await _ready_for_review_job()
    plane.review_job(job_id, reviewer_id="reviewer-1", approved=True, reason="Verified held-out improvement.")
    authorization = plane.authorize_delivery(
        job_id,
        scope_ref="tenant:acme",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    plane.record_activation_receipt(
        ActivationReceipt(
            receipt_id="receipt-1",
            authorization_id=authorization.authorization_id,
            candidate_id="candidate-1",
            scope_ref="tenant:acme",
            provider_ref="penguiflow.skills:sk_123",
            delivered_at=datetime.now(UTC),
        )
    )

    audit_record = plane.get_job_audit_record(job_id)

    assert audit_record.job.review is not None
    assert audit_record.candidate.candidate_id == "candidate-1"
    assert audit_record.authorizations == (authorization,)
    assert audit_record.receipts[0].receipt_id == "receipt-1"
