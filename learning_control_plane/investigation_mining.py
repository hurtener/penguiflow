"""Read verified MLflow investigation attachments into safe mining records."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .evaluation import EvaluationCase
from .evidence import EvidenceContext
from .investigation import INVESTIGATION_TRAJECTORY_SCHEMA_VERSION, InvestigationTrajectoryV1, SourceTraceRef
from .investigation_publisher import (
    INVESTIGATION_ATTACHMENT_OUTPUT_KEY,
    INVESTIGATION_DIGEST_TAG,
    INVESTIGATION_ID_TAG,
)
from .mining import TraceCohorts, TraceLearningRecord, reserve_later_held_out_cohort
from .verification import VERIFICATION_SCHEMA_VERSION


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class InvestigationSelection:
    """Optional discovery filters for completed investigation documents."""

    experiment_id: str
    agent_ref: str | None = None
    provider_ref: str | None = None
    scope_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _non_empty(self.experiment_id, "experiment_id"))
        for field_name in ("agent_ref", "provider_ref", "scope_ref"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _non_empty(value, field_name))


@runtime_checkable
class MlflowTraceAttachmentStore(Protocol):
    """The read-only MLflow operations needed to discover investigation attachments."""

    def search_traces(
        self,
        *,
        locations: Sequence[str],
        filter_string: str,
        return_type: str,
    ) -> Sequence[Any]:
        """Return MLflow trace records that match one discovery filter."""
        ...


@runtime_checkable
class TraceAttachmentDownloader(Protocol):
    """Download one attachment referenced by an MLflow trace."""

    def download(self, trace: Any, attachment_id: str) -> bytes:
        """Return attachment bytes for one trace and attachment identifier."""
        ...


EvaluationCaseBuilder = Callable[[TraceLearningRecord], EvaluationCase]


class MlflowInvestigationReader:
    """Load canonical investigation attachments and project safe mining records."""

    def __init__(
        self,
        *,
        mlflow_module: MlflowTraceAttachmentStore | None = None,
        attachment_downloader: TraceAttachmentDownloader | None = None,
        attachment_reference_parser: Any | None = None,
    ) -> None:
        self._mlflow = mlflow_module
        self._attachment_downloader = attachment_downloader
        self._attachment_reference_parser = attachment_reference_parser

    def load_records(self, selection: InvestigationSelection) -> tuple[TraceLearningRecord, ...]:
        """Load completed, verified documents as safe records ordered by source time."""

        mlflow, downloader, parse_attachment_reference = self._dependencies()
        traces = mlflow.search_traces(
            locations=[selection.experiment_id],
            filter_string='tags.`learning.investigation.status` = "completed"',
            return_type="list",
        )
        records: list[TraceLearningRecord] = []
        for trace in traces:
            tags = _trace_tags(trace)
            if not _matches_selection(tags, selection):
                continue
            attachment_reference = _investigation_attachment_reference(trace)
            attachment_details = parse_attachment_reference(attachment_reference)
            if attachment_details is None:
                raise ValueError("MLflow investigation attachment reference is invalid")

            attachment_id = attachment_details.get("attachment_id")
            if not isinstance(attachment_id, str) or not attachment_id:
                raise ValueError("MLflow investigation attachment has no attachment_id")
            document = _document_from_canonical_bytes(downloader.download(trace, attachment_id))
            _verify_trace_index(tags, document)
            records.append(_safe_learning_record(document))

        return tuple(sorted(records, key=lambda record: (record.recorded_at, record.trace_id)))

    def load_cohorts(
        self,
        selection: InvestigationSelection,
        *,
        held_out_count: int,
    ) -> TraceCohorts:
        """Load records and reserve newer source runs before candidate mining begins."""

        return reserve_later_held_out_cohort(self.load_records(selection), held_out_count=held_out_count)

    def _dependencies(self) -> tuple[MlflowTraceAttachmentStore, TraceAttachmentDownloader, Any]:
        if self._mlflow is None:
            try:
                import mlflow
                from mlflow.tracing.attachments import Attachment
                from mlflow.tracing.client import TracingClient
            except ImportError as error:
                raise RuntimeError("MLflow 3.12.0 or newer with trace attachments is required") from error
            self._mlflow = mlflow
            self._attachment_downloader = _MlflowAttachmentDownloader(TracingClient())
            self._attachment_reference_parser = Attachment.parse_ref

        if self._attachment_downloader is None or self._attachment_reference_parser is None:
            raise RuntimeError(
                "attachment_downloader and attachment_reference_parser are required with an injected mlflow_module"
            )
        return self._mlflow, self._attachment_downloader, self._attachment_reference_parser


class _MlflowAttachmentDownloader:
    """Bridge MLflow's trace artifact repository to the reader's small download contract."""

    def __init__(self, tracing_client: Any) -> None:
        self._tracing_client = tracing_client

    def download(self, trace: Any, attachment_id: str) -> bytes:
        """Download one attachment using the trace's own artifact destination."""

        artifact_repository = self._tracing_client._get_artifact_repo_for_trace(trace.info)
        return artifact_repository.download_trace_attachment(attachment_id)


def _trace_tags(trace: Any) -> dict[str, str]:
    info = getattr(trace, "info", None)
    tags = getattr(info, "tags", None)
    if not isinstance(tags, Mapping):
        raise ValueError("MLflow investigation trace has no tag mapping")
    return {str(key): str(value) for key, value in tags.items()}


def _matches_selection(tags: Mapping[str, str], selection: InvestigationSelection) -> bool:
    expected_tags = {
        "learning.investigation.agent_ref": selection.agent_ref,
        "learning.investigation.provider_ref": selection.provider_ref,
        "learning.investigation.scope_ref": selection.scope_ref,
    }
    return all(value is None or tags.get(name) == value for name, value in expected_tags.items())


def _investigation_attachment_reference(trace: Any) -> str:
    spans = getattr(getattr(trace, "data", None), "spans", None)
    if not isinstance(spans, Sequence):
        raise ValueError("MLflow investigation trace has no spans")
    for span in spans:
        outputs = getattr(span, "outputs", None)
        if not isinstance(outputs, Mapping):
            continue
        attachment_reference = outputs.get(INVESTIGATION_ATTACHMENT_OUTPUT_KEY)
        if isinstance(attachment_reference, str):
            return attachment_reference
    raise ValueError("MLflow investigation trace has no investigation attachment")


def _document_from_canonical_bytes(content_bytes: bytes) -> InvestigationTrajectoryV1:
    try:
        decoded = content_bytes.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("investigation attachment is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("investigation attachment must contain a JSON object")
    if payload.get("schema_version") != INVESTIGATION_TRAJECTORY_SCHEMA_VERSION:
        raise ValueError("investigation attachment has an unsupported schema version")

    document_payload = dict(payload)
    document_payload.pop("schema_version", None)
    source_trace_ref = document_payload.pop("source_trace_ref", None)
    if not isinstance(source_trace_ref, Mapping):
        raise ValueError("investigation attachment has no source_trace_ref")
    for timestamp_name in ("started_at", "completed_at"):
        value = document_payload.get(timestamp_name)
        if value is not None:
            document_payload[timestamp_name] = _parse_timestamp(value, timestamp_name)

    try:
        document = InvestigationTrajectoryV1(
            source_trace_ref=SourceTraceRef(**dict(source_trace_ref)),
            **document_payload,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("investigation attachment does not match InvestigationTrajectoryV1") from error
    if document.canonical_bytes() != content_bytes:
        raise ValueError("investigation attachment bytes are not canonical")
    return document


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"investigation attachment {field_name} must be a timestamp string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"investigation attachment {field_name} is invalid") from error


def _verify_trace_index(tags: Mapping[str, str], document: InvestigationTrajectoryV1) -> None:
    expected_tags = {
        INVESTIGATION_ID_TAG: document.investigation_id,
        INVESTIGATION_DIGEST_TAG: document.digest(),
        **document.query_index(),
    }
    for name, expected_value in expected_tags.items():
        if tags.get(name) != expected_value:
            raise ValueError(f"MLflow investigation tag {name!r} does not match the attachment")


def _safe_learning_record(
    document: InvestigationTrajectoryV1,
) -> TraceLearningRecord:
    execution_context = document.execution_context
    intent_class = _safe_intent_class(document.intent_descriptor)
    verified_success = _verified_success(document)
    safe_evidence = _project_verification_evidence(document)
    safe_summary = (
        f"intent_class={intent_class}; "
        f"step_signature={document.step_signature}; "
        f"step_count={len(document.steps)}; "
        f"failed_step_count={_safe_non_negative_count(execution_context.get('failed_step_count'))}; "
        f"has_text={_safe_boolean(document.request.get('has_text'))}; "
        f"input_part_count={_safe_non_negative_count(document.request.get('input_part_count'))}; "
        f"verified_success={verified_success}"
    )
    return TraceLearningRecord(
        trace_id=document.source_trace_ref.mlflow_trace_id,
        context=EvidenceContext(
            agent_id=document.agent_ref,
            deployment_digest=document.source_trace_ref.deployment_ref,
            trace_id=document.source_trace_ref.mlflow_trace_id,
            scope_ref=document.scope_ref,
        ),
        recorded_at=document.started_at,
        successful=document.status == "completed",
        pattern_key=_pattern_key(intent_class, document.step_signature),
        safe_summary=safe_summary,
        investigation_digest=document.digest(),
        intent_class=intent_class,
        verified_success=verified_success,
        safe_evidence=safe_evidence,
    )


def _project_verification_evidence(document: InvestigationTrajectoryV1) -> dict[str, Any]:
    """Select the redacted verification fields useful for skill drafting."""

    verification = document.extensions.get("learning.verification")
    if not isinstance(verification, Mapping):
        return {}
    if verification.get("schema_version") != VERIFICATION_SCHEMA_VERSION:
        return {}

    step_evidence: list[dict[str, Any]] = []
    raw_steps = verification.get("step_evidence")
    if isinstance(raw_steps, Sequence) and not isinstance(raw_steps, (str, bytes)):
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping):
                continue
            step = _selected_fields(
                raw_step,
                (
                    "step_index",
                    "node_name",
                    "argument_facts",
                    "decision_reason_codes",
                    "verified",
                ),
            )
            raw_checks = raw_step.get("result_checks")
            if isinstance(raw_checks, Sequence) and not isinstance(raw_checks, (str, bytes)):
                step["result_checks"] = [
                    _selected_fields(check, ("check_id", "status", "reason_codes"))
                    for check in raw_checks
                    if isinstance(check, Mapping)
                ]
            step_evidence.append(step)

    evidence: dict[str, Any] = {
        "schema_version": verification["schema_version"],
        "verified_success": verification.get("verified_success") is True,
        "step_evidence": step_evidence,
    }
    final_answer = verification.get("final_answer")
    if isinstance(final_answer, Mapping):
        projected_answer = _selected_fields(
            final_answer,
            (
                "rubric_version",
                "minimum_score",
                "score",
                "passed",
                "hard_failure_codes",
            ),
        )
        criteria = final_answer.get("criteria")
        if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)):
            projected_answer["criteria"] = [
                _selected_fields(
                    criterion,
                    ("criterion_id", "status", "score", "reason_codes"),
                )
                for criterion in criteria
                if isinstance(criterion, Mapping)
            ]
        evidence["final_answer"] = projected_answer

    policy_compliance = verification.get("policy_compliance")
    if isinstance(policy_compliance, Mapping):
        evidence["policy_compliance"] = _selected_fields(
            policy_compliance,
            ("check_id", "status", "reason_codes"),
        )
    return evidence


def _selected_fields(record: Mapping[str, Any], names: Sequence[str]) -> dict[str, Any]:
    """Copy only declared fields from one safe verification record."""

    return {name: record[name] for name in names if name in record}


def _verified_success(document: InvestigationTrajectoryV1) -> bool:
    """Trust only a versioned verification extension with assessment lineage."""

    verification = document.extensions.get("learning.verification")
    if not isinstance(verification, Mapping):
        return False
    return (
        verification.get("schema_version") == VERIFICATION_SCHEMA_VERSION
        and verification.get("verified_success") is True
        and bool(document.assessment_refs)
        and document.execution_context.get("verified_success") is True
    )


def _safe_non_negative_count(value: object) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _safe_boolean(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _safe_intent_class(intent_descriptor: Mapping[str, Any] | None) -> str:
    """Return the optional integration-owned workflow label, or ``unknown``."""

    if intent_descriptor is None:
        return "unknown"
    value = intent_descriptor.get("class")
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return value.strip()


def _pattern_key(intent_class: str, step_signature: str) -> str:
    """Keep legacy signatures stable while separating explicitly labelled workflows."""

    if intent_class == "unknown":
        return step_signature
    return f"{intent_class}:{step_signature}"


def build_held_out_evaluation_cases(
    records: Sequence[TraceLearningRecord],
    build_case: EvaluationCaseBuilder,
) -> tuple[EvaluationCase, ...]:
    """Build held-out cases through a host-owned source-run lookup without exposing raw input to mining."""

    cases: list[EvaluationCase] = []
    for record in records:
        case = build_case(record)
        if case.source_investigation_digest not in (None, record.investigation_digest):
            raise ValueError(f"evaluation case {case.case_id!r} has a different investigation digest")
        if case.source_trace_id not in (None, record.trace_id):
            raise ValueError(f"evaluation case {case.case_id!r} has a different source trace ID")
        cases.append(
            replace(
                case,
                source_trace_id=record.trace_id,
                source_investigation_digest=record.investigation_digest,
            )
        )
    return tuple(cases)


__all__ = [
    "InvestigationSelection",
    "EvaluationCaseBuilder",
    "MlflowInvestigationReader",
    "MlflowTraceAttachmentStore",
    "TraceAttachmentDownloader",
]
