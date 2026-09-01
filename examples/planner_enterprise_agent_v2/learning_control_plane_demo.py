"""Evaluate one advisory candidate against the enterprise policy test split."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.learning_control_plane import (
    EnterprisePolicyComplianceRunner,
    enterprise_policy_metric,
    load_policy_compliance_dataset,
)
from learning_control_plane.control_plane import AdvisorySkillCandidate, LearningControlPlane, PromotionPolicy
from learning_control_plane.evaluation import LocalEvaluationBackend
from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.persistence import SQLiteControlPlaneRepository
from learning_control_plane.worker import OfflineEvaluationWorker


async def evaluate_candidate(args: argparse.Namespace) -> dict[str, object]:
    """Run one real baseline-versus-candidate gate and return its durable record."""

    args.db_directory.mkdir(parents=True, exist_ok=True)
    database_path = args.db_directory / "control-plane.db"
    if database_path.exists():
        raise FileExistsError(f"database already exists: {database_path}")

    dataset = load_policy_compliance_dataset(args.query_suite, split="test")
    advisory_skill = str(args.advisory_skill).strip()
    candidate_id = f"enterprise-policy-{hashlib.sha256(advisory_skill.encode()).hexdigest()[:12]}"
    context = EvidenceContext(
        agent_id=args.agent_id,
        deployment_digest=args.deployment_digest,
    )
    plane = LearningControlPlane(
        policy=PromotionPolicy(
            policy_version="enterprise-policy-v1",
            primary_metric="policy_compliance",
            minimum_primary_improvement=0.0,
            minimum_complete_cases=len(dataset.cases),
        ),
        evaluation_backend=LocalEvaluationBackend(),
        repository=SQLiteControlPlaneRepository(database_path),
    )
    candidate = AdvisorySkillCandidate(candidate_id=candidate_id, advisory_skill=advisory_skill)
    plane.register_candidate(candidate, context)
    job = plane.create_job(
        candidate_id=candidate.candidate_id,
        evaluation_id=f"{dataset.dataset_id}-held-out",
        context=context,
        dataset=dataset,
    )

    runner = EnterprisePolicyComplianceRunner(AgentConfig.from_env())
    worker_result = await OfflineEvaluationWorker(
        plane,
        run_one=runner,
        metric=enterprise_policy_metric,
    ).run_pending()
    completed_job = plane.get_job(job.job_id)
    audit = plane.get_job_audit_record(job.job_id)
    return {
        "candidate_id": candidate_id,
        "job_id": job.job_id,
        "job_state": completed_job.state,
        "worker": {
            "ready_for_review_job_ids": worker_result.ready_for_review_job_ids,
            "rejected_job_ids": worker_result.rejected_job_ids,
            "failed_job_ids": worker_result.failed_job_ids,
        },
        "gate": audit.job.decision,
        "control_plane_db": str(database_path),
    }


def main() -> None:
    """Run a real offline evaluation without delivering a skill to customers."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--db-directory", type=Path, required=True)
    parser.add_argument(
        "--query-suite",
        type=Path,
        default=Path(__file__).parent / "evals/policy_compliance_v1/query_suite.json",
    )
    parser.add_argument("--advisory-skill", required=True)
    parser.add_argument("--agent-id", default="planner_enterprise_agent_v2")
    parser.add_argument("--deployment-digest", default="sha256:planner-enterprise-agent-v2-local")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate_candidate(args)), indent=2, default=str))


if __name__ == "__main__":
    main()
