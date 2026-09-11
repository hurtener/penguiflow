from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from learning_control_plane.control_plane import AdvisorySkillCandidate, DeliveryAuthorization
from learning_control_plane.evaluation import EvaluationCase, EvaluationVariant
from learning_control_plane.evidence import EvidenceContext, EvidenceEvent
from learning_control_plane.investigation import SourceTraceRef
from learning_control_plane.penguiflow import (
    PenguiFlowEvaluationRunner,
    PenguiFlowInvestigationContext,
    PenguiFlowInvestigationProjector,
    PenguiFlowInvestigationPublicationHook,
    PenguiFlowTracePublicationHook,
    PenguiFlowTracePublisher,
    ScopedSkillActivationAdapter,
    compile_advisory_skill,
    project_trajectory,
)
from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory, TrajectoryStep
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


def _investigation_context() -> PenguiFlowInvestigationContext:
    return PenguiFlowInvestigationContext(
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="mlflow://local",
            experiment_id="42",
            mlflow_trace_id="tr-source-42",
            deployment_ref="sha256:planner-v2",
            native_trace_id="penguiflow-run-42",
        ),
        agent_ref="planner_enterprise_agent_v2",
        scope_ref="tenant:acme",
        execution_fingerprint="sha256:planner-v2",
        started_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        allowed_node_names=frozenset({"search_docs"}),
        intent_descriptor={"class": "document-analysis"},
    )


def test_investigation_projector_redacts_raw_trajectory_content_before_document_creation() -> None:
    secret = "customer-secret-must-not-leak"
    trajectory = Trajectory(
        query=secret,
        llm_context={"prompt": secret},
        tool_context={"api_key": secret},
        artifacts={"report": secret},
        sources=[{"content": secret}],
        metadata={"private": secret},
        final_answer=secret,
        finish_reason="answer_complete",
        steps=[
            TrajectoryStep(
                action=PlannerAction(next_node="search_docs", args={"query": secret}, thought=secret),
                observation={"content": secret},
                llm_observation=secret,
                streams={"updates": ({"content": secret},)},
            ),
            TrajectoryStep(
                action=PlannerAction(next_node=secret, args={"credential": secret}),
                error=secret,
                failure={"message": secret},
            ),
        ],
    )

    document = PenguiFlowInvestigationProjector(_investigation_context()).project(
        trajectory,
        completed_at=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
    )

    assert document.status == "completed"
    assert document.request == {"has_text": True, "input_part_count": 0}
    assert document.steps == [
        {
            "index": 0,
            "node": "search_docs",
            "status": "completed",
            "has_observation": True,
            "has_streams": True,
        },
        {
            "index": 1,
            "node": "redacted_node",
            "status": "failed",
            "has_observation": False,
            "has_streams": False,
        },
    ]
    assert document.step_signature == "search_docs>redacted_node"
    assert document.intent_descriptor == {"class": "document-analysis"}
    assert secret not in document.canonical_bytes().decode()


@pytest.mark.parametrize(
    ("finish_reason", "expected_status", "expected_termination_reason"),
    [
        ("no_path", "failed", "no_path"),
        ("budget_exhausted", "timed_out", "budget_exhausted"),
        ("paused", "interrupted", "paused"),
        ("cancelled", "cancelled", "cancelled"),
    ],
)
def test_investigation_projector_preserves_safe_terminal_states(
    finish_reason: str,
    expected_status: str,
    expected_termination_reason: str,
) -> None:
    trajectory = Trajectory(query="customer question", finish_reason=finish_reason)

    document = PenguiFlowInvestigationProjector(_investigation_context()).project(trajectory)

    assert document.status == expected_status
    assert document.termination_reason == expected_termination_reason


def test_investigation_hook_only_gives_a_redacted_document_to_the_publisher() -> None:
    secret = "raw-answer-must-not-reach-publisher"
    published = Event()
    documents = []

    class Publisher:
        def publish(self, document: object) -> str:
            documents.append(document)
            published.set()
            return "sha256:test"

    hook = PenguiFlowInvestigationPublicationHook(
        PenguiFlowInvestigationProjector(_investigation_context()),
        Publisher(),
    )
    publication = hook(Trajectory(query=secret, final_answer=secret, finish_reason="answer_complete"))

    assert published.wait(timeout=1)
    assert publication.wait(timeout_s=1)
    assert publication.digest == "sha256:test"
    assert publication.document is not None
    assert secret not in documents[0].canonical_bytes().decode()


def test_investigation_hook_returns_before_a_slow_publisher_finishes() -> None:
    publishing_started = Event()
    allow_publish_to_finish = Event()

    class SlowPublisher:
        def publish(self, document: object) -> str:
            publishing_started.set()
            assert allow_publish_to_finish.wait(timeout=1)
            return "sha256:test"

    hook = PenguiFlowInvestigationPublicationHook(
        PenguiFlowInvestigationProjector(_investigation_context()),
        SlowPublisher(),
    )

    publication = hook(Trajectory(query="customer question", finish_reason="answer_complete"))

    assert publishing_started.wait(timeout=1)
    assert not publication.completed.is_set()
    allow_publish_to_finish.set()
    assert publication.wait(timeout_s=1)


def test_trace_publication_hook_publishes_on_a_background_thread_with_the_tool_trace_id() -> None:
    trajectory = Trajectory(query="customer question", tool_context={"trace_id": "trace-from-tool-context"})
    delivered = Event()
    events: list[EvidenceEvent] = []

    class Sink:
        def emit(self, event: EvidenceEvent) -> bool:
            events.append(event)
            delivered.set()
            return True

    hook = PenguiFlowTracePublicationHook(
        PenguiFlowTracePublisher(Sink()),
        EvidenceContext(agent_id="agent", deployment_digest="sha256:bundle"),
    )
    hook(trajectory)

    assert delivered.wait(timeout=1)
    assert events[0].context.trace_id == "trace-from-tool-context"


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
    candidate = AdvisorySkillCandidate(
        "candidate-1",
        "Check the verified runbook first.",
        optimization_goal="latency",
    )
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
    assert receipt.skill_digest == f"sha256:{records[0].content_hash}"
    assert records[0].origin == "learned"
    assert records[0].origin_ref == "auth-1"
    assert records[0].scope_tenant_id == "acme"
    assert records[0].steps == ["Check the verified runbook first."]
    assert records[0].extra["lcp_optimization_goal"] == "latency"


def test_redelivery_of_unchanged_skill_updates_its_authorization_provenance(tmp_path: Path) -> None:
    candidate = AdvisorySkillCandidate("candidate-1", "Check the verified runbook first.")
    skill = compile_advisory_skill(candidate, trigger="Customer asks about deployment status.")
    store = LocalSkillStore(db_path=tmp_path / "skills.db")
    adapter = ScopedSkillActivationAdapter(store)
    first = DeliveryAuthorization(
        authorization_id="auth-1",
        job_id="job-1",
        candidate_id="candidate-1",
        scope_ref="tenant:acme",
        authorized_by="reviewer-1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    second = replace(first, authorization_id="auth-2")

    adapter.deliver(first, candidate, skill)
    adapter.deliver(second, candidate, skill)
    records = store.get_by_name(["learned.candidate-1.tenant-acme"], scope_clause="", scope_params=())

    assert records[0].origin_ref == "auth-2"


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
