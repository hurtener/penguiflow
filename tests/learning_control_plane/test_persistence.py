from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from learning_control_plane.persistence import SQLiteControlPlaneRepository


@pytest.mark.asyncio
async def test_sqlite_repository_restores_a_reviewed_job_and_delivery_receipt(tmp_path: Path) -> None:
    repository = SQLiteControlPlaneRepository(tmp_path / "learning-control-plane.db")
    policy = PromotionPolicy(policy_version="policy-v1", primary_metric="quality")
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle")
    dataset = EvaluationDataset(
        dataset_id="heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),),
    )
    plane = LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(),
        repository=repository,
    )
    candidate = AdvisorySkillCandidate(
        "candidate-1",
        "Use the verified runbook.",
        source_trace_ids=("trace-1",),
    )
    plane.register_candidate(candidate, context)
    job = plane.create_job(
        candidate_id=candidate.candidate_id,
        evaluation_id="eval-1",
        context=context,
        dataset=dataset,
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct" if variant.advisory_skill else "incorrect"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality": 1.0 if output == case.expected else 0.0}

    await plane.run_job(job.job_id, run_one, score)
    plane.review_job(job.job_id, reviewer_id="reviewer-1", approved=True, reason="Held-out cases improved.")
    authorization = plane.authorize_delivery(
        job.job_id,
        scope_ref="tenant:acme",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    plane.record_activation_receipt(
        ActivationReceipt(
            receipt_id="receipt-1",
            authorization_id=authorization.authorization_id,
            candidate_id=candidate.candidate_id,
            scope_ref="tenant:acme",
            provider_ref="penguiflow.skills:sk_123",
            delivered_at=datetime.now(UTC),
        )
    )

    restarted = LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(),
        repository=repository,
    )
    restored_job = restarted.get_job(job.job_id)

    assert restored_job.state == "approved"
    assert restored_job.decision is not None
    assert restored_job.review is not None
    assert authorization.authorization_id in {item.authorization_id for item in repository.load().authorizations}
    assert {item.receipt_id for item in repository.load().receipts} == {"receipt-1"}


def test_new_sqlite_repository_starts_with_empty_state(tmp_path: Path) -> None:
    repository = SQLiteControlPlaneRepository(tmp_path / "learning-control-plane.db")

    assert repository.load().candidates == ()
    assert repository.load().jobs == ()
