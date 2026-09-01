from __future__ import annotations

import pytest

from learning_control_plane.control_plane import (
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
from learning_control_plane.worker import OfflineEvaluationWorker


def _draft_job(plane: LearningControlPlane, candidate_id: str) -> str:
    context = EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle")
    candidate = AdvisorySkillCandidate(candidate_id, "Use the verified runbook.")
    plane.register_candidate(candidate, context)
    dataset = EvaluationDataset(
        dataset_id=f"heldout-{candidate_id}",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),),
    )
    return plane.create_job(
        candidate_id=candidate_id,
        evaluation_id=f"eval-{candidate_id}",
        context=context,
        dataset=dataset,
    ).job_id


@pytest.mark.asyncio
async def test_worker_runs_only_a_bounded_batch_of_draft_jobs() -> None:
    plane = LearningControlPlane(
        policy=PromotionPolicy(policy_version="policy-v1", primary_metric="quality"),
        evaluation_backend=LocalEvaluationBackend(),
    )
    first_job_id = _draft_job(plane, "candidate-1")
    second_job_id = _draft_job(plane, "candidate-2")

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct" if variant.advisory_skill else "incorrect"

    def metric(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality": 1.0 if output == case.expected else 0.0}

    result = await OfflineEvaluationWorker(plane, run_one=run_one, metric=metric).run_pending(max_jobs=1)

    assert len(result.attempted_job_ids) == 1
    assert result.ready_for_review_job_ids == result.attempted_job_ids
    assert {plane.get_job(first_job_id).state, plane.get_job(second_job_id).state} == {"draft", "ready_for_review"}


@pytest.mark.asyncio
async def test_worker_records_a_failed_job_when_its_evaluation_backend_fails() -> None:
    class FailingBackend:
        async def evaluate(self, *args: object) -> object:
            raise RuntimeError("worker backend unavailable")

    plane = LearningControlPlane(
        policy=PromotionPolicy(policy_version="policy-v1", primary_metric="quality"),
        evaluation_backend=FailingBackend(),
    )
    job_id = _draft_job(plane, "candidate-1")

    result = await OfflineEvaluationWorker(
        plane,
        run_one=lambda case, variant: "unused",
        metric=lambda case, output: {},
    ).run_pending()

    assert result.failed_job_ids == (job_id,)
    assert plane.get_job(job_id).state == "failed"


@pytest.mark.asyncio
async def test_worker_rejects_an_invalid_batch_limit() -> None:
    plane = LearningControlPlane(
        policy=PromotionPolicy(policy_version="policy-v1", primary_metric="quality"),
        evaluation_backend=LocalEvaluationBackend(),
    )

    with pytest.raises(ValueError, match="at least 1"):
        await OfflineEvaluationWorker(
            plane,
            run_one=lambda case, variant: "unused",
            metric=lambda case, output: {},
        ).run_pending(max_jobs=0)
