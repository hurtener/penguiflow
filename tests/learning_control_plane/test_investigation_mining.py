from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from learning_control_plane.evaluation import EvaluationCase
from learning_control_plane.investigation import InvestigationTrajectoryV1, SourceTraceRef
from learning_control_plane.investigation_mining import (
    InvestigationSelection,
    MlflowInvestigationReader,
    build_held_out_evaluation_cases,
)
from learning_control_plane.investigation_publisher import INVESTIGATION_DIGEST_TAG, INVESTIGATION_ID_TAG


def _investigation(index: int) -> InvestigationTrajectoryV1:
    return InvestigationTrajectoryV1(
        investigation_id=f"investigation-{index}",
        source_trace_ref=SourceTraceRef(
            tracking_store_ref="mlflow-local",
            experiment_id="experiment-42",
            mlflow_trace_id=f"source-trace-{index}",
            deployment_ref="sha256:planner-v2",
        ),
        agent_ref="planner_enterprise_agent_v2",
        provider_ref="penguiflow:v1",
        scope_ref="tenant:acme",
        started_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=index),
        status="completed",
        execution_fingerprint="sha256:execution-v1",
        request={"has_text": True, "input_part_count": 1, "raw_query": "must never reach a skill drafter"},
        steps=({"node": "classify", "raw_observation": "must never reach a skill drafter"},),
        redaction_profile="safe:v1",
        step_signature="classify>plan",
        execution_context={"failed_step_count": 0, "unsafe_detail": "must never reach a skill drafter"},
    )


@dataclass
class _FakeTraceInfo:
    tags: dict[str, str]


@dataclass
class _FakeSpan:
    outputs: dict[str, str]


@dataclass
class _FakeTraceData:
    spans: tuple[_FakeSpan, ...]


@dataclass
class _FakeTrace:
    info: _FakeTraceInfo
    data: _FakeTraceData


class _FakeMlflow:
    def __init__(self, traces: list[_FakeTrace]) -> None:
        self.traces = traces
        self.searches: list[tuple[list[str], str]] = []

    def search_traces(
        self,
        *,
        locations: list[str],
        filter_string: str,
        return_type: str,
    ) -> list[_FakeTrace]:
        assert return_type == "list"
        self.searches.append((locations, filter_string))
        return self.traces


class _FakeDownloader:
    def __init__(self, attachments: dict[str, bytes]) -> None:
        self.attachments = attachments
        self.downloaded_ids: list[str] = []

    def download(self, trace: _FakeTrace, attachment_id: str) -> bytes:
        del trace
        self.downloaded_ids.append(attachment_id)
        return self.attachments[attachment_id]


def _stored_trace(document: InvestigationTrajectoryV1, attachment_id: str) -> _FakeTrace:
    return _FakeTrace(
        info=_FakeTraceInfo(
            tags={
                **document.query_index(),
                INVESTIGATION_ID_TAG: document.investigation_id,
                INVESTIGATION_DIGEST_TAG: document.digest(),
            }
        ),
        data=_FakeTraceData(
            spans=(
                _FakeSpan(outputs={"learning.investigation_trajectory": f"attachment://{attachment_id}"}),
            )
        ),
    )


def _reader(documents: tuple[InvestigationTrajectoryV1, ...]) -> tuple[MlflowInvestigationReader, _FakeMlflow]:
    attachments = {f"attachment-{index}": document.canonical_bytes() for index, document in enumerate(documents)}
    traces = [_stored_trace(document, f"attachment-{index}") for index, document in enumerate(documents)]
    mlflow = _FakeMlflow(traces)
    reader = MlflowInvestigationReader(
        mlflow_module=mlflow,
        attachment_downloader=_FakeDownloader(attachments),
        attachment_reference_parser=lambda reference: {"attachment_id": reference.rsplit("/", 1)[1]},
    )
    return reader, mlflow


def test_reader_returns_only_allowlisted_safe_records_in_source_time_order() -> None:
    newer = _investigation(2)
    older = _investigation(1)
    reader, mlflow = _reader((newer, older))

    records = reader.load_records(
        InvestigationSelection(experiment_id="experiment-42", agent_ref="planner_enterprise_agent_v2")
    )

    assert [record.trace_id for record in records] == ["source-trace-1", "source-trace-2"]
    assert records[0].pattern_key == "classify>plan"
    assert records[0].investigation_digest == older.digest()
    assert records[0].safe_summary == (
        "step_signature=classify>plan; step_count=1; failed_step_count=0; has_text=True; input_part_count=1"
    )
    assert "raw_query" not in records[0].safe_summary
    assert "raw_observation" not in records[0].safe_summary
    assert mlflow.searches == [
        (["experiment-42"], 'tags.`learning.investigation.status` = "completed"')
    ]


def test_reader_rejects_an_attachment_with_a_digest_that_does_not_match_its_trace_tag() -> None:
    document = _investigation(1)
    reader, mlflow = _reader((document,))
    mlflow.traces[0].info.tags[INVESTIGATION_DIGEST_TAG] = "sha256:not-the-document"

    with pytest.raises(ValueError, match="does not match"):
        reader.load_records(InvestigationSelection(experiment_id="experiment-42"))


def test_reader_rejects_noncanonical_attachment_bytes() -> None:
    document = _investigation(1)
    reader, _ = _reader((document,))
    downloader = reader._attachment_downloader
    assert isinstance(downloader, _FakeDownloader)
    downloader.attachments["attachment-0"] = document.canonical_bytes().replace(b",", b", ", 1)

    with pytest.raises(ValueError, match="bytes are not canonical"):
        reader.load_records(InvestigationSelection(experiment_id="experiment-42"))


def test_reader_reserves_the_latest_real_records_before_mining() -> None:
    documents = tuple(_investigation(index) for index in range(3))
    reader, _ = _reader(documents)

    cohorts = reader.load_cohorts(InvestigationSelection(experiment_id="experiment-42"), held_out_count=1)

    assert [record.trace_id for record in cohorts.mining_records] == ["source-trace-0", "source-trace-1"]
    assert [record.trace_id for record in cohorts.held_out_records] == ["source-trace-2"]


def test_held_out_cases_are_built_by_the_host_and_keep_investigation_lineage() -> None:
    document = _investigation(1)
    reader, _ = _reader((document, _investigation(2)))
    cohorts = reader.load_cohorts(InvestigationSelection(experiment_id="experiment-42"), held_out_count=1)

    cases = build_held_out_evaluation_cases(
        cohorts.held_out_records,
        lambda record: EvaluationCase(case_id=f"case-{record.trace_id}", inputs={"query": "host-owned real input"}),
    )

    assert cases[0].source_trace_id == "source-trace-2"
    assert cases[0].source_investigation_digest == _investigation(2).digest()


def test_held_out_case_rejects_mismatched_lineage() -> None:
    document = _investigation(1)
    reader, _ = _reader((document, _investigation(2)))
    cohorts = reader.load_cohorts(InvestigationSelection(experiment_id="experiment-42"), held_out_count=1)

    with pytest.raises(ValueError, match="different investigation digest"):
        build_held_out_evaluation_cases(
            cohorts.held_out_records,
            lambda record: EvaluationCase(
                case_id="case-1",
                inputs={"query": "host-owned real input"},
                source_investigation_digest="sha256:wrong",
            ),
        )


def test_real_mlflow_reader_downloads_and_verifies_published_attachments(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    from learning_control_plane.investigation_publisher import MlflowAttachmentPublisher

    previous_tracking_uri = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
        experiment_id = mlflow.create_experiment(
            "lcp-investigation-mining-test",
            artifact_location=(tmp_path / "artifacts").as_uri(),
        )
        for index in range(2):
            document = _investigation(index)
            document = replace(
                document,
                source_trace_ref=replace(document.source_trace_ref, experiment_id=experiment_id),
            )
            MlflowAttachmentPublisher().publish(document)

        cohorts = MlflowInvestigationReader().load_cohorts(
            InvestigationSelection(experiment_id=experiment_id),
            held_out_count=1,
        )

        assert [record.trace_id for record in cohorts.mining_records] == ["source-trace-0"]
        assert [record.trace_id for record in cohorts.held_out_records] == ["source-trace-1"]
    finally:
        mlflow.set_tracking_uri(previous_tracking_uri)
