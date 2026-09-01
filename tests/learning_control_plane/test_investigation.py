from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from learning_control_plane.investigation import InvestigationTrajectoryV1, SourceTraceRef


def _investigation(*, request: dict[str, object] | None = None) -> InvestigationTrajectoryV1:
    return InvestigationTrajectoryV1(
        investigation_id="investigation-1",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="mlflow-prod",
            experiment_id="experiment-42",
            mlflow_trace_id="trace-42",
            deployment_ref="sha256:agent-v1",
            native_trace_id="penguiflow-trace-42",
        ),
        agent_ref="planner_enterprise_agent_v2",
        provider_ref="penguiflow:v1",
        scope_ref="tenant:acme",
        started_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 1, 12, 0, 1, tzinfo=UTC),
        status="completed",
        execution_fingerprint="sha256:execution-v1",
        request=request or {"intent": "document-analysis"},
        steps=({"index": 0, "action": {"target": "triage_query"}},),
        redaction_profile="internal-safe:v1",
        step_signature="triage_query>analyze_documents",
        intent_descriptor={"class": "document-analysis"},
        outcome_refs=("assessment:success",),
    )


def test_canonical_bytes_and_digest_ignore_mapping_insertion_order() -> None:
    first = _investigation(request={"a": 1, "b": {"x": 0.5}})
    second = _investigation(request={"b": {"x": 0.5}, "a": 1})

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.digest() == second.digest()
    assert first.canonical_bytes().startswith(b'{"agent_ref":"planner_enterprise_agent_v2"')
    assert b'"request":{"a":1,"b":{"x":0.5}}' in first.canonical_bytes()
    assert b" " not in first.canonical_bytes()


def test_source_trace_ref_is_complete_without_reading_the_native_run() -> None:
    trace_ref = _investigation().source_trace_ref

    assert trace_ref.record() == {
        "tracking_store_ref": "mlflow-prod",
        "experiment_id": "experiment-42",
        "mlflow_trace_id": "trace-42",
        "deployment_ref": "sha256:agent-v1",
        "native_trace_id": "penguiflow-trace-42",
    }


def test_contract_requires_a_source_trace_ref() -> None:
    with pytest.raises(TypeError, match="source_trace_ref"):
        InvestigationTrajectoryV1(
            investigation_id="investigation-1",
            agent_ref="planner_enterprise_agent_v2",
            provider_ref="penguiflow:v1",
            scope_ref="tenant:acme",
            started_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            status="completed",
            execution_fingerprint="sha256:execution-v1",
            request={"intent": "document-analysis"},
            steps=(),
            redaction_profile="internal-safe:v1",
            step_signature="triage_query",
        )


def test_query_index_contains_only_discovery_fields() -> None:
    index = _investigation().query_index()

    assert index == {
        "learning.investigation.schema": "investigation_trajectory.v1",
        "learning.investigation.id": "investigation-1",
        "learning.investigation.agent_ref": "planner_enterprise_agent_v2",
        "learning.investigation.provider_ref": "penguiflow:v1",
        "learning.investigation.scope_ref": "tenant:acme",
        "learning.investigation.status": "completed",
        "learning.investigation.execution_fingerprint": "sha256:execution-v1",
        "learning.investigation.intent_class": "document-analysis",
        "learning.investigation.step_signature": "triage_query>analyze_documents",
        "learning.investigation.has_outcome": "true",
        "learning.investigation.has_assessment": "false",
    }


def test_contract_rejects_non_finite_numbers_before_a_digest_is_created() -> None:
    investigation = _investigation(request={"confidence": float("nan")})

    with pytest.raises(ValueError, match="non-finite"):
        investigation.canonical_bytes()


def test_contract_normalizes_timestamps_to_utc() -> None:
    investigation = replace(
        _investigation(),
        started_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone(timedelta(hours=-4))),
    )

    assert investigation.record()["started_at"] == "2026-09-01T12:00:00.000000Z"
