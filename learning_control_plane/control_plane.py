"""Offline candidate evaluation and deterministic gate decisions for advisory skills."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from .evaluation import (
    EvaluationBackend,
    EvaluationDataset,
    EvaluationRequest,
    EvaluationVariant,
    Metric,
    PairedCaseResult,
    PairedEvaluationResult,
    RunOne,
)
from .evidence import EvidenceContext, EvidenceEvent, EvidenceSink

logger = logging.getLogger("learning_control_plane.control_plane")

JobState = Literal["draft", "evaluating", "ready_for_review", "rejected", "failed"]


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _non_empty(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "advisory_skill", _non_empty(self.advisory_skill, "advisory_skill"))
        source_trace_ids = tuple(_non_empty(trace_id, "source_trace_id") for trace_id in self.source_trace_ids)
        object.__setattr__(self, "source_trace_ids", source_trace_ids)


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """The deterministic rules a candidate must satisfy before human review."""

    policy_version: str
    primary_metric: str
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
        if not math.isfinite(self.minimum_primary_improvement):
            raise ValueError("minimum_primary_improvement must be finite")
        if self.minimum_complete_cases < 1:
            raise ValueError("minimum_complete_cases must be at least 1")
        if self.maximum_failed_cases < 0:
            raise ValueError("maximum_failed_cases must not be negative")
        if self.maximum_attempts < 1:
            raise ValueError("maximum_attempts must be at least 1")


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The reproducible result of applying one policy to one paired evaluation."""

    approved: bool
    policy_version: str
    reasons: tuple[str, ...]
    baseline_metrics: Mapping[str, float]
    candidate_metrics: Mapping[str, float]


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
    error: str | None = None
    evidence_event_ids: tuple[str, ...] = ()


class LearningControlPlane:
    """Own offline candidate state and deterministic promotion-to-review decisions."""

    def __init__(
        self,
        *,
        policy: PromotionPolicy,
        evaluation_backend: EvaluationBackend,
        evidence_sink: EvidenceSink | None = None,
    ) -> None:
        self._policy = policy
        self._evaluation_backend = evaluation_backend
        self._evidence_sink = evidence_sink
        self._candidates: dict[str, AdvisorySkillCandidate] = {}
        self._jobs: dict[str, LearningJob] = {}

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
                    "advisory_skill_char_count": len(candidate.advisory_skill),
                },
            )
        )
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
        return job

    def get_job(self, job_id: str) -> LearningJob:
        """Return the current immutable record for one learning job."""

        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown learning job: {job_id}")
        return job

    async def run_job(self, job_id: str, run_one: RunOne, metric: Metric) -> LearningJob:
        """Run one draft job offline and advance it only to review or rejection."""

        job = self.get_job(job_id)
        if job.state != "draft":
            raise ValueError(f"learning job is not ready to run: {job.state}")

        job = replace(job, state="evaluating", attempt_count=job.attempt_count + 1, error=None)
        self._jobs[job_id] = job

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
            return job

        decision = self._apply_gate(evaluation)
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
        return job

    def _apply_gate(self, evaluation: PairedEvaluationResult) -> GateDecision:
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

        required_metrics = (self._policy.primary_metric, *self._policy.protected_metrics)
        baseline_metrics, candidate_metrics = self._paired_mean_metrics(complete_pairs, required_metrics, reasons)

        primary = self._policy.primary_metric
        baseline_primary = baseline_metrics.get(primary)
        candidate_primary = candidate_metrics.get(primary)
        if baseline_primary is None or candidate_primary is None:
            reasons.append(f"missing primary metric: {primary}")
        elif candidate_primary - baseline_primary < self._policy.minimum_primary_improvement:
            reasons.append(
                f"primary metric did not improve by {self._policy.minimum_primary_improvement}: "
                f"{baseline_primary} -> {candidate_primary}"
            )

        for metric_name in self._policy.protected_metrics:
            baseline = baseline_metrics.get(metric_name)
            candidate = candidate_metrics.get(metric_name)
            if baseline is None or candidate is None:
                reasons.append(f"missing protected metric: {metric_name}")
            elif candidate < baseline:
                reasons.append(f"protected metric regressed: {metric_name} {baseline} -> {candidate}")

        return GateDecision(
            approved=not reasons,
            policy_version=self._policy.policy_version,
            reasons=tuple(reasons),
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
        )

    @staticmethod
    def _complete_pairs(case_results: Sequence[PairedCaseResult]) -> tuple[PairedCaseResult, ...]:
        return tuple(
            pair
            for pair in case_results
            if pair.baseline.error is None and pair.candidate.error is None
        )

    @staticmethod
    def _paired_mean_metrics(
        pairs: Sequence[PairedCaseResult],
        metric_names: Sequence[str],
        reasons: list[str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        baseline_metrics: dict[str, float] = {}
        candidate_metrics: dict[str, float] = {}
        for metric_name in dict.fromkeys(metric_names):
            baseline_values: list[float] = []
            candidate_values: list[float] = []
            for pair in pairs:
                baseline = pair.baseline.metrics.get(metric_name)
                candidate = pair.candidate.metrics.get(metric_name)
                if baseline is None or candidate is None:
                    reasons.append(f"missing metric {metric_name} for case {pair.case_id}")
                    continue
                baseline_values.append(baseline)
                candidate_values.append(candidate)
            if baseline_values and candidate_values:
                baseline_metrics[metric_name] = sum(baseline_values) / len(baseline_values)
                candidate_metrics[metric_name] = sum(candidate_values) / len(candidate_values)
        return baseline_metrics, candidate_metrics

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
        event = EvidenceEvent(
            event_type="gate.decided",
            context=job.evaluation_request.evidence_context,
            attributes={
                "approved": decision.approved,
                "reason_count": len(decision.reasons),
                "dataset_digest": evaluation.request.dataset.manifest_digest,
                "case_count": len(evaluation.case_results),
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


__all__ = [
    "AdvisorySkillCandidate",
    "GateDecision",
    "JobState",
    "LearningControlPlane",
    "LearningJob",
    "PromotionPolicy",
]
