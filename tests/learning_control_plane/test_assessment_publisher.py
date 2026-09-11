from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from learning_control_plane.assessment_publisher import MlflowAssessmentPublisher
from learning_control_plane.investigation import InvestigationTrajectoryV1, SourceTraceRef
from learning_control_plane.verification import (
    InvestigationVerification,
    SafeStepEvidence,
    VerificationCheck,
    score_final_answer,
)


@dataclass
class _Assessment:
    assessment_id: str


class _FakeMlflow:
    AssessmentSource = None

    def __init__(self) -> None:
        self.feedback: list[dict[str, Any]] = []

    def log_feedback(self, **kwargs: Any) -> _Assessment:
        self.feedback.append(kwargs)
        return _Assessment(assessment_id=f"assessment-{len(self.feedback)}")


class _TraceArrivesAfterOneAttemptMlflow(_FakeMlflow):
    """Simulate MLflow receiving the trace shortly after the callback begins."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def log_feedback(self, **kwargs: Any) -> _Assessment:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("NOT_FOUND: Trace with ID tr-123 not found")
        return super().log_feedback(**kwargs)


def _document() -> InvestigationTrajectoryV1:
    criteria = {
        name: VerificationCheck(name, "passed")
        for name in (
            "factual_numerical_correctness",
            "scope_correctness",
            "evidence_grounding",
            "completeness",
            "interpretation_correctness",
        )
    }
    verification = InvestigationVerification(
        step_evidence=(
            SafeStepEvidence(
                step_index=0,
                node_name="aggregate_report",
                result_checks=(VerificationCheck("tool_execution", "passed"),),
            ),
        ),
        final_answer=score_final_answer(criteria),
    )
    return InvestigationTrajectoryV1(
        investigation_id="investigation-1",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="mlflow-local",
            experiment_id="experiment-1",
            mlflow_trace_id="trace-1",
            deployment_ref="sha256:agent",
        ),
        agent_ref="campaign-performance",
        provider_ref="penguiflow",
        scope_ref="tenant:test",
        started_at=datetime(2026, 9, 3, tzinfo=UTC),
        status="completed",
        execution_fingerprint="sha256:execution",
        request={"has_text": True},
        steps=({"node": "aggregate_report"},),
        redaction_profile="campaign-safe:v2",
        step_signature="aggregate_report",
        assessment_refs=(verification.final_answer.assessment_ref,),
        extensions={"learning.verification": verification.record()},
    )


def test_mlflow_publisher_logs_scores_and_reason_codes_without_answer_content() -> None:
    mlflow = _FakeMlflow()

    assessment_ids = MlflowAssessmentPublisher(mlflow_module=mlflow).publish(_document())

    assert len(assessment_ids) == 7
    assert mlflow.feedback[0]["trace_id"] == "trace-1"
    assert mlflow.feedback[0]["name"] == "lcp_final_answer_accuracy"
    assert all("." not in item["name"] for item in mlflow.feedback)
    assert mlflow.feedback[0]["value"] == 1.0
    assert all("raw_answer" not in item and "expected_facts" not in item for item in mlflow.feedback)


def test_mlflow_publisher_removes_dots_from_dynamic_criterion_names() -> None:
    mlflow = _FakeMlflow()
    document = _document()
    verification = document.extensions["learning.verification"]
    verification["final_answer"]["criteria"][0]["criterion_id"] = "scope.correctness"

    MlflowAssessmentPublisher(mlflow_module=mlflow).publish(document)

    assert "lcp_final_answer_accuracy_scope_correctness" in {
        item["name"] for item in mlflow.feedback
    }


def test_mlflow_publisher_retries_until_the_async_trace_is_available() -> None:
    mlflow = _TraceArrivesAfterOneAttemptMlflow()
    delays: list[float] = []

    assessment_ids = MlflowAssessmentPublisher(
        mlflow_module=mlflow,
        trace_availability_delays=(0.1,),
        sleep_fn=delays.append,
    ).publish(_document())

    assert len(assessment_ids) == 7
    assert delays == [0.1]
