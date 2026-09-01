"""Offline candidate evaluation and deterministic gate decisions for advisory skills."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from .evaluation import (
    EvaluationBackend,
    EvaluationDataset,
    EvaluationRequest,
    EvaluationVariant,
    Metric,
    MetricSpecification,
    MetricSummary,
    PairedCaseResult,
    PairedEvaluationResult,
    RunOne,
)
from .evidence import EvidenceContext, EvidenceEvent, EvidenceSink

if TYPE_CHECKING:
    from .persistence import SQLiteControlPlaneRepository

logger = logging.getLogger("learning_control_plane.control_plane")

JobState = Literal["draft", "evaluating", "ready_for_review", "approved", "rejected", "failed"]


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class AdvisorySkillCandidate:
    """A proposed advisory skill that cannot change agent permissions or code."""

    candidate_id: str
    advisory_skill: str
    source_trace_ids: Sequence[str] = ()
    source_investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "advisory_skill", _non_empty(self.advisory_skill, "advisory_skill"))
        source_trace_ids = tuple(_non_empty(trace_id, "source_trace_id") for trace_id in self.source_trace_ids)
        object.__setattr__(self, "source_trace_ids", source_trace_ids)
        investigation_digests = tuple(
            _non_empty(digest, "source_investigation_digest") for digest in self.source_investigation_digests
        )
        object.__setattr__(self, "source_investigation_digests", investigation_digests)


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """The deterministic rules a candidate must satisfy before human review."""

    policy_version: str
    primary_metric: str
    metric_specifications: Sequence[MetricSpecification] = ()
    minimum_primary_improvement: float = 0.0
    protected_metrics: Sequence[str] = ()
    minimum_complete_cases: int = 1
    maximum_failed_cases: int = 0
    maximum_attempts: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _non_empty(self.policy_version, "policy_version"))
        object.__setattr__(self, "primary_metric", _non_empty(self.primary_metric, "primary_metric"))
        protected = tuple(_non_empty(name, "protected metric") for name in self.protected_metrics)
        object.__setattr__(self, "protected_metrics", protected)
        specifications = tuple(self.metric_specifications)
        specification_names = [specification.name for specification in specifications]
        if len(specification_names) != len(set(specification_names)):
            raise ValueError("metric specification names must be unique")
        object.__setattr__(self, "metric_specifications", specifications)
        if not math.isfinite(self.minimum_primary_improvement):
            raise ValueError("minimum_primary_improvement must be finite")
        if self.minimum_complete_cases < 1:
            raise ValueError("minimum_complete_cases must be at least 1")
        if self.maximum_failed_cases < 0:
            raise ValueError("maximum_failed_cases must not be negative")
        if self.maximum_attempts < 1:
            raise ValueError("maximum_attempts must be at least 1")

    def metric_specification(self, metric_name: str) -> MetricSpecification:
        """Return a declared specification or preserve the original score direction."""

        for specification in self.metric_specifications:
            if specification.name == metric_name:
                return specification
        return MetricSpecification(name=metric_name)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The reproducible result of applying one policy to one paired evaluation."""

    approved: bool
    policy_version: str
    reasons: tuple[str, ...]
    baseline_metrics: Mapping[str, float]
    candidate_metrics: Mapping[str, float]
    metric_improvements: Mapping[str, float] = field(default_factory=dict)
    metric_summaries: Sequence[MetricSummary] = ()
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_improvements", dict(self.metric_improvements))
        object.__setattr__(self, "metric_summaries", tuple(self.metric_summaries))
        investigation_digests = tuple(
            _non_empty(digest, "investigation_digest") for digest in self.investigation_digests
        )
        object.__setattr__(self, "investigation_digests", investigation_digests)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """A human decision on a passing candidate before it can be authorized."""

    reviewer_id: str
    approved: bool
    reason: str
    decided_at: datetime
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewer_id", _non_empty(self.reviewer_id, "reviewer_id"))
        object.__setattr__(self, "reason", _non_empty(self.reason, "review reason"))
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware")
        object.__setattr__(
            self,
            "investigation_digests",
            tuple(_non_empty(digest, "investigation_digest") for digest in self.investigation_digests),
        )


@dataclass(frozen=True, slots=True)
class DeliveryAuthorization:
    """A human-approved, scope-bound right for a host to activate one candidate."""

    authorization_id: str
    job_id: str
    candidate_id: str
    scope_ref: str
    authorized_by: str
    expires_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_id", _non_empty(self.authorization_id, "authorization_id"))
        object.__setattr__(self, "job_id", _non_empty(self.job_id, "job_id"))
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "scope_ref", _non_empty(self.scope_ref, "scope_ref"))
        object.__setattr__(self, "authorized_by", _non_empty(self.authorized_by, "authorized_by"))
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.revoked_at is not None and self.revoked_at.tzinfo is None:
            raise ValueError("revoked_at must be timezone-aware")
        object.__setattr__(
            self,
            "investigation_digests",
            tuple(_non_empty(digest, "investigation_digest") for digest in self.investigation_digests),
        )

    def is_active(self, now: datetime) -> bool:
        """Return whether this authorization is still valid at the supplied time."""

        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return self.revoked_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class ActivationReceipt:
    """A provider's immutable confirmation that it delivered an authorized skill."""

    receipt_id: str
    authorization_id: str
    candidate_id: str
    scope_ref: str
    provider_ref: str
    delivered_at: datetime
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _non_empty(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "authorization_id", _non_empty(self.authorization_id, "authorization_id"))
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "scope_ref", _non_empty(self.scope_ref, "scope_ref"))
        object.__setattr__(self, "provider_ref", _non_empty(self.provider_ref, "provider_ref"))
        if self.delivered_at.tzinfo is None:
            raise ValueError("delivered_at must be timezone-aware")
        object.__setattr__(
            self,
            "investigation_digests",
            tuple(_non_empty(digest, "investigation_digest") for digest in self.investigation_digests),
        )


@dataclass(frozen=True, slots=True)
class LearningJob:
    """One offline attempt to evaluate and gate a registered candidate."""

    job_id: str
    candidate_id: str
    evaluation_request: EvaluationRequest
    state: JobState = "draft"
    attempt_count: int = 0
    evaluation: PairedEvaluationResult | None = None
    decision: GateDecision | None = None
    review: ReviewDecision | None = None
    error: str | None = None
    evidence_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewQueueItem:
    """A gate-passing candidate waiting for a human approval decision."""

    job: LearningJob
    candidate: AdvisorySkillCandidate


@dataclass(frozen=True, slots=True)
class JobAuditRecord:
    """The complete decision and delivery history for one learning job."""

    job: LearningJob
    candidate: AdvisorySkillCandidate
    authorizations: tuple[DeliveryAuthorization, ...]
    receipts: tuple[ActivationReceipt, ...]


class LearningControlPlane:
    """Own offline candidate state and deterministic promotion-to-review decisions."""

    def __init__(
        self,
        *,
        policy: PromotionPolicy,
        evaluation_backend: EvaluationBackend,
        evidence_sink: EvidenceSink | None = None,
        repository: SQLiteControlPlaneRepository | None = None,
    ) -> None:
        self._policy = policy
        self._evaluation_backend = evaluation_backend
        self._evidence_sink = evidence_sink
        self._repository = repository
        if repository is None:
            self._candidates: dict[str, AdvisorySkillCandidate] = {}
            self._jobs: dict[str, LearningJob] = {}
            self._authorizations: dict[str, DeliveryAuthorization] = {}
            self._receipts: dict[str, ActivationReceipt] = {}
        else:
            state = repository.load()
            self._candidates = {candidate.candidate_id: candidate for candidate in state.candidates}
            self._jobs = {job.job_id: job for job in state.jobs}
            self._authorizations = {
                authorization.authorization_id: authorization for authorization in state.authorizations
            }
            self._receipts = {receipt.receipt_id: receipt for receipt in state.receipts}

    def register_candidate(self, candidate: AdvisorySkillCandidate, context: EvidenceContext) -> AdvisorySkillCandidate:
        """Register one immutable advisory-skill candidate for offline evaluation."""

        if candidate.candidate_id in self._candidates:
            raise ValueError(f"candidate already exists: {candidate.candidate_id}")

        self._candidates[candidate.candidate_id] = candidate
        candidate_context = replace(
            context,
            candidate_id=candidate.candidate_id,
            policy_version=self._policy.policy_version,
        )
        self._emit(
            EvidenceEvent(
                event_type="candidate.created",
                context=candidate_context,
                attributes={
                    "source_trace_count": len(candidate.source_trace_ids),
                    "source_investigation_count": len(candidate.source_investigation_digests),
                    "advisory_skill_char_count": len(candidate.advisory_skill),
                },
            )
        )
        self._persist()
        return candidate

    def create_job(
        self,
        *,
        candidate_id: str,
        evaluation_id: str,
        context: EvidenceContext,
        dataset: EvaluationDataset,
        baseline_variant_id: str = "baseline",
    ) -> LearningJob:
        """Create a draft job that compares one candidate with the unchanged baseline."""

        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"unknown candidate: {candidate_id}")

        job_context = replace(
            context,
            evaluation_id=evaluation_id,
            candidate_id=candidate_id,
            dataset_version=dataset.version,
            policy_version=self._policy.policy_version,
        )
        request = EvaluationRequest(
            evaluation_id=evaluation_id,
            evidence_context=job_context,
            dataset=dataset,
            baseline=EvaluationVariant(variant_id=baseline_variant_id),
            candidate=EvaluationVariant(candidate_id, advisory_skill=candidate.advisory_skill),
        )
        job = LearningJob(
            job_id=f"job_{uuid4().hex}",
            candidate_id=candidate_id,
            evaluation_request=request,
        )
        self._jobs[job.job_id] = job
        self._persist()
        return job

    def get_job(self, job_id: str) -> LearningJob:
        """Return the current immutable record for one learning job."""

        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown learning job: {job_id}")
        return job

    def list_jobs(self, *, state: JobState | None = None) -> tuple[LearningJob, ...]:
        """Return persisted jobs, optionally limited to one workflow state."""

        jobs = tuple(sorted(self._jobs.values(), key=lambda job: job.job_id))
        if state is None:
            return jobs
        return tuple(job for job in jobs if job.state == state)

    def list_review_queue(self) -> tuple[ReviewQueueItem, ...]:
        """Return every gate-passing job that still requires a human decision."""

        return tuple(
            ReviewQueueItem(job=job, candidate=self._candidates[job.candidate_id])
            for job in self.list_jobs(state="ready_for_review")
        )

    def get_job_audit_record(self, job_id: str) -> JobAuditRecord:
        """Return a job's candidate, decision, approval, delivery, and receipt history."""

        job = self.get_job(job_id)
        candidate = self._candidates[job.candidate_id]
        authorizations = tuple(
            sorted(
                (
                    authorization
                    for authorization in self._authorizations.values()
                    if authorization.job_id == job_id
                ),
                key=lambda authorization: authorization.authorization_id,
            )
        )
        authorization_ids = {authorization.authorization_id for authorization in authorizations}
        receipts = tuple(
            sorted(
                (
                    receipt
                    for receipt in self._receipts.values()
                    if receipt.authorization_id in authorization_ids
                ),
                key=lambda receipt: receipt.receipt_id,
            )
        )
        return JobAuditRecord(
            job=job,
            candidate=candidate,
            authorizations=authorizations,
            receipts=receipts,
        )

    async def run_job(self, job_id: str, run_one: RunOne, metric: Metric) -> LearningJob:
        """Run one draft job offline and advance it only to review or rejection."""

        job = self.get_job(job_id)
        if job.state != "draft":
            raise ValueError(f"learning job is not ready to run: {job.state}")

        job = replace(job, state="evaluating", attempt_count=job.attempt_count + 1, error=None)
        self._jobs[job_id] = job
        self._persist()

        try:
            evaluation = await self._evaluation_backend.evaluate(job.evaluation_request, run_one, metric)
        except Exception as error:
            failure_event_id = self._emit_failure(job, error)
            event_ids = job.evidence_event_ids
            if failure_event_id is not None:
                event_ids += (failure_event_id,)
            job = replace(
                job,
                state="failed",
                error=f"{type(error).__name__}: {error}",
                evidence_event_ids=event_ids,
            )
            self._jobs[job_id] = job
            self._persist()
            return job

        decision = self._apply_gate(evaluation, candidate=self._candidates[job.candidate_id])
        gate_event_id = self._emit_gate(job, evaluation, decision)
        event_ids = job.evidence_event_ids
        if gate_event_id is not None:
            event_ids += (gate_event_id,)
        state: JobState = "ready_for_review" if decision.approved else "rejected"
        job = replace(
            job,
            state=state,
            evaluation=evaluation,
            decision=decision,
            evidence_event_ids=event_ids,
        )
        self._jobs[job_id] = job
        self._persist()
        return job

    def retry_job(self, job_id: str) -> LearningJob:
        """Return an infrastructure-failed job to draft when its retry budget remains."""

        job = self.get_job(job_id)
        if job.state != "failed":
            raise ValueError("only failed learning jobs can be retried")
        if job.attempt_count >= self._policy.maximum_attempts:
            raise ValueError("learning job has exhausted its retry budget")

        job = replace(job, state="draft", error=None)
        self._jobs[job_id] = job
        self._persist()
        return job

    def review_job(self, job_id: str, *, reviewer_id: str, approved: bool, reason: str) -> LearningJob:
        """Record the required human decision for a candidate that passed the gate."""

        job = self.get_job(job_id)
        if job.state != "ready_for_review":
            raise ValueError("only gate-passing jobs can be reviewed")

        review = ReviewDecision(
            reviewer_id=reviewer_id,
            approved=approved,
            reason=reason,
            decided_at=datetime.now(UTC),
            investigation_digests=job.decision.investigation_digests if job.decision else (),
        )
        state: JobState = "approved" if approved else "rejected"
        review_event_id = self._emit(
            EvidenceEvent(
                event_type="review.decided",
                context=job.evaluation_request.evidence_context,
                attributes={
                    "approved": approved,
                    "reviewer_id": reviewer_id,
                    "investigation_digest_count": len(review.investigation_digests),
                },
            )
        )
        event_ids = job.evidence_event_ids
        if review_event_id is not None:
            event_ids += (review_event_id,)
        job = replace(job, state=state, review=review, evidence_event_ids=event_ids)
        self._jobs[job_id] = job
        self._persist()
        return job

    def authorize_delivery(self, job_id: str, *, scope_ref: str, expires_at: datetime) -> DeliveryAuthorization:
        """Authorize one reviewed candidate for one scope; the host performs delivery."""

        job = self.get_job(job_id)
        if job.state != "approved" or job.review is None or not job.review.approved:
            raise ValueError("only human-approved jobs can be authorized for delivery")
        if expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if expires_at <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        if any(
            authorization.job_id == job_id
            and authorization.scope_ref == scope_ref
            and authorization.is_active(datetime.now(UTC))
            for authorization in self._authorizations.values()
        ):
            raise ValueError("an active delivery authorization already exists for this job and scope")

        authorization = DeliveryAuthorization(
            authorization_id=f"auth_{uuid4().hex}",
            job_id=job_id,
            candidate_id=job.candidate_id,
            scope_ref=scope_ref,
            authorized_by=job.review.reviewer_id,
            expires_at=expires_at,
            investigation_digests=job.decision.investigation_digests if job.decision else (),
        )
        self._authorizations[authorization.authorization_id] = authorization
        self._emit(
            EvidenceEvent(
                event_type="delivery.authorized",
                context=replace(job.evaluation_request.evidence_context, scope_ref=scope_ref),
                attributes={
                    "authorization_id": authorization.authorization_id,
                    "expires_at": expires_at.isoformat(),
                    "investigation_digest_count": len(authorization.investigation_digests),
                },
            )
        )
        self._persist()
        return authorization

    def record_activation_receipt(self, receipt: ActivationReceipt) -> ActivationReceipt:
        """Record a host/provider receipt only when it matches an active authorization."""

        if receipt.receipt_id in self._receipts:
            raise ValueError(f"activation receipt already exists: {receipt.receipt_id}")
        authorization = self._authorizations.get(receipt.authorization_id)
        if authorization is None:
            raise ValueError(f"unknown delivery authorization: {receipt.authorization_id}")
        if not authorization.is_active(receipt.delivered_at):
            raise ValueError("delivery authorization is expired or revoked")
        if receipt.candidate_id != authorization.candidate_id or receipt.scope_ref != authorization.scope_ref:
            raise ValueError("activation receipt does not match delivery authorization")
        if receipt.investigation_digests != authorization.investigation_digests:
            raise ValueError("activation receipt does not match delivery authorization evidence")

        self._receipts[receipt.receipt_id] = receipt
        job = self.get_job(authorization.job_id)
        self._emit(
            EvidenceEvent(
                event_type="delivery.receipted",
                context=replace(job.evaluation_request.evidence_context, scope_ref=receipt.scope_ref),
                attributes={
                    "authorization_id": receipt.authorization_id,
                    "provider_ref": receipt.provider_ref,
                    "investigation_digest_count": len(receipt.investigation_digests),
                },
            )
        )
        self._persist()
        return receipt

    def revoke_delivery(self, authorization_id: str, *, revoked_by: str, reason: str) -> DeliveryAuthorization:
        """Revoke a delivery authorization so no later receipt can be accepted."""

        authorization = self._authorizations.get(authorization_id)
        if authorization is None:
            raise ValueError(f"unknown delivery authorization: {authorization_id}")
        if authorization.revoked_at is not None:
            raise ValueError("delivery authorization is already revoked")

        revoked_by = _non_empty(revoked_by, "revoked_by")
        reason = _non_empty(reason, "revocation reason")
        authorization = replace(
            authorization,
            revoked_at=datetime.now(UTC),
            revocation_reason=reason,
        )
        self._authorizations[authorization_id] = authorization
        job = self.get_job(authorization.job_id)
        self._emit(
            EvidenceEvent(
                event_type="delivery.revoked",
                context=replace(job.evaluation_request.evidence_context, scope_ref=authorization.scope_ref),
                attributes={"authorization_id": authorization_id, "revoked_by": revoked_by},
            )
        )
        self._persist()
        return authorization

    def _persist(self) -> None:
        """Save the complete workflow state when local persistence is enabled."""

        if self._repository is None:
            return
        from .persistence import PersistedControlPlaneState

        self._repository.save(
            PersistedControlPlaneState(
                candidates=tuple(self._candidates.values()),
                jobs=tuple(self._jobs.values()),
                authorizations=tuple(self._authorizations.values()),
                receipts=tuple(self._receipts.values()),
            )
        )

    def _apply_gate(
        self,
        evaluation: PairedEvaluationResult,
        *,
        candidate: AdvisorySkillCandidate,
    ) -> GateDecision:
        reasons: list[str] = []
        complete_pairs = self._complete_pairs(evaluation.case_results)
        failed_case_count = len(evaluation.case_results) - len(complete_pairs)
        if failed_case_count > self._policy.maximum_failed_cases:
            reasons.append(
                f"too many failed baseline/candidate pairs "
                f"({failed_case_count} > {self._policy.maximum_failed_cases})"
            )
        if len(complete_pairs) < self._policy.minimum_complete_cases:
            reasons.append(
                "not enough complete baseline/candidate pairs "
                f"({len(complete_pairs)} < {self._policy.minimum_complete_cases})"
            )

        required_metrics = tuple(dict.fromkeys((self._policy.primary_metric, *self._policy.protected_metrics)))
        summaries = tuple(
            evaluation.metric_summary(self._policy.metric_specification(metric_name))
            for metric_name in required_metrics
        )
        baseline_metrics: dict[str, float] = {}
        candidate_metrics: dict[str, float] = {}
        metric_improvements: dict[str, float] = {}
        for summary in summaries:
            baseline_mean = summary.baseline_mean
            candidate_mean = summary.candidate_mean
            mean_improvement = summary.mean_improvement
            if baseline_mean is not None:
                baseline_metrics[summary.specification.name] = baseline_mean
            if candidate_mean is not None:
                candidate_metrics[summary.specification.name] = candidate_mean
            if mean_improvement is not None:
                metric_improvements[summary.specification.name] = mean_improvement
            for case_id in summary.missing_case_ids:
                reasons.append(f"missing metric {summary.specification.name} for case {case_id}")

        primary = self._policy.primary_metric
        baseline_primary = baseline_metrics.get(primary)
        candidate_primary = candidate_metrics.get(primary)
        primary_improvement = metric_improvements.get(primary)
        if baseline_primary is None or candidate_primary is None:
            reasons.append(f"missing primary metric: {primary}")
        elif primary_improvement is None or primary_improvement < self._policy.minimum_primary_improvement:
            reasons.append(
                f"primary metric did not improve by {self._policy.minimum_primary_improvement}: "
                f"{baseline_primary} -> {candidate_primary} "
                f"({self._policy.metric_specification(primary).direction})"
            )

        for metric_name in self._policy.protected_metrics:
            baseline = baseline_metrics.get(metric_name)
            candidate_value = candidate_metrics.get(metric_name)
            improvement = metric_improvements.get(metric_name)
            if baseline is None or candidate_value is None:
                reasons.append(f"missing protected metric: {metric_name}")
            elif improvement is None or improvement < 0:
                reasons.append(
                    f"protected metric regressed: {metric_name} {baseline} -> {candidate_value} "
                    f"({self._policy.metric_specification(metric_name).direction})"
                )

        return GateDecision(
            approved=not reasons,
            policy_version=self._policy.policy_version,
            reasons=tuple(reasons),
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            metric_improvements=metric_improvements,
            metric_summaries=summaries,
            investigation_digests=_investigation_digests(candidate, evaluation),
        )

    @staticmethod
    def _complete_pairs(case_results: Sequence[PairedCaseResult]) -> tuple[PairedCaseResult, ...]:
        return tuple(
            pair
            for pair in case_results
            if pair.baseline.error is None and pair.candidate.error is None
        )

    def _emit_failure(self, job: LearningJob, error: Exception) -> str | None:
        event = EvidenceEvent(
            event_type="evaluation.failed",
            context=job.evaluation_request.evidence_context,
            attributes={"error_type": type(error).__name__},
        )
        return self._emit(event)

    def _emit_gate(
        self,
        job: LearningJob,
        evaluation: PairedEvaluationResult,
        decision: GateDecision,
    ) -> str | None:
        metrics = {f"baseline.{name}": value for name, value in decision.baseline_metrics.items()}
        metrics.update({f"candidate.{name}": value for name, value in decision.candidate_metrics.items()})
        metrics.update({f"improvement.{name}": value for name, value in decision.metric_improvements.items()})
        event = EvidenceEvent(
            event_type="gate.decided",
            context=job.evaluation_request.evidence_context,
            attributes={
                "approved": decision.approved,
                "reason_count": len(decision.reasons),
                "dataset_digest": evaluation.request.dataset.manifest_digest,
                "case_count": len(evaluation.case_results),
                "investigation_digest_count": len(decision.investigation_digests),
            },
            metrics=metrics,
        )
        return self._emit(event)

    def _emit(self, event: EvidenceEvent) -> str | None:
        if self._evidence_sink is None:
            return None
        try:
            delivered = self._evidence_sink.emit(event)
        except Exception:
            logger.warning("Learning-control-plane evidence emission failed", exc_info=True)
            return None
        return event.event_id if delivered else None


def _investigation_digests(
    candidate: AdvisorySkillCandidate,
    evaluation: PairedEvaluationResult,
) -> tuple[str, ...]:
    """Return the ordered, unique investigation evidence behind one gate decision."""

    digests = list(candidate.source_investigation_digests)
    digests.extend(
        case.source_investigation_digest
        for case in evaluation.request.dataset.cases
        if case.source_investigation_digest is not None
    )
    return tuple(dict.fromkeys(digests))


__all__ = [
    "AdvisorySkillCandidate",
    "GateDecision",
    "JobState",
    "LearningControlPlane",
    "LearningJob",
    "PromotionPolicy",
]
