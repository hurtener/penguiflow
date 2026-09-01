from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from learning_control_plane.investigation import InvestigationTrajectoryV1, SourceTraceRef
from learning_control_plane.investigation_publisher import MlflowAttachmentPublisher


def _investigation(*, request: dict[str, object] | None = None) -> InvestigationTrajectoryV1:
    return InvestigationTrajectoryV1(
        investigation_id="investigation-1",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="mlflow-test",
            experiment_id="experiment-42",
            mlflow_trace_id="source-trace-42",
            deployment_ref="sha256:agent-v1",
        ),
        agent_ref="planner_enterprise_agent_v2",
        provider_ref="penguiflow:v1",
        scope_ref="tenant:acme",
        started_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        status="completed",
        execution_fingerprint="sha256:execution-v1",
        request=request or {"intent": "document-analysis"},
        steps=({"index": 0, "action": {"target": "triage_query"}},),
        redaction_profile="internal-safe:v1",
        step_signature="triage_query>analyze_documents",
    )


class _FakeAttachment:
    def __init__(self, *, content_type: str, content_bytes: bytes) -> None:
        self.content_type = content_type
        self.content_bytes = content_bytes


class _FakeSpan:
    def __init__(self) -> None:
        self.outputs: dict[str, Any] = {}

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


@dataclass
class _FakeTraceInfo:
    tags: dict[str, str]


@dataclass
class _FakeTrace:
    info: _FakeTraceInfo


class _FakeSpanContext:
    def __init__(self, mlflow: _FakeMlflow) -> None:
        self._mlflow = mlflow
        self.span = _FakeSpan()

    def __enter__(self) -> _FakeSpan:
        self._mlflow.current_context = self
        return self.span

    def __exit__(self, *_: object) -> None:
        assert self._mlflow.current_context is self
        self._mlflow.current_context = None


class _FakeMlflow:
    def __init__(self) -> None:
        self.contexts: list[_FakeSpanContext] = []
        self.current_context: _FakeSpanContext | None = None
        self.traces: list[_FakeTrace] = []
        self.destinations: list[object] = []
        self.flushed = False

    def search_traces(self, *, locations: list[object], filter_string: str, return_type: str) -> list[_FakeTrace]:
        del locations, return_type
        investigation_id = filter_string.split('"')[1]
        return [trace for trace in self.traces if trace.info.tags.get("learning.investigation.id") == investigation_id]

    def start_span(self, *, name: str, span_type: str, trace_destination: object) -> _FakeSpanContext:
        assert name == "learning.investigation.publish"
        assert span_type == "CHAIN"
        self.destinations.append(trace_destination)
        context = _FakeSpanContext(self)
        self.contexts.append(context)
        return context

    def update_current_trace(self, *, tags: dict[str, str]) -> None:
        assert self.current_context is not None
        self.traces.append(_FakeTrace(info=_FakeTraceInfo(tags=tags)))

    def flush_trace_async_logging(self) -> None:
        self.flushed = True


def _publisher(mlflow: _FakeMlflow) -> MlflowAttachmentPublisher:
    return MlflowAttachmentPublisher(
        mlflow_module=mlflow,
        attachment_factory=_FakeAttachment,
        trace_destination_factory=lambda experiment_id: {"experiment_id": experiment_id},
    )


def test_first_publish_writes_one_attachment_and_returns_the_document_digest() -> None:
    mlflow = _FakeMlflow()
    document = _investigation()

    digest = _publisher(mlflow).publish(document)

    attachment = mlflow.contexts[0].span.outputs["learning.investigation_trajectory"]
    assert digest == document.digest()
    assert attachment.content_type == "application/json"
    assert attachment.content_bytes == document.canonical_bytes()
    assert mlflow.traces[0].info.tags["learning.investigation.digest"] == digest
    assert mlflow.destinations == [{"experiment_id": "experiment-42"}]
    assert mlflow.flushed


def test_retry_with_the_same_investigation_returns_without_a_second_attachment() -> None:
    mlflow = _FakeMlflow()
    publisher = _publisher(mlflow)
    document = _investigation()

    first_digest = publisher.publish(document)
    second_digest = publisher.publish(document)

    assert second_digest == first_digest
    assert len(mlflow.contexts) == 1


def test_retry_from_a_new_publisher_uses_the_existing_mlflow_trace() -> None:
    mlflow = _FakeMlflow()
    document = _investigation()

    first_digest = _publisher(mlflow).publish(document)
    second_digest = _publisher(mlflow).publish(document)

    assert second_digest == first_digest
    assert len(mlflow.contexts) == 1


def test_retry_with_different_document_bytes_fails_instead_of_overwriting_evidence() -> None:
    mlflow = _FakeMlflow()
    publisher = _publisher(mlflow)
    publisher.publish(_investigation())

    with pytest.raises(ValueError, match="different digest"):
        publisher.publish(_investigation(request={"intent": "different"}))
    assert len(mlflow.contexts) == 1


def test_real_mlflow_attachment_publisher_writes_an_attachment_reference(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    previous_tracking_uri = mlflow.get_tracking_uri()
    tracking_database = tmp_path / "mlflow.db"
    artifact_directory = tmp_path / "artifacts"

    try:
        mlflow.set_tracking_uri(f"sqlite:///{tracking_database}")
        experiment_id = mlflow.create_experiment("lcp-attachment-test", artifact_location=artifact_directory.as_uri())
        document = _investigation()
        document = InvestigationTrajectoryV1(
            **{
                field: getattr(document, field)
                for field in document.__dataclass_fields__
                if field not in {"source_trace_ref", "schema_version"}
            },
            source_trace_ref=SourceTraceRef(
                tracking_store_ref="mlflow-test",
                experiment_id=experiment_id,
                mlflow_trace_id="source-trace-42",
                deployment_ref="sha256:agent-v1",
            ),
        )

        digest = MlflowAttachmentPublisher().publish(document)
        traces = mlflow.search_traces(
            locations=[experiment_id],
            filter_string='tags.`learning.investigation.id` = "investigation-1"',
            return_type="list",
        )

        assert digest == document.digest()
        assert len(traces) == 1
        assert traces[0].info.tags["learning.investigation.digest"] == digest
        assert traces[0].data.spans[0].outputs["learning.investigation_trajectory"].startswith(
            "mlflow-attachment://"
        )
    finally:
        mlflow.set_tracking_uri(previous_tracking_uri)
