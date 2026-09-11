"""Run the governed Planner V2 learning loop from verified MLflow evidence."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.learning_control_plane import (
    EnterpriseOutcomeProvider,
    EnterpriseOutcomeScorer,
    PlannerEnterpriseV2EvaluationRunner,
    load_real_held_out_dataset,
)
from learning_control_plane.control_plane import LearningControlPlane, PromotionPolicy
from learning_control_plane.evaluation import LocalEvaluationBackend, MetricSpecification
from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.investigation_mining import InvestigationSelection, MlflowInvestigationReader
from learning_control_plane.mining import CandidateMiner
from learning_control_plane.penguiflow import ScopedSkillActivationAdapter, compile_advisory_skill
from learning_control_plane.persistence import SQLiteControlPlaneRepository
from learning_control_plane.worker import OfflineEvaluationWorker
from penguiflow.skills.local_store import LocalSkillStore


def load_planner_environment() -> Path | None:
    """Load the Planner V2 environment file before reading its configuration."""

    from dotenv import load_dotenv

    example_environment = Path(__file__).parent / ".env"
    project_environment = Path(__file__).parents[2] / ".env"
    environment_path = example_environment if example_environment.exists() else project_environment
    if not environment_path.exists():
        return None
    load_dotenv(environment_path)
    return environment_path


async def run_learning_loop(args: argparse.Namespace) -> dict[str, Any]:
    """Mine, evaluate, review, and deliver one Planner V2 advisory skill offline."""

    environment_path = load_planner_environment()
    _configure_mlflow_tracking(args.mlflow_tracking_uri)
    host = _load_host_integration(args.host_integration)
    reader = MlflowInvestigationReader()
    cohorts = reader.load_cohorts(
        InvestigationSelection(
            experiment_id=args.experiment_id,
            agent_ref="planner_enterprise_agent_v2",
            scope_ref=args.scope_ref,
        ),
        held_out_count=args.held_out_count,
    )
    mined_candidates = CandidateMiner(
        minimum_successes=args.minimum_successes,
        drafter=host.draft_skill,
    ).mine(cohorts.mining_records)
    if len(mined_candidates) != 1:
        raise RuntimeError(f"expected exactly one eligible candidate, found {len(mined_candidates)}")
    candidate = mined_candidates[0].candidate
    dataset = load_real_held_out_dataset(
        cohorts.held_out_records,
        dataset_id=f"planner-v2-heldout:{args.experiment_id}",
        version=f"mlflow:{args.experiment_id}:{args.held_out_count}",
        case_loader=host.build_case,
    )

    args.db_directory.mkdir(parents=True, exist_ok=True)
    repository = SQLiteControlPlaneRepository(args.db_directory / "control-plane.db")
    context = EvidenceContext(
        agent_id="planner_enterprise_agent_v2",
        deployment_digest=mined_candidates[0].pattern.deployment_digest,
        scope_ref=args.scope_ref,
    )
    plane = LearningControlPlane(
        policy=PromotionPolicy(
            policy_version="planner-v2-real-outcomes-v1",
            primary_metric="task_success",
            metric_specifications=(
                MetricSpecification("task_success"),
                MetricSpecification("policy_compliance"),
                MetricSpecification("latency_ms", direction="lower_is_better"),
                MetricSpecification("tool_error_rate", direction="lower_is_better"),
                MetricSpecification("cost_usd", direction="lower_is_better"),
                MetricSpecification("customer_correction_rate", direction="lower_is_better"),
            ),
            minimum_primary_improvement=args.minimum_task_success_improvement,
            protected_metrics=("policy_compliance", "latency_ms", "tool_error_rate"),
            minimum_complete_cases=len(dataset.cases),
        ),
        evaluation_backend=LocalEvaluationBackend(),
        repository=repository,
    )
    plane.register_candidate(candidate, context)
    job = plane.create_job(
        candidate_id=candidate.candidate_id,
        evaluation_id=f"planner-v2:{args.experiment_id}:heldout",
        context=context,
        dataset=dataset,
    )
    worker_result = await OfflineEvaluationWorker(
        plane,
        run_one=PlannerEnterpriseV2EvaluationRunner(AgentConfig.from_env()),
        metric=EnterpriseOutcomeScorer(host.outcome_provider),
    ).run_pending()
    completed_job = plane.get_job(job.job_id)
    receipt_id: str | None = None
    if completed_job.state == "ready_for_review":
        reviewed_job = plane.review_job(
            job.job_id,
            reviewer_id=args.reviewer_id,
            approved=args.review_decision == "approve",
            reason=args.review_reason,
        )
        if reviewed_job.state == "approved":
            authorization = plane.authorize_delivery(
                job.job_id,
                scope_ref=args.scope_ref,
                expires_at=datetime.now(UTC) + timedelta(days=args.authorization_days),
            )
            skill = compile_advisory_skill(candidate, trigger=args.skill_trigger)
            receipt = ScopedSkillActivationAdapter(
                LocalSkillStore(db_path=args.db_directory / "skills.db")
            ).deliver(authorization, candidate, skill)
            plane.record_activation_receipt(receipt)
            receipt_id = receipt.receipt_id

    audit = plane.get_job_audit_record(job.job_id)
    return {
        "environment_file": str(environment_path) if environment_path else None,
        "candidate_id": candidate.candidate_id,
        "mining_trace_ids": [record.trace_id for record in cohorts.mining_records],
        "held_out_trace_ids": [record.trace_id for record in cohorts.held_out_records],
        "job_id": job.job_id,
        "job_state": audit.job.state,
        "gate": audit.job.decision,
        "review": audit.job.review,
        "receipt_id": receipt_id,
        "worker": {
            "ready_for_review_job_ids": worker_result.ready_for_review_job_ids,
            "rejected_job_ids": worker_result.rejected_job_ids,
            "failed_job_ids": worker_result.failed_job_ids,
        },
        "control_plane_db": str(args.db_directory / "control-plane.db"),
        "skills_db": str(args.db_directory / "skills.db"),
    }


class _HostIntegration:
    """The three host-owned operations that keep real data outside learning code."""

    def __init__(self, module: Any) -> None:
        self.draft_skill = _required_callable(module, "draft_skill")
        self.build_case = _required_callable(module, "build_case")
        outcome_provider = getattr(module, "outcome_provider", None)
        if not isinstance(outcome_provider, EnterpriseOutcomeProvider):
            raise TypeError("host integration must expose outcome_provider.metrics_for(case, output)")
        self.outcome_provider = outcome_provider


def _load_host_integration(reference: str) -> _HostIntegration:
    """Import one explicit host integration module in ``package.module`` form."""

    return _HostIntegration(importlib.import_module(reference))


def _required_callable(module: Any, name: str) -> Any:
    value = getattr(module, name, None)
    if not callable(value):
        raise TypeError(f"host integration must expose callable {name}()")
    return value


def _configure_mlflow_tracking(tracking_uri: str | None) -> None:
    """Apply an explicit tracking URI only when the caller supplied one."""

    if tracking_uri is None:
        return
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)


def parse_args() -> argparse.Namespace:
    """Parse the explicit evidence, review, and delivery inputs for one local run."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--scope-ref", required=True)
    parser.add_argument("--db-directory", type=Path, required=True)
    parser.add_argument("--host-integration", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--review-decision", choices=("approve", "reject"), required=True)
    parser.add_argument("--review-reason", default="Local end-to-end verification.")
    parser.add_argument("--skill-trigger", default="Use when the current request matches the verified pattern.")
    parser.add_argument("--held-out-count", type=int, default=2)
    parser.add_argument("--minimum-successes", type=int, default=2)
    parser.add_argument("--minimum-task-success-improvement", type=float, default=0.0)
    parser.add_argument("--authorization-days", type=int, default=1)
    parser.add_argument("--mlflow-tracking-uri")
    return parser.parse_args()


def main() -> None:
    """Run one explicit offline learning loop and print its durable audit identifiers."""

    print(json.dumps(asyncio.run(run_learning_loop(parse_args())), indent=2, default=str))


if __name__ == "__main__":
    main()
