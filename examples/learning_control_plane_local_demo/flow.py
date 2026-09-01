"""Run the learning-control-plane MVP locally without external services."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


async def run_demo(db_directory: Path) -> dict[str, str | list[str]]:
    """Execute the full local learning loop and return its durable identifiers."""

    db_directory.mkdir(parents=True, exist_ok=True)
    control_plane_db = db_directory / "control-plane.db"
    skills_db = db_directory / "skills.db"
    if control_plane_db.exists() or skills_db.exists():
        raise FileExistsError(f"demo directory must be empty: {db_directory}")

    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle-v1")
    records = tuple(
        TraceLearningRecord(
            trace_id=f"trace-{index}",
            context=context,
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            successful=True,
            pattern_key="billing-refund",
            safe_summary="Verified the refund status with the billing system.",
            investigation_digest=f"sha256:investigation-{index}",
        )
        for index in range(5)
    )
    cohorts = reserve_later_held_out_cohort(records, held_out_count=2)
    candidate = CandidateMiner(
        minimum_successes=3,
        drafter=lambda pattern: "Verify the refund status with the billing system before responding.",
    ).mine(cohorts.mining_records)[0].candidate

    dataset = EvaluationDataset(
        dataset_id="refund-heldout",
        version="2026-01-01",
        cases=tuple(
            EvaluationCase(
                case_id=record.trace_id,
                inputs={"query": record.safe_summary},
                expected="verified",
                source_trace_id=record.trace_id,
                source_investigation_digest=record.investigation_digest,
            )
            for record in cohorts.held_out_records
        ),
    )
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality",
        minimum_primary_improvement=0.5,
    )
    plane = LearningControlPlane(
        policy=policy,
        evaluation_backend=LocalEvaluationBackend(),
        repository=SQLiteControlPlaneRepository(control_plane_db),
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
    if worker_result.ready_for_review_job_ids != (job.job_id,):
        raise RuntimeError("demo candidate did not pass the offline gate")
    plane.review_job(
        job.job_id,
        reviewer_id="local-reviewer",
        approved=True,
        reason="Held-out quality improved without regressions.",
    )
    authorization = plane.authorize_delivery(
        job.job_id,
        scope_ref="tenant:demo",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    skill = compile_advisory_skill(candidate, trigger="Customer asks about a refund.")
    receipt = ScopedSkillActivationAdapter(LocalSkillStore(db_path=skills_db)).deliver(
        authorization,
        candidate,
        skill,
    )
    plane.record_activation_receipt(receipt)
    audit_record = plane.get_job_audit_record(job.job_id)
    if audit_record.job.decision is None:
        raise RuntimeError("demo job has no gate decision")

    return {
        "candidate_id": candidate.candidate_id,
        "job_id": job.job_id,
        "authorization_id": authorization.authorization_id,
        "receipt_id": receipt.receipt_id,
        "investigation_digests": list(audit_record.job.decision.investigation_digests),
        "control_plane_db": str(control_plane_db),
        "skills_db": str(skills_db),
    }


def main() -> None:
    """Run the demo with an explicitly selected empty local directory."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--db-directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(args.db_directory)), indent=2))


if __name__ == "__main__":
    main()
