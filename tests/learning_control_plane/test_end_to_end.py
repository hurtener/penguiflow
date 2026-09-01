from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from learning_control_plane.control_plane import LearningControlPlane, PromotionPolicy
from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationVariant,
    LocalEvaluationBackend,
)
from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.mining import (
    CandidateMiner,
    TraceLearningRecord,
    reserve_later_held_out_cohort,
)
from learning_control_plane.penguiflow import ScopedSkillActivationAdapter, compile_advisory_skill
from learning_control_plane.persistence import SQLiteControlPlaneRepository
from learning_control_plane.worker import OfflineEvaluationWorker
from penguiflow.skills.local_store import LocalSkillStore


@pytest.mark.asyncio
async def test_local_end_to_end_loop_mines_evaluates_reviews_and_delivers_a_skill(tmp_path: Path) -> None:
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle-v1")
    records = tuple(
        TraceLearningRecord(
            trace_id=f"trace-{index}",
            context=context,
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            successful=True,
            pattern_key="billing-refund",
            safe_summary="Verified the refund status with the billing system.",
        )
        for index in range(5)
    )
    cohorts = reserve_later_held_out_cohort(records, held_out_count=2)
    mined = CandidateMiner(
        minimum_successes=3,
        drafter=lambda pattern: "Verify the refund status with the billing system before responding.",
    ).mine(cohorts.mining_records)
    assert len(mined) == 1
    candidate = mined[0].candidate

    dataset = EvaluationDataset(
        dataset_id="refund-heldout",
        version="2026-01-01",
        cases=tuple(
            EvaluationCase(
                case_id=record.trace_id,
                inputs={"query": record.safe_summary},
                expected="verified",
                source_trace_id=record.trace_id,
            )
            for record in cohorts.held_out_records
        ),
    )
    plane = LearningControlPlane(
        policy=PromotionPolicy(
            policy_version="policy-v1",
            primary_metric="quality",
            minimum_primary_improvement=0.5,
        ),
        evaluation_backend=LocalEvaluationBackend(),
        repository=SQLiteControlPlaneRepository(tmp_path / "control-plane.db"),
    )
    plane.register_candidate(candidate, context)
    job = plane.create_job(
        candidate_id=candidate.candidate_id,
        evaluation_id="refund-eval-1",
        context=context,
        dataset=dataset,
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "verified" if variant.advisory_skill else "unverified"

    def metric(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality": 1.0 if output == case.expected else 0.0}

    worker_result = await OfflineEvaluationWorker(plane, run_one=run_one, metric=metric).run_pending()
    assert worker_result.ready_for_review_job_ids == (job.job_id,)

    plane.review_job(
        job.job_id,
        reviewer_id="reviewer-1",
        approved=True,
        reason="Held-out quality improved without regressions.",
    )
    authorization = plane.authorize_delivery(
        job.job_id,
        scope_ref="tenant:acme",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    skill = compile_advisory_skill(candidate, trigger="Customer asks about a refund.")
    receipt = ScopedSkillActivationAdapter(LocalSkillStore(db_path=tmp_path / "skills.db")).deliver(
        authorization,
        candidate,
        skill,
    )
    plane.record_activation_receipt(receipt)

    restarted = LearningControlPlane(
        policy=PromotionPolicy(
            policy_version="policy-v1",
            primary_metric="quality",
            minimum_primary_improvement=0.5,
        ),
        evaluation_backend=LocalEvaluationBackend(),
        repository=SQLiteControlPlaneRepository(tmp_path / "control-plane.db"),
    )
    assert restarted.get_job(job.job_id).state == "approved"
    assert len(restarted.list_jobs(state="approved")) == 1
