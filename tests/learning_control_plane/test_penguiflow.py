from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from learning_control_plane.control_plane import AdvisorySkillCandidate, DeliveryAuthorization
from learning_control_plane.evaluation import EvaluationCase, EvaluationVariant
from learning_control_plane.evidence import EvidenceContext, EvidenceEvent
from learning_control_plane.penguiflow import (
    PenguiFlowEvaluationRunner,
    PenguiFlowTracePublisher,
    ScopedSkillActivationAdapter,
    compile_advisory_skill,
    project_trajectory,
)
from penguiflow.planner.trajectory import Trajectory
from penguiflow.skills.local_store import LocalSkillStore


def test_projection_and_trace_publisher_exclude_trajectory_content() -> None:
    trajectory = Trajectory(query="customer secret", final_answer="sensitive answer", finish_reason="answer_complete")
    projection = project_trajectory(trajectory)
    events: list[EvidenceEvent] = []

    class Sink:
        def emit(self, event: EvidenceEvent) -> bool:
            events.append(event)
            return True

    published = PenguiFlowTracePublisher(Sink()).publish(
        trajectory,
        EvidenceContext(agent_id="agent", deployment_digest="sha256:bundle", trace_id="trace-1"),
    )

    assert projection.step_count == 0
    assert projection.has_final_answer
    assert published
    assert events[0].attributes == {
        "step_count": 0,
        "failed_step_count": 0,
        "finish_reason": "answer_complete",
        "has_final_answer": True,
    }


@pytest.mark.asyncio
async def test_evaluation_runner_creates_an_isolated_planner_for_each_case() -> None:
    planners: list[FakePlanner] = []

    def build_planner(variant: EvaluationVariant) -> FakePlanner:
        planner = FakePlanner(variant)
        planners.append(planner)
        return planner

    output = await PenguiFlowEvaluationRunner(build_planner)(
        EvaluationCase(case_id="case-1", inputs={"query": "What changed?", "tool_context": {"tenant_id": "acme"}}),
        EvaluationVariant(variant_id="candidate", advisory_skill="Check evidence."),
    )

    assert output == {"variant": "candidate", "query": "What changed?", "tenant_id": "acme"}
    assert len(planners) == 1


def test_scoped_activation_writes_a_learned_skill_and_returns_a_receipt(tmp_path: Path) -> None:
    candidate = AdvisorySkillCandidate("candidate-1", "Check the verified runbook first.")
    authorization = DeliveryAuthorization(
        authorization_id="auth-1",
        job_id="job-1",
        candidate_id="candidate-1",
        scope_ref="tenant:acme",
        authorized_by="reviewer-1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    skill = compile_advisory_skill(candidate, trigger="Customer asks about deployment status.")
    store = LocalSkillStore(db_path=tmp_path / "skills.db")

    receipt = ScopedSkillActivationAdapter(store).deliver(authorization, candidate, skill)
    records = store.get_by_name(["learned.candidate-1.tenant-acme"], scope_clause="", scope_params=())

    assert receipt.provider_ref.startswith("penguiflow.skills:sk_")
    assert records[0].origin == "learned"
    assert records[0].origin_ref == "auth-1"
    assert records[0].scope_tenant_id == "acme"
    assert records[0].steps == ["Check the verified runbook first."]


def test_scoped_activation_refuses_an_expired_authorization(tmp_path: Path) -> None:
    candidate = AdvisorySkillCandidate("candidate-1", "Check the verified runbook first.")
    authorization = DeliveryAuthorization(
        authorization_id="auth-1",
        job_id="job-1",
        candidate_id="candidate-1",
        scope_ref="global",
        authorized_by="reviewer-1",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    skill = compile_advisory_skill(candidate, trigger="Customer asks about deployment status.")

    with pytest.raises(ValueError, match="expired or revoked"):
        ScopedSkillActivationAdapter(LocalSkillStore(db_path=tmp_path / "skills.db")).deliver(
            authorization,
            candidate,
            skill,
        )


class FakePlanner:
    def __init__(self, variant: EvaluationVariant) -> None:
        self._variant = variant

    async def run(self, query: str, *, tool_context: dict[str, object]) -> dict[str, str | object]:
        return {
            "variant": self._variant.variant_id,
            "query": query,
            "tenant_id": tool_context["tenant_id"],
        }
