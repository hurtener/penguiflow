"""Local durable storage for learning-control-plane workflow state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .control_plane import (
    ActivationReceipt,
    AdvisorySkillCandidate,
    DeliveryAuthorization,
    GateDecision,
    LearningJob,
    ReviewDecision,
)
from .evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRequest,
    EvaluationVariant,
    MetricSpecification,
    MetricSummary,
    PairedCaseResult,
    PairedEvaluationResult,
    PairedMetricValue,
    VariantCaseResult,
)
from .evidence import EvidenceContext


@dataclass(frozen=True, slots=True)
class PersistedControlPlaneState:
    """The complete workflow state restored when a local LCP process starts."""

    candidates: tuple[AdvisorySkillCandidate, ...] = ()
    jobs: tuple[LearningJob, ...] = ()
    authorizations: tuple[DeliveryAuthorization, ...] = ()
    receipts: tuple[ActivationReceipt, ...] = ()


class SQLiteControlPlaneRepository:
    """Persist one local control-plane workflow snapshot in SQLite.

    This deliberately stores JSON rather than Python objects, so a restart is
    safe to inspect and does not deserialize executable data.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    def load(self) -> PersistedControlPlaneState:
        """Load the latest durable state, or an empty state for a new database."""

        self._ensure_schema()
        with sqlite3.connect(self._db_path) as connection:
            row = connection.execute("SELECT payload FROM lcp_state WHERE id = 1").fetchone()
        if row is None:
            return PersistedControlPlaneState()
        return _state_from_payload(json.loads(str(row[0])))

    def save(self, state: PersistedControlPlaneState) -> None:
        """Atomically replace the local workflow snapshot with the supplied state."""

        self._ensure_schema()
        payload = json.dumps(_state_payload(state), ensure_ascii=False, sort_keys=True, default=str)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                INSERT INTO lcp_state (id, payload) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (payload,),
            )

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lcp_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL
                )
                """
            )


def _state_payload(state: PersistedControlPlaneState) -> dict[str, object]:
    return {
        "candidates": [_candidate_payload(candidate) for candidate in state.candidates],
        "jobs": [_job_payload(job) for job in state.jobs],
        "authorizations": [_authorization_payload(authorization) for authorization in state.authorizations],
        "receipts": [_receipt_payload(receipt) for receipt in state.receipts],
    }


def _state_from_payload(payload: Mapping[str, Any]) -> PersistedControlPlaneState:
    return PersistedControlPlaneState(
        candidates=tuple(_candidate_from_payload(item) for item in payload.get("candidates", [])),
        jobs=tuple(_job_from_payload(item) for item in payload.get("jobs", [])),
        authorizations=tuple(_authorization_from_payload(item) for item in payload.get("authorizations", [])),
        receipts=tuple(_receipt_from_payload(item) for item in payload.get("receipts", [])),
    )


def _candidate_payload(candidate: AdvisorySkillCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "advisory_skill": candidate.advisory_skill,
        "source_trace_ids": list(candidate.source_trace_ids),
        "source_investigation_digests": list(candidate.source_investigation_digests),
    }


def _candidate_from_payload(payload: Mapping[str, Any]) -> AdvisorySkillCandidate:
    return AdvisorySkillCandidate(**payload)


def _job_payload(job: LearningJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "candidate_id": job.candidate_id,
        "evaluation_request": _request_payload(job.evaluation_request),
        "state": job.state,
        "attempt_count": job.attempt_count,
        "evaluation": _evaluation_payload(job.evaluation) if job.evaluation else None,
        "decision": _decision_payload(job.decision) if job.decision else None,
        "review": _review_payload(job.review) if job.review else None,
        "error": job.error,
        "evidence_event_ids": list(job.evidence_event_ids),
    }


def _job_from_payload(payload: Mapping[str, Any]) -> LearningJob:
    evaluation_payload = payload.get("evaluation")
    decision_payload = payload.get("decision")
    review_payload = payload.get("review")
    return LearningJob(
        job_id=str(payload["job_id"]),
        candidate_id=str(payload["candidate_id"]),
        evaluation_request=_request_from_payload(payload["evaluation_request"]),
        state=payload["state"],
        attempt_count=int(payload["attempt_count"]),
        evaluation=_evaluation_from_payload(evaluation_payload) if evaluation_payload else None,
        decision=_decision_from_payload(decision_payload) if decision_payload else None,
        review=_review_from_payload(review_payload) if review_payload else None,
        error=payload.get("error"),
        evidence_event_ids=tuple(payload.get("evidence_event_ids", [])),
    )


def _request_payload(request: EvaluationRequest) -> dict[str, object]:
    return {
        "evaluation_id": request.evaluation_id,
        "evidence_context": {
            "agent_id": request.evidence_context.agent_id,
            "deployment_digest": request.evidence_context.deployment_digest,
            "trace_id": request.evidence_context.trace_id,
            "evaluation_id": request.evidence_context.evaluation_id,
            "candidate_id": request.evidence_context.candidate_id,
            "dataset_version": request.evidence_context.dataset_version,
            "metric_version": request.evidence_context.metric_version,
            "policy_version": request.evidence_context.policy_version,
            "scope_ref": request.evidence_context.scope_ref,
        },
        "dataset": {
            "dataset_id": request.dataset.dataset_id,
            "version": request.dataset.version,
            "cases": [
                {
                    "case_id": case.case_id,
                    "inputs": dict(case.inputs),
                    "expected": case.expected,
                    "source_trace_id": case.source_trace_id,
                    "source_investigation_digest": case.source_investigation_digest,
                }
                for case in request.dataset.cases
            ],
        },
        "baseline": {"variant_id": request.baseline.variant_id, "advisory_skill": request.baseline.advisory_skill},
        "candidate": {"variant_id": request.candidate.variant_id, "advisory_skill": request.candidate.advisory_skill},
    }


def _request_from_payload(payload: Any) -> EvaluationRequest:
    request = _mapping(payload, "evaluation request")
    dataset_payload = _mapping(request["dataset"], "dataset")
    cases = tuple(EvaluationCase(**_mapping(case, "evaluation case")) for case in dataset_payload["cases"])
    return EvaluationRequest(
        evaluation_id=str(request["evaluation_id"]),
        evidence_context=EvidenceContext(**_mapping(request["evidence_context"], "evidence context")),
        dataset=EvaluationDataset(
            dataset_id=str(dataset_payload["dataset_id"]),
            version=str(dataset_payload["version"]),
            cases=cases,
        ),
        baseline=EvaluationVariant(**_mapping(request["baseline"], "baseline variant")),
        candidate=EvaluationVariant(**_mapping(request["candidate"], "candidate variant")),
    )


def _evaluation_payload(evaluation: PairedEvaluationResult) -> dict[str, object]:
    return {
        "request": _request_payload(evaluation.request),
        "case_results": [
            {
                "case_id": pair.case_id,
                "baseline": _variant_result_payload(pair.baseline),
                "candidate": _variant_result_payload(pair.candidate),
            }
            for pair in evaluation.case_results
        ],
    }


def _evaluation_from_payload(payload: Any) -> PairedEvaluationResult:
    evaluation = _mapping(payload, "evaluation")
    pairs = tuple(
        PairedCaseResult(
            case_id=str(pair["case_id"]),
            baseline=_variant_result_from_payload(pair["baseline"]),
            candidate=_variant_result_from_payload(pair["candidate"]),
        )
        for pair in evaluation["case_results"]
    )
    return PairedEvaluationResult(request=_request_from_payload(evaluation["request"]), case_results=pairs)


def _variant_result_payload(result: VariantCaseResult) -> dict[str, object]:
    return {
        "variant_id": result.variant_id,
        "output": result.output,
        "metrics": dict(result.metrics),
        "error": result.error,
    }


def _variant_result_from_payload(payload: Any) -> VariantCaseResult:
    return VariantCaseResult(**_mapping(payload, "variant result"))


def _decision_payload(decision: GateDecision) -> dict[str, object]:
    return {
        "approved": decision.approved,
        "policy_version": decision.policy_version,
        "reasons": list(decision.reasons),
        "baseline_metrics": dict(decision.baseline_metrics),
        "candidate_metrics": dict(decision.candidate_metrics),
        "metric_improvements": dict(decision.metric_improvements),
        "metric_summaries": [_metric_summary_payload(summary) for summary in decision.metric_summaries],
        "investigation_digests": list(decision.investigation_digests),
    }


def _decision_from_payload(payload: Any) -> GateDecision:
    decision = dict(_mapping(payload, "gate decision"))
    decision["metric_summaries"] = tuple(
        _metric_summary_from_payload(summary) for summary in decision.get("metric_summaries", [])
    )
    return GateDecision(**decision)


def _metric_summary_payload(summary: MetricSummary) -> dict[str, object]:
    return {
        "specification": {
            "name": summary.specification.name,
            "direction": summary.specification.direction,
        },
        "paired_values": [
            {
                "case_id": value.case_id,
                "baseline": value.baseline,
                "candidate": value.candidate,
                "improvement": value.improvement,
            }
            for value in summary.paired_values
        ],
        "missing_case_ids": list(summary.missing_case_ids),
    }


def _metric_summary_from_payload(payload: Any) -> MetricSummary:
    summary = _mapping(payload, "metric summary")
    specification = MetricSpecification(**_mapping(summary["specification"], "metric specification"))
    paired_values = tuple(
        PairedMetricValue(**_mapping(value, "paired metric value"))
        for value in summary.get("paired_values", [])
    )
    return MetricSummary(
        specification=specification,
        paired_values=paired_values,
        missing_case_ids=tuple(summary.get("missing_case_ids", [])),
    )


def _review_payload(review: ReviewDecision) -> dict[str, object]:
    return {
        "reviewer_id": review.reviewer_id,
        "approved": review.approved,
        "reason": review.reason,
        "decided_at": review.decided_at.isoformat(),
        "investigation_digests": list(review.investigation_digests),
    }


def _review_from_payload(payload: Any) -> ReviewDecision:
    review = _mapping(payload, "review")
    return ReviewDecision(
        reviewer_id=str(review["reviewer_id"]),
        approved=bool(review["approved"]),
        reason=str(review["reason"]),
        decided_at=datetime.fromisoformat(str(review["decided_at"])),
        investigation_digests=tuple(review.get("investigation_digests", [])),
    )


def _authorization_payload(authorization: DeliveryAuthorization) -> dict[str, object]:
    return {
        "authorization_id": authorization.authorization_id,
        "job_id": authorization.job_id,
        "candidate_id": authorization.candidate_id,
        "scope_ref": authorization.scope_ref,
        "authorized_by": authorization.authorized_by,
        "expires_at": authorization.expires_at.isoformat(),
        "revoked_at": authorization.revoked_at.isoformat() if authorization.revoked_at else None,
        "revocation_reason": authorization.revocation_reason,
        "investigation_digests": list(authorization.investigation_digests),
    }


def _authorization_from_payload(payload: Any) -> DeliveryAuthorization:
    authorization = _mapping(payload, "delivery authorization")
    revoked_at = authorization.get("revoked_at")
    return DeliveryAuthorization(
        authorization_id=str(authorization["authorization_id"]),
        job_id=str(authorization["job_id"]),
        candidate_id=str(authorization["candidate_id"]),
        scope_ref=str(authorization["scope_ref"]),
        authorized_by=str(authorization["authorized_by"]),
        expires_at=datetime.fromisoformat(str(authorization["expires_at"])),
        revoked_at=datetime.fromisoformat(str(revoked_at)) if revoked_at else None,
        revocation_reason=authorization.get("revocation_reason"),
        investigation_digests=tuple(authorization.get("investigation_digests", [])),
    )


def _receipt_payload(receipt: ActivationReceipt) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "authorization_id": receipt.authorization_id,
        "candidate_id": receipt.candidate_id,
        "scope_ref": receipt.scope_ref,
        "provider_ref": receipt.provider_ref,
        "delivered_at": receipt.delivered_at.isoformat(),
        "investigation_digests": list(receipt.investigation_digests),
    }


def _receipt_from_payload(payload: Any) -> ActivationReceipt:
    receipt = _mapping(payload, "activation receipt")
    return ActivationReceipt(
        receipt_id=str(receipt["receipt_id"]),
        authorization_id=str(receipt["authorization_id"]),
        candidate_id=str(receipt["candidate_id"]),
        scope_ref=str(receipt["scope_ref"]),
        provider_ref=str(receipt["provider_ref"]),
        delivered_at=datetime.fromisoformat(str(receipt["delivered_at"])),
        investigation_digests=tuple(receipt.get("investigation_digests", [])),
    )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"stored {name} must be an object")
    return value
