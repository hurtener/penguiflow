"""Offline candidate evaluation and deterministic gate decisions for advisory skills."""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable, Mapping, Sequence
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
ConfidenceIntervalStatistic = Literal[
    "mean_improvement",
    "candidate_mean",
    "relative_mean_improvement",
    "relative_mean_regression",
]


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
    optimization_goal: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "advisory_skill", _non_empty(self.advisory_skill, "advisory_skill"))
        source_trace_ids = tuple(_non_empty(trace_id, "source_trace_id") for trace_id in self.source_trace_ids)
        object.__setattr__(self, "source_trace_ids", source_trace_ids)
        investigation_digests = tuple(
            _non_empty(digest, "source_investigation_digest") for digest in self.source_investigation_digests
        )
        object.__setattr__(self, "source_investigation_digests", investigation_digests)
        if self.optimization_goal is not None:
            object.__setattr__(
                self,
                "optimization_goal",
                _non_empty(self.optimization_goal, "optimization_goal"),
            )


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """The deterministic rules a candidate must satisfy before human review."""

    policy_version: str
    primary_metric: str
    metric_specifications: Sequence[MetricSpecification] = ()
    minimum_primary_improvement: float = 0.0
    primary_benefit_thresholds: Mapping[str, float] = field(default_factory=dict)
    protected_metrics: Sequence[str] = ()
    candidate_metric_thresholds: Mapping[str, float] = field(default_factory=dict)
    maximum_relative_mean_regressions: Mapping[str, float] = field(default_factory=dict)
    minimum_complete_cases: int = 1
    maximum_failed_cases: int = 0
    maximum_attempts: int = 3
    required_source_case_ids: Sequence[str] = ()
    minimum_complete_pairs_per_source_case: int = 1
    confidence_level: float = 0.95
    bootstrap_resamples: int = 10_000
    confidence_interval_requirements: Sequence[ConfidenceIntervalRequirement] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _non_empty(self.policy_version, "policy_version"))
        object.__setattr__(self, "primary_metric", _non_empty(self.primary_metric, "primary_metric"))
        protected = tuple(_non_empty(name, "protected metric") for name in self.protected_metrics)
        object.__setattr__(self, "protected_metrics", protected)
        thresholds: dict[str, float] = {}
        for raw_name, raw_value in self.candidate_metric_thresholds.items():
            name = _non_empty(str(raw_name), "candidate metric threshold")
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"candidate metric threshold {name!r} must be finite")
            thresholds[name] = value
        object.__setattr__(self, "candidate_metric_thresholds", thresholds)
        relative_regressions: dict[str, float] = {}
        for raw_name, raw_value in self.maximum_relative_mean_regressions.items():
            name = _non_empty(str(raw_name), "relative regression metric")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"maximum relative regression for {name!r} must be finite and non-negative")
            relative_regressions[name] = value
        object.__setattr__(self, "maximum_relative_mean_regressions", relative_regressions)
        specifications = tuple(self.metric_specifications)
        specification_names = [specification.name for specification in specifications]
        if len(specification_names) != len(set(specification_names)):
            raise ValueError("metric specification names must be unique")
        object.__setattr__(self, "metric_specifications", specifications)
        if not math.isfinite(self.minimum_primary_improvement):
            raise ValueError("minimum_primary_improvement must be finite")
        benefit_thresholds: dict[str, float] = {}
        for raw_name, raw_value in self.primary_benefit_thresholds.items():
            name = _non_empty(str(raw_name), "primary benefit metric")
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"primary benefit threshold {name!r} must be finite")
            benefit_thresholds[name] = value
        object.__setattr__(self, "primary_benefit_thresholds", benefit_thresholds)
        if self.minimum_complete_cases < 1:
            raise ValueError("minimum_complete_cases must be at least 1")
        if self.maximum_failed_cases < 0:
            raise ValueError("maximum_failed_cases must not be negative")
        if self.maximum_attempts < 1:
            raise ValueError("maximum_attempts must be at least 1")
        source_case_ids = tuple(
            _non_empty(source_case_id, "required source case ID")
            for source_case_id in self.required_source_case_ids
        )
        if len(source_case_ids) != len(set(source_case_ids)):
            raise ValueError("required source case IDs must be unique")
        object.__setattr__(self, "required_source_case_ids", source_case_ids)
        if self.minimum_complete_pairs_per_source_case < 1:
            raise ValueError("minimum_complete_pairs_per_source_case must be at least 1")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level must be greater than 0 and less than 1")
        if self.bootstrap_resamples < 100:
            raise ValueError("bootstrap_resamples must be at least 100")
        requirements = tuple(self.confidence_interval_requirements)
        requirement_keys = [(requirement.metric_name, requirement.statistic) for requirement in requirements]
        if len(requirement_keys) != len(set(requirement_keys)):
            raise ValueError("confidence interval requirements must be unique per metric and statistic")
        object.__setattr__(self, "confidence_interval_requirements", requirements)

    def metric_specification(self, metric_name: str) -> MetricSpecification:
        """Return a declared specification or preserve the original score direction."""

        for specification in self.metric_specifications:
            if specification.name == metric_name:
                return specification
        return MetricSpecification(name=metric_name)

    def primary_benefits(self) -> Mapping[str, float]:
        """Return one or more pre-registered benefits that can advance a candidate."""

        if self.primary_benefit_thresholds:
            return self.primary_benefit_thresholds
        return {self.primary_metric: self.minimum_primary_improvement}


@dataclass(frozen=True, slots=True)
class ConfidenceIntervalRequirement:
    """One conservative confidence-bound condition for a promotion metric."""

    metric_name: str
    statistic: ConfidenceIntervalStatistic
    minimum_lower_bound: float | None = None
    maximum_upper_bound: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_name", _non_empty(self.metric_name, "confidence interval metric"))
        if self.statistic not in {
            "mean_improvement",
            "candidate_mean",
            "relative_mean_improvement",
            "relative_mean_regression",
        }:
            raise ValueError("confidence interval statistic is not supported")
        has_lower_bound = self.minimum_lower_bound is not None
        has_upper_bound = self.maximum_upper_bound is not None
        if has_lower_bound == has_upper_bound:
            raise ValueError("confidence interval requirement needs exactly one lower or upper bound")
        if has_lower_bound and not math.isfinite(self.minimum_lower_bound):
            raise ValueError("confidence interval lower bound must be finite")
        if has_upper_bound and not math.isfinite(self.maximum_upper_bound):
            raise ValueError("confidence interval upper bound must be finite")


@dataclass(frozen=True, slots=True)
class MetricConfidenceInterval:
    """A bootstrap confidence interval retained with the gate decision."""

    metric_name: str
    statistic: ConfidenceIntervalStatistic
    confidence_level: float
    estimate: float
    lower_bound: float
    upper_bound: float
    required_lower_bound: float | None = None
    required_upper_bound: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_name", _non_empty(self.metric_name, "confidence interval metric"))
        if self.statistic not in {
            "mean_improvement",
            "candidate_mean",
            "relative_mean_improvement",
            "relative_mean_regression",
        }:
            raise ValueError("confidence interval statistic is not supported")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level must be greater than 0 and less than 1")
        has_lower_requirement = self.required_lower_bound is not None
        has_upper_requirement = self.required_upper_bound is not None
        if has_lower_requirement == has_upper_requirement:
            raise ValueError("confidence interval needs exactly one lower or upper requirement")
        values = [self.estimate, self.lower_bound, self.upper_bound]
        if self.required_lower_bound is not None:
            values.append(self.required_lower_bound)
        if self.required_upper_bound is not None:
            values.append(self.required_upper_bound)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("confidence interval values must be finite")
        if self.lower_bound > self.upper_bound:
            raise ValueError("confidence interval lower bound must not exceed its upper bound")


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
    confidence_intervals: Sequence[MetricConfidenceInterval] = ()
    established_primary_benefit_metrics: Sequence[str] = ()
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_improvements", dict(self.metric_improvements))
        object.__setattr__(self, "metric_summaries", tuple(self.metric_summaries))
        object.__setattr__(self, "confidence_intervals", tuple(self.confidence_intervals))
        object.__setattr__(
            self,
            "established_primary_benefit_metrics",
            tuple(
                _non_empty(metric, "established primary benefit metric")
                for metric in self.established_primary_benefit_metrics
            ),
        )
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
    skill_digest: str | None = None
    investigation_digests: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _non_empty(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "authorization_id", _non_empty(self.authorization_id, "authorization_id"))
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "scope_ref", _non_empty(self.scope_ref, "scope_ref"))
        object.__setattr__(self, "provider_ref", _non_empty(self.provider_ref, "provider_ref"))
        if self.skill_digest is not None:
            object.__setattr__(self, "skill_digest", _non_empty(self.skill_digest, "skill_digest"))
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

        return self.record_evaluation(job_id, evaluation)

    def record_evaluation(self, job_id: str, evaluation: PairedEvaluationResult) -> LearningJob:
        """Gate a completed evaluation produced by an external evaluation backend."""

        job = self.get_job(job_id)
        if job.state not in {"draft", "evaluating"}:
            raise ValueError(f"learning job is not ready to record evaluation: {job.state}")
        if evaluation.request != job.evaluation_request:
            raise ValueError("evaluation request does not match the registered learning job")
        if job.state == "draft":
            job = replace(job, state="evaluating", attempt_count=job.attempt_count + 1, error=None)
            self._jobs[job_id] = job
            self._persist()

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
        attributes: dict[str, object] = {
            "authorization_id": receipt.authorization_id,
            "provider_ref": receipt.provider_ref,
            "investigation_digest_count": len(receipt.investigation_digests),
        }
        if receipt.skill_digest is not None:
            attributes["skill_digest"] = receipt.skill_digest
        self._emit(
            EvidenceEvent(
                event_type="delivery.receipted",
                context=replace(job.evaluation_request.evidence_context, scope_ref=receipt.scope_ref),
                attributes=attributes,
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

        declared_metrics = tuple(specification.name for specification in self._policy.metric_specifications)
        primary_benefits = self._policy.primary_benefits()
        required_metrics = tuple(
            dict.fromkeys(
                (
                    *primary_benefits,
                    *self._policy.protected_metrics,
                    *self._policy.candidate_metric_thresholds,
                    *self._policy.maximum_relative_mean_regressions,
                    *(requirement.metric_name for requirement in self._policy.confidence_interval_requirements),
                    *declared_metrics,
                )
            )
        )
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

        primary_benefit_point_passes: dict[str, bool] = {}
        for metric_name, minimum_improvement in primary_benefits.items():
            baseline = baseline_metrics.get(metric_name)
            candidate_value = candidate_metrics.get(metric_name)
            improvement = metric_improvements.get(metric_name)
            primary_benefit_point_passes[metric_name] = bool(
                baseline is not None
                and candidate_value is not None
                and improvement is not None
                and improvement >= minimum_improvement
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

        for metric_name, threshold in self._policy.candidate_metric_thresholds.items():
            candidate_value = candidate_metrics.get(metric_name)
            specification = self._policy.metric_specification(metric_name)
            if candidate_value is None:
                reasons.append(f"missing candidate threshold metric: {metric_name}")
                continue
            threshold_passed = candidate_value >= threshold
            if specification.direction == "lower_is_better":
                threshold_passed = candidate_value <= threshold
            if not threshold_passed:
                reasons.append(
                    f"candidate metric missed threshold: {metric_name} {candidate_value} "
                    f"({specification.direction}, threshold={threshold})"
                )

        for metric_name, maximum_regression in self._policy.maximum_relative_mean_regressions.items():
            baseline = baseline_metrics.get(metric_name)
            candidate_value = candidate_metrics.get(metric_name)
            specification = self._policy.metric_specification(metric_name)
            if baseline is None or candidate_value is None:
                reasons.append(f"missing relative regression metric: {metric_name}")
                continue
            regression = _relative_regression(baseline, candidate_value, specification)
            if regression > maximum_regression:
                reasons.append(
                    f"relative mean regression exceeded: {metric_name} {baseline} -> {candidate_value} "
                    f"({specification.direction}, regression={regression}, maximum={maximum_regression})"
                )

        confidence_intervals = self._confidence_intervals_for_complete_pairs(
            evaluation=evaluation,
            complete_pairs=complete_pairs,
            reasons=reasons,
        )
        primary_benefit_interval_passes: dict[str, bool] = {}
        for interval in confidence_intervals:
            is_primary_benefit_interval = (
                interval.metric_name in primary_benefits
                and interval.statistic == "mean_improvement"
            )
            condition_passed = _confidence_interval_requirement_passed(interval)
            if is_primary_benefit_interval:
                primary_benefit_interval_passes[interval.metric_name] = condition_passed
                continue
            if (
                interval.required_lower_bound is not None
                and interval.lower_bound < interval.required_lower_bound
            ):
                reasons.append(
                    "confidence interval lower bound did not clear requirement: "
                    f"{interval.metric_name} {interval.statistic} "
                    f"{interval.lower_bound} < {interval.required_lower_bound} "
                    f"({interval.confidence_level:.0%} confidence)"
                )
            if (
                interval.required_upper_bound is not None
                and interval.upper_bound > interval.required_upper_bound
            ):
                reasons.append(
                    "confidence interval upper bound exceeded requirement: "
                    f"{interval.metric_name} {interval.statistic} "
                    f"{interval.upper_bound} > {interval.required_upper_bound} "
                    f"({interval.confidence_level:.0%} confidence)"
                )

        established_primary_benefits = tuple(
            metric_name
            for metric_name, point_passed in primary_benefit_point_passes.items()
            if point_passed and primary_benefit_interval_passes.get(metric_name, True)
        )
        if not established_primary_benefits:
            _append_primary_benefit_failure_reason(
                reasons,
                primary_benefits=primary_benefits,
                baseline_metrics=baseline_metrics,
                candidate_metrics=candidate_metrics,
                metric_improvements=metric_improvements,
                interval_passes=primary_benefit_interval_passes,
            )

        return GateDecision(
            approved=not reasons,
            policy_version=self._policy.policy_version,
            reasons=tuple(reasons),
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            metric_improvements=metric_improvements,
            metric_summaries=summaries,
            confidence_intervals=confidence_intervals,
            established_primary_benefit_metrics=established_primary_benefits,
            investigation_digests=_investigation_digests(candidate, evaluation),
        )

    @staticmethod
    def _complete_pairs(case_results: Sequence[PairedCaseResult]) -> tuple[PairedCaseResult, ...]:
        return tuple(
            pair
            for pair in case_results
            if pair.baseline.error is None and pair.candidate.error is None
        )

    def _confidence_intervals_for_complete_pairs(
        self,
        *,
        evaluation: PairedEvaluationResult,
        complete_pairs: Sequence[PairedCaseResult],
        reasons: list[str],
    ) -> tuple[MetricConfidenceInterval, ...]:
        """Calculate required bootstrap intervals after case coverage is complete."""

        requirements = self._policy.confidence_interval_requirements
        if not requirements:
            return ()

        pairs_by_source_case = _pairs_by_source_case(evaluation, complete_pairs)
        source_case_ids = self._policy.required_source_case_ids or tuple(sorted(pairs_by_source_case))
        if not source_case_ids:
            reasons.append("no complete source cases are available for confidence intervals")
            return ()

        for source_case_id in source_case_ids:
            pairs = pairs_by_source_case.get(source_case_id, ())
            if not pairs:
                reasons.append(f"missing frozen source case: {source_case_id}")
                continue
            if len(pairs) < self._policy.minimum_complete_pairs_per_source_case:
                reasons.append(
                    "not enough complete baseline/candidate pairs for frozen source case "
                    f"{source_case_id} ({len(pairs)} < {self._policy.minimum_complete_pairs_per_source_case})"
                )

        if any("frozen source case" in reason for reason in reasons):
            return ()
        if not _pairs_include_metrics(pairs_by_source_case, source_case_ids, requirements):
            for requirement in requirements:
                if not _pairs_include_metric(
                    pairs_by_source_case,
                    source_case_ids,
                    requirement.metric_name,
                ):
                    reasons.append(
                        "missing confidence interval metric "
                        f"{requirement.metric_name} for one or more complete source cases"
                    )
            return ()

        try:
            return _bootstrap_confidence_intervals(
                pairs_by_source_case=pairs_by_source_case,
                source_case_ids=source_case_ids,
                requirements=requirements,
                metric_specification=self._policy.metric_specification,
                confidence_level=self._policy.confidence_level,
                resamples=self._policy.bootstrap_resamples,
            )
        except ValueError as error:
            reasons.append(str(error))
            return ()

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
        metrics.update(
            {
                f"confidence_lower.{interval.metric_name}.{interval.statistic}": interval.lower_bound
                for interval in decision.confidence_intervals
            }
        )
        metrics.update(
            {
                f"confidence_upper.{interval.metric_name}.{interval.statistic}": interval.upper_bound
                for interval in decision.confidence_intervals
            }
        )
        attributes: dict[str, object] = {
            "approved": decision.approved,
            "reason_count": len(decision.reasons),
            "dataset_digest": evaluation.request.dataset.manifest_digest,
            "case_count": len(evaluation.case_results),
            "investigation_digest_count": len(decision.investigation_digests),
        }
        score_evidence = _evaluation_safe_evidence(evaluation)
        if score_evidence:
            attributes["evaluation_score_evidence"] = score_evidence
        event = EvidenceEvent(
            event_type="gate.decided",
            context=job.evaluation_request.evidence_context,
            attributes=attributes,
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


def _evaluation_safe_evidence(evaluation: PairedEvaluationResult) -> list[dict[str, object]]:
    """Return only the explicitly safe per-arm evidence supplied by an evaluator."""

    case_evidence: list[dict[str, object]] = []
    for pair in evaluation.case_results:
        arms = {
            "baseline": dict(pair.baseline.safe_evidence),
            "candidate": dict(pair.candidate.safe_evidence),
        }
        if not any(arms.values()):
            continue
        case_evidence.append({"case_id": pair.case_id, **arms})
    return case_evidence


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


def _relative_regression(
    baseline: float,
    candidate: float,
    specification: MetricSpecification,
) -> float:
    """Return the proportional loss from baseline, with zero meaning no regression."""

    absolute_regression = baseline - candidate
    if specification.direction == "lower_is_better":
        absolute_regression = candidate - baseline
    if absolute_regression <= 0:
        return 0.0
    if baseline == 0:
        return math.inf
    return absolute_regression / abs(baseline)


def _confidence_interval_requirement_passed(interval: MetricConfidenceInterval) -> bool:
    """Return whether one stored interval clears its frozen lower or upper condition."""

    lower_passed = (
        interval.required_lower_bound is None
        or interval.lower_bound >= interval.required_lower_bound
    )
    upper_passed = (
        interval.required_upper_bound is None
        or interval.upper_bound <= interval.required_upper_bound
    )
    return lower_passed and upper_passed


def _append_primary_benefit_failure_reason(
    reasons: list[str],
    *,
    primary_benefits: Mapping[str, float],
    baseline_metrics: Mapping[str, float],
    candidate_metrics: Mapping[str, float],
    metric_improvements: Mapping[str, float],
    interval_passes: Mapping[str, bool],
) -> None:
    """Explain why no pre-registered benefit was proven without choosing one after the run."""

    descriptions = []
    for metric_name, minimum_improvement in primary_benefits.items():
        baseline = baseline_metrics.get(metric_name)
        candidate = candidate_metrics.get(metric_name)
        improvement = metric_improvements.get(metric_name)
        interval_passed = interval_passes.get(metric_name, True)
        descriptions.append(
            f"{metric_name}: {baseline} -> {candidate}, improvement={improvement}, "
            f"required={minimum_improvement}, confidence_passed={interval_passed}"
        )
    if len(primary_benefits) == 1:
        reasons.append(f"primary benefit was not established: {descriptions[0]}")
        return
    candidates = "; ".join(descriptions)
    reasons.append(
        "no pre-registered primary benefit was established; require one of: "
        f"{candidates}"
    )


def _pairs_by_source_case(
    evaluation: PairedEvaluationResult,
    complete_pairs: Sequence[PairedCaseResult],
) -> dict[str, tuple[PairedCaseResult, ...]]:
    """Group repeated evaluations by the frozen question they execute."""

    source_case_by_imported_case = {
        case.case_id: str(case.inputs.get("source_case_id") or case.case_id)
        for case in evaluation.request.dataset.cases
    }
    grouped: dict[str, list[PairedCaseResult]] = {}
    for pair in complete_pairs:
        source_case_id = source_case_by_imported_case.get(pair.case_id, pair.case_id)
        grouped.setdefault(source_case_id, []).append(pair)
    return {source_case_id: tuple(pairs) for source_case_id, pairs in grouped.items()}


def _pairs_include_metrics(
    pairs_by_source_case: Mapping[str, Sequence[PairedCaseResult]],
    source_case_ids: Sequence[str],
    requirements: Sequence[ConfidenceIntervalRequirement],
) -> bool:
    """Return whether every confidence metric exists on both arms of every pair."""

    return all(
        _pairs_include_metric(pairs_by_source_case, source_case_ids, requirement.metric_name)
        for requirement in requirements
    )


def _pairs_include_metric(
    pairs_by_source_case: Mapping[str, Sequence[PairedCaseResult]],
    source_case_ids: Sequence[str],
    metric_name: str,
) -> bool:
    """Return whether one metric exists on each required paired execution."""

    return all(
        metric_name in pair.baseline.metrics and metric_name in pair.candidate.metrics
        for source_case_id in source_case_ids
        for pair in pairs_by_source_case.get(source_case_id, ())
    )


def _bootstrap_confidence_intervals(
    *,
    pairs_by_source_case: Mapping[str, Sequence[PairedCaseResult]],
    source_case_ids: Sequence[str],
    requirements: Sequence[ConfidenceIntervalRequirement],
    metric_specification: Callable[[str], MetricSpecification],
    confidence_level: float,
    resamples: int,
) -> tuple[MetricConfidenceInterval, ...]:
    """Bootstrap case-balanced paired metrics across questions and repetitions."""

    random_source = random.Random(
        _bootstrap_seed(pairs_by_source_case, source_case_ids, requirements, confidence_level, resamples)
    )
    estimates = {
        (requirement.metric_name, requirement.statistic): []
        for requirement in requirements
    }
    observed_pairs = [
        pair
        for source_case_id in source_case_ids
        for pair in pairs_by_source_case[source_case_id]
    ]
    observed_statistics = {
        (requirement.metric_name, requirement.statistic): _confidence_statistic(
            observed_pairs,
            requirement,
            metric_specification(requirement.metric_name),
        )
        for requirement in requirements
    }

    for _ in range(resamples):
        sampled_source_case_ids = [
            random_source.choice(source_case_ids)
            for _ in source_case_ids
        ]
        sampled_pairs = [
            pair
            for source_case_id in sampled_source_case_ids
            for pair in pairs_by_source_case[source_case_id]
        ]
        for requirement in requirements:
            key = (requirement.metric_name, requirement.statistic)
            estimates[key].append(
                _confidence_statistic(
                    sampled_pairs,
                    requirement,
                    metric_specification(requirement.metric_name),
                )
            )

    tail_probability = (1 - confidence_level) / 2
    intervals = []
    for requirement in requirements:
        key = (requirement.metric_name, requirement.statistic)
        samples = estimates[key]
        intervals.append(
            MetricConfidenceInterval(
                metric_name=requirement.metric_name,
                statistic=requirement.statistic,
                confidence_level=confidence_level,
                estimate=observed_statistics[key],
                lower_bound=_percentile(samples, tail_probability),
                upper_bound=_percentile(samples, 1 - tail_probability),
                required_lower_bound=requirement.minimum_lower_bound,
                required_upper_bound=requirement.maximum_upper_bound,
            )
        )
    return tuple(intervals)


def _confidence_statistic(
    pairs: Sequence[PairedCaseResult],
    requirement: ConfidenceIntervalRequirement,
    specification: MetricSpecification,
) -> float:
    """Calculate one direction-normalized statistic from paired metric values."""

    baseline_mean = sum(pair.baseline.metrics[requirement.metric_name] for pair in pairs) / len(pairs)
    candidate_mean = sum(pair.candidate.metrics[requirement.metric_name] for pair in pairs) / len(pairs)
    if requirement.statistic == "candidate_mean":
        return candidate_mean

    improvement = candidate_mean - baseline_mean
    if specification.direction == "lower_is_better":
        improvement = baseline_mean - candidate_mean
    if requirement.statistic == "mean_improvement":
        return improvement
    if baseline_mean == 0:
        raise ValueError(
            "cannot calculate relative mean improvement with a zero baseline metric: "
            f"{requirement.metric_name}"
        )
    relative_improvement = improvement / abs(baseline_mean)
    if requirement.statistic == "relative_mean_improvement":
        return relative_improvement
    return -relative_improvement


def _bootstrap_seed(
    pairs_by_source_case: Mapping[str, Sequence[PairedCaseResult]],
    source_case_ids: Sequence[str],
    requirements: Sequence[ConfidenceIntervalRequirement],
    confidence_level: float,
    resamples: int,
) -> str:
    """Build a stable random seed so one evidence set always yields one decision."""

    seed_material = [str(confidence_level), str(resamples)]
    seed_material.extend(source_case_ids)
    seed_material.extend(f"{item.metric_name}:{item.statistic}" for item in requirements)
    for source_case_id in source_case_ids:
        for pair in pairs_by_source_case[source_case_id]:
            seed_material.append(pair.case_id)
            seed_material.append(str(sorted(pair.baseline.metrics.items())))
            seed_material.append(str(sorted(pair.candidate.metrics.items())))
    return "|".join(seed_material)


def _percentile(values: Sequence[float], probability: float) -> float:
    """Return one linearly interpolated percentile from finite bootstrap estimates."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    lower_weight = upper_index - position
    upper_weight = position - lower_index
    return ordered[lower_index] * lower_weight + ordered[upper_index] * upper_weight


__all__ = [
    "AdvisorySkillCandidate",
    "ConfidenceIntervalRequirement",
    "GateDecision",
    "JobState",
    "LearningControlPlane",
    "LearningJob",
    "MetricConfidenceInterval",
    "PromotionPolicy",
]
