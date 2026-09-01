from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from examples.planner_enterprise_agent_v2 import learning_control_plane_local_host
from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.learning_control_plane import (
    EnterpriseEvaluationOutput,
    EnterpriseOutcomeScorer,
    EnterprisePolicyComplianceRunner,
    enterprise_policy_metric,
    load_policy_compliance_dataset,
    load_real_held_out_dataset,
)
from examples.planner_enterprise_agent_v2.main import EnterpriseAgentOrchestrator
from learning_control_plane.evaluation import EvaluationCase, EvaluationVariant
from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.mining import TraceLearningRecord
from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory, TrajectoryStep


def test_load_policy_compliance_dataset_keeps_the_held_out_test_split(tmp_path: Path) -> None:
    suite_path = tmp_path / "query_suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "suite_id": "enterprise-policy-v1",
                "queries": [
                    {"query_id": "validation", "text": "A validation request", "split": "val"},
                    {"query_id": "held-out", "text": "A held out request", "split": "test"},
                ],
            }
        )
    )

    dataset = load_policy_compliance_dataset(suite_path)

    assert dataset.version == "enterprise-policy-v1:test"
    assert [case.case_id for case in dataset.cases] == ["held-out"]


@pytest.mark.asyncio
async def test_runner_only_adds_a_temporary_skill_to_the_candidate() -> None:
    received_skills: list[bool] = []

    class FakeAgent:
        async def execute(self, query: str, **_: Any) -> dict[str, str]:
            return {"answer": query}

    def build_agent(skills: Any, provider: Any, callback: Callable[[Trajectory], None]) -> FakeAgent:
        received_skills.append(skills is not None and provider is not None)
        callback(
            Trajectory(
                query="test",
                finish_reason="answer_complete",
                steps=[TrajectoryStep(action=PlannerAction(next_node="triage_query"))],
            )
        )
        return FakeAgent()

    runner = EnterprisePolicyComplianceRunner(AgentConfig.from_env(), agent_factory=build_agent)
    case = EvaluationCase(case_id="pc-003", inputs={"query": "Give team communication guidance"})

    baseline = await runner(case, EvaluationVariant(variant_id="baseline"))
    candidate = await runner(case, EvaluationVariant(variant_id="candidate", advisory_skill="Use the policy."))

    assert received_skills == [False, True]
    assert baseline.trace["steps"][0]["action"]["next_node"] == "triage_query"
    assert candidate.answer == {"answer": "Give team communication guidance"}
    assert baseline.latency_ms >= 0


def test_enterprise_policy_metric_uses_the_existing_trace_based_policy() -> None:
    output = type("Output", (), {"answer": {"text": "Guidance"}, "trace": {"steps": [
        {"action": {"next_node": "triage_query"}},
        {"action": {"next_node": "answer_general"}},
    ]}})()
    case = EvaluationCase(case_id="pc-003", inputs={"query": "Need communication norms"})

    assert enterprise_policy_metric(case, output)["policy_compliance"] == 1.0


def test_enterprise_outcome_scorer_combines_host_outcomes_with_runtime_measurements() -> None:
    class OutcomeProvider:
        def metrics_for(self, case: EvaluationCase, output: EnterpriseEvaluationOutput) -> dict[str, float]:
            assert case.case_id == "real-case"
            assert output.answer == {"text": "Guidance"}
            return {
                "task_success": 1.0,
                "customer_correction_rate": 0.0,
                "human_feedback_score": 0.8,
            }

    output = EnterpriseEvaluationOutput(
        answer={"text": "Guidance"},
        latency_ms=125.0,
        trace={
            "metadata": {"planner_cost_usd": 0.04},
            "steps": [
                {"action": {"next_node": "triage_query"}},
                {"action": {"next_node": "answer_general"}, "error": "tool timeout"},
            ],
        },
    )
    case = EvaluationCase(case_id="real-case", inputs={"query": "Need communication norms"})

    metrics = EnterpriseOutcomeScorer(OutcomeProvider())(case, output)

    assert metrics == {
        "task_success": 1.0,
        "customer_correction_rate": 0.0,
        "human_feedback_score": 0.8,
        "policy_compliance": 1.0,
        "latency_ms": 125.0,
        "tool_error_rate": 0.5,
        "cost_usd": 0.04,
    }


def test_real_held_out_dataset_uses_the_host_loader_and_preserves_digest_lineage() -> None:
    record = TraceLearningRecord(
        trace_id="trace-42",
        context=EvidenceContext(agent_id="planner_enterprise_agent_v2", deployment_digest="sha256:planner"),
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        successful=True,
        pattern_key="triage>answer",
        safe_summary="step_signature=triage>answer",
        investigation_digest="sha256:investigation-42",
    )

    dataset = load_real_held_out_dataset(
        (record,),
        dataset_id="planner-real-heldout",
        version="2026-09-01",
        case_loader=lambda source: EvaluationCase(
            case_id="case-42",
            inputs={"query": "Approved lookup result", "source": source.trace_id},
            expected={"approved_outcome_ref": "outcome-42"},
        ),
    )

    assert dataset.cases[0].inputs["query"] == "Approved lookup result"
    assert dataset.cases[0].source_trace_id == "trace-42"
    assert dataset.cases[0].source_investigation_digest == "sha256:investigation-42"


def test_local_end_to_end_host_keeps_raw_inputs_outside_the_mining_record() -> None:
    record = TraceLearningRecord(
        trace_id="trace-42",
        context=EvidenceContext(agent_id="planner_enterprise_agent_v2", deployment_digest="sha256:planner"),
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        successful=True,
        pattern_key="triage>answer",
        safe_summary="step_signature=triage>answer",
        investigation_digest="sha256:investigation-42",
    )
    output = EnterpriseEvaluationOutput(
        answer={"text": "A plan"},
        latency_ms=100.0,
        trace={"tool_context": {"trace_id": "lcp-eval-case-candidate_123"}, "steps": []},
    )

    case = learning_control_plane_local_host.build_case(record)
    metrics = learning_control_plane_local_host.outcome_provider.metrics_for(case, output)

    assert "raw" not in record.safe_summary
    assert case.inputs["query"] == "Create a concise dependency-aware plan."
    assert metrics["task_success"] == 1.0


def test_investigation_publication_context_uses_the_configured_mlflow_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LCP_INVESTIGATION_PUBLISHING_ENABLED", "true")
    monkeypatch.setenv("LCP_MLFLOW_EXPERIMENT_ID", "experiment-42")
    monkeypatch.setenv("LCP_MLFLOW_TRACKING_STORE_REF", "databricks")
    monkeypatch.setenv("LCP_SCOPE_REF", "tenant:acme")
    orchestrator = object.__new__(EnterpriseAgentOrchestrator)
    orchestrator.config = AgentConfig.from_env()
    orchestrator._nodes = [type("Node", (), {"name": "triage_query"})()]
    trajectory = Trajectory(
        query="raw content stays in the native trajectory",
        tool_context={
            "trace_id": "native-trace-42",
            "lcp_investigation_started_at": "2026-09-01T12:00:00+00:00",
        },
    )

    context = EnterpriseAgentOrchestrator._investigation_context(orchestrator, trajectory)

    assert context.source_trace_ref.experiment_id == "experiment-42"
    assert context.source_trace_ref.tracking_store_ref == "databricks"
    assert context.source_trace_ref.mlflow_trace_id == "native-trace-42"
    assert context.scope_ref == "tenant:acme"
    assert context.allowed_node_names == frozenset({"triage_query"})


def test_disabled_investigation_publication_leaves_the_existing_completion_callback_intact() -> None:
    completed_trace_ids: list[str] = []
    orchestrator = object.__new__(EnterpriseAgentOrchestrator)
    orchestrator._on_trajectory_complete = lambda trajectory: completed_trace_ids.append(trajectory.query)
    orchestrator._investigation_publishing_enabled = False

    EnterpriseAgentOrchestrator._on_trajectory_complete_callback(orchestrator, Trajectory(query="completed"))

    assert completed_trace_ids == ["completed"]
