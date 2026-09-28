"""An owner can approve a candidate the gate rejected, but only explicitly, and the override stays visible."""

from __future__ import annotations

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


async def _rejected_job(plane: LearningControlPlane):
    plane.register_candidate(_candidate(), _context())
    job = plane.create_job(
        candidate_id="candidate-skill", evaluation_id="evaluation-1", context=_context(), dataset=_dataset()
    )

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": 0.55 if output == "candidate" else 0.5}  # below the 0.1 bar

    return await plane.run_job(job.job_id, run_one, score)


def _plane(tmp_path=None) -> LearningControlPlane:
    policy = PromotionPolicy(
        policy_version="policy-v1",
        primary_metric="quality_score",
        metric_specifications=(MetricSpecification("quality_score"),),
        minimum_primary_improvement=0.1,
    )
    repository = SQLiteControlPlaneRepository(tmp_path / "cp.db") if tmp_path else None
    return LearningControlPlane(policy=policy, evaluation_backend=LocalEvaluationBackend(), repository=repository)


async def test_an_owner_override_approves_a_rejected_job_and_keeps_the_gate_decision(tmp_path) -> None:
    plane = _plane(tmp_path)
    rejected = await _rejected_job(plane)
    assert rejected.state == "rejected"

    with pytest.raises(ValueError, match="only gate-passing jobs can be reviewed"):
        plane.review_job(rejected.job_id, reviewer_id="owner", approved=True, reason="ship it")

    approved = plane.override_gate_rejection(
        rejected.job_id, reviewer_id="owner", reason="v1: accept the borderline gain"
    )

    assert approved.state == "approved"
    assert approved.review is not None and approved.review.overrode_gate and approved.review.approved
    assert approved.decision == rejected.decision and not approved.decision.approved
    reloaded = SQLiteControlPlaneRepository(tmp_path / "cp.db").load()
    assert next(job for job in reloaded.jobs if job.job_id == rejected.job_id).review.overrode_gate
    with pytest.raises(ValueError, match="only a job the gate rejected"):
        plane.override_gate_rejection(rejected.job_id, reviewer_id="owner", reason="again")
