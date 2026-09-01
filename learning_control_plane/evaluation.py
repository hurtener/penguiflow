"""Standalone, local evaluation of a baseline and one advisory-skill candidate."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import statistics
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from .evidence import EvidenceContext, EvidenceEvent, EvidenceSink

logger = logging.getLogger("learning_control_plane.evaluation")

MetricDirection = Literal["higher_is_better", "lower_is_better"]


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def _json_digest(payload: object) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("evaluation cases must be JSON-serializable") from error
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _validated_metrics(metrics: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_name, raw_value in metrics.items():
        name = _non_empty(str(raw_name), "metric name")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"metric {name!r} must be finite")
        normalized[name] = value
    return normalized


async def _await_value(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One fixed input and expected outcome from an evaluation dataset."""

    case_id: str
    inputs: Mapping[str, Any]
    expected: Any = None
    source_trace_id: str | None = None
    source_investigation_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _non_empty(self.case_id, "case_id"))
        if self.source_investigation_digest is not None:
            object.__setattr__(
                self,
                "source_investigation_digest",
                _non_empty(self.source_investigation_digest, "source_investigation_digest"),
            )


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """A versioned, fixed evaluation dataset with a content digest."""

    dataset_id: str
    version: str
    cases: Sequence[EvaluationCase]
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _non_empty(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "version", _non_empty(self.version, "version"))
        cases = tuple(self.cases)
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation dataset case_id values must be unique")
        object.__setattr__(self, "cases", cases)
        manifest = {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "cases": [asdict(case) for case in cases],
        }
        object.__setattr__(self, "manifest_digest", _json_digest(manifest))


@dataclass(frozen=True, slots=True)
class EvaluationVariant:
    """One agent configuration, differing only by an advisory skill in the MVP."""

    variant_id: str
    advisory_skill: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "variant_id", _non_empty(self.variant_id, "variant_id"))
        if self.advisory_skill is not None:
            object.__setattr__(self, "advisory_skill", _non_empty(self.advisory_skill, "advisory_skill"))


@dataclass(frozen=True, slots=True)
class MetricSpecification:
    """Name one outcome metric and whether a larger or smaller value is better."""

    name: str
    direction: MetricDirection = "higher_is_better"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _non_empty(self.name, "metric name"))
        if self.direction not in ("higher_is_better", "lower_is_better"):
            raise ValueError("metric direction must be higher_is_better or lower_is_better")


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """Define one paired evaluation against a pinned agent deployment and dataset."""

    evaluation_id: str
    evidence_context: EvidenceContext
    dataset: EvaluationDataset
    baseline: EvaluationVariant
    candidate: EvaluationVariant

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_id", _non_empty(self.evaluation_id, "evaluation_id"))
        if self.evidence_context.evaluation_id != self.evaluation_id:
            raise ValueError("evidence_context.evaluation_id must match evaluation_id")
        if self.evidence_context.dataset_version != self.dataset.version:
            raise ValueError("evidence_context.dataset_version must match dataset.version")
        if self.baseline.advisory_skill is not None:
            raise ValueError("baseline must not include an advisory skill")
        if self.candidate.advisory_skill is None:
            raise ValueError("candidate must include an advisory skill")
        if self.baseline.variant_id == self.candidate.variant_id:
            raise ValueError("baseline and candidate variant_id values must differ")


@runtime_checkable
class RunOne(Protocol):
    """Run one evaluation case with one fixed variant."""

    def __call__(self, case: EvaluationCase, variant: EvaluationVariant) -> Any | Awaitable[Any]:
        ...


@runtime_checkable
class Metric(Protocol):
    """Score one agent output against one evaluation case."""

    def __call__(self, case: EvaluationCase, output: Any) -> Mapping[str, float] | Awaitable[Mapping[str, float]]:
        ...


@dataclass(frozen=True, slots=True)
class VariantCaseResult:
    """The output, scores, or failure recorded for one case and one variant."""

    variant_id: str
    output: Any = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PairedCaseResult:
    """Baseline and candidate results for the same evaluation case."""

    case_id: str
    baseline: VariantCaseResult
    candidate: VariantCaseResult


@dataclass(frozen=True, slots=True)
class PairedMetricValue:
    """One baseline/candidate metric pair, with positive improvement always better."""

    case_id: str
    baseline: float
    candidate: float
    improvement: float


@dataclass(frozen=True, slots=True)
class MetricSummary:
    """Complete paired evidence and descriptive statistics for one outcome metric."""

    specification: MetricSpecification
    paired_values: Sequence[PairedMetricValue]
    missing_case_ids: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "paired_values", tuple(self.paired_values))
        object.__setattr__(self, "missing_case_ids", tuple(self.missing_case_ids))

    @property
    def baseline_mean(self) -> float | None:
        """Return the mean baseline value, or None when no pair supplied this metric."""

        if not self.paired_values:
            return None
        return statistics.fmean(value.baseline for value in self.paired_values)

    @property
    def candidate_mean(self) -> float | None:
        """Return the mean candidate value, or None when no pair supplied this metric."""

        if not self.paired_values:
            return None
        return statistics.fmean(value.candidate for value in self.paired_values)

    @property
    def mean_improvement(self) -> float | None:
        """Return the direction-normalized mean paired improvement."""

        if not self.paired_values:
            return None
        return statistics.fmean(value.improvement for value in self.paired_values)

    @property
    def median_improvement(self) -> float | None:
        """Return the median direction-normalized paired improvement."""

        if not self.paired_values:
            return None
        return float(statistics.median(value.improvement for value in self.paired_values))

    @property
    def minimum_improvement(self) -> float | None:
        """Return the worst direction-normalized paired improvement."""

        if not self.paired_values:
            return None
        return min(value.improvement for value in self.paired_values)

    @property
    def maximum_improvement(self) -> float | None:
        """Return the best direction-normalized paired improvement."""

        if not self.paired_values:
            return None
        return max(value.improvement for value in self.paired_values)


@dataclass(frozen=True, slots=True)
class PairedEvaluationResult:
    """Complete local evidence for one baseline-versus-candidate evaluation."""

    request: EvaluationRequest
    case_results: Sequence[PairedCaseResult]

    def metric_summary(self, specification: MetricSpecification) -> MetricSummary:
        """Return paired metric values and direction-aware summary statistics."""

        paired_values: list[PairedMetricValue] = []
        missing_case_ids: list[str] = []
        for pair in self.case_results:
            if pair.baseline.error is not None or pair.candidate.error is not None:
                continue

            baseline = pair.baseline.metrics.get(specification.name)
            candidate = pair.candidate.metrics.get(specification.name)
            if baseline is None or candidate is None:
                missing_case_ids.append(pair.case_id)
                continue

            improvement = candidate - baseline
            if specification.direction == "lower_is_better":
                improvement = baseline - candidate
            paired_values.append(
                PairedMetricValue(
                    case_id=pair.case_id,
                    baseline=baseline,
                    candidate=candidate,
                    improvement=improvement,
                )
            )

        return MetricSummary(
            specification=specification,
            paired_values=paired_values,
            missing_case_ids=missing_case_ids,
        )

    def mean_metrics(self, variant_id: str) -> dict[str, float]:
        """Return mean metrics from successful runs of one variant."""

        is_baseline = variant_id == self.request.baseline.variant_id
        if not is_baseline and variant_id != self.request.candidate.variant_id:
            raise ValueError(f"unknown evaluation variant: {variant_id}")

        metric_values: dict[str, list[float]] = {}
        for pair in self.case_results:
            result = pair.baseline if is_baseline else pair.candidate
            if result.error is not None:
                continue
            for name, value in result.metrics.items():
                metric_values.setdefault(name, []).append(value)
        return {name: sum(values) / len(values) for name, values in metric_values.items()}

    def failed_case_ids(self, variant_id: str) -> tuple[str, ...]:
        """Return every case whose runner or metric failed for one variant."""

        is_baseline = variant_id == self.request.baseline.variant_id
        if not is_baseline and variant_id != self.request.candidate.variant_id:
            raise ValueError(f"unknown evaluation variant: {variant_id}")

        failures: list[str] = []
        for pair in self.case_results:
            result = pair.baseline if is_baseline else pair.candidate
            if result.error is not None:
                failures.append(pair.case_id)
        return tuple(failures)


@runtime_checkable
class EvaluationBackend(Protocol):
    """Evaluate a fixed baseline and candidate against the same dataset."""

    async def evaluate(
        self,
        request: EvaluationRequest,
        run_one: RunOne,
        metric: Metric,
    ) -> PairedEvaluationResult:
        ...


class LocalEvaluationBackend:
    """Run paired evaluation locally and retain failures as evidence instead of skipping them."""

    def __init__(self, *, evidence_sink: EvidenceSink | None = None) -> None:
        self._evidence_sink = evidence_sink

    async def evaluate(
        self,
        request: EvaluationRequest,
        run_one: RunOne,
        metric: Metric,
    ) -> PairedEvaluationResult:
        """Evaluate the baseline and candidate against every fixed case."""

        self._emit_started(request)

        case_results: list[PairedCaseResult] = []
        for case in request.dataset.cases:
            baseline = await self._evaluate_case(case, request.baseline, run_one, metric)
            candidate = await self._evaluate_case(case, request.candidate, run_one, metric)
            case_results.append(PairedCaseResult(case_id=case.case_id, baseline=baseline, candidate=candidate))

        result = PairedEvaluationResult(request=request, case_results=tuple(case_results))
        self._emit_completed(result)
        return result

    async def _evaluate_case(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        run_one: RunOne,
        metric: Metric,
    ) -> VariantCaseResult:
        try:
            output = await _await_value(run_one(case, variant))
            metrics = await _await_value(metric(case, output))
            return VariantCaseResult(variant_id=variant.variant_id, output=output, metrics=_validated_metrics(metrics))
        except Exception as error:
            logger.info("Evaluation case failed", exc_info=True)
            return VariantCaseResult(variant_id=variant.variant_id, error=f"{type(error).__name__}: {error}")

    def _emit_started(self, request: EvaluationRequest) -> None:
        context = replace(request.evidence_context, candidate_id=request.candidate.variant_id)
        event = EvidenceEvent(
            event_type="evaluation.started",
            context=context,
            attributes={
                "dataset_digest": request.dataset.manifest_digest,
                "case_count": len(request.dataset.cases),
                "baseline_variant_id": request.baseline.variant_id,
            },
        )
        self._emit(event)

    def _emit_completed(self, result: PairedEvaluationResult) -> None:
        request = result.request
        baseline_metrics = result.mean_metrics(request.baseline.variant_id)
        candidate_metrics = result.mean_metrics(request.candidate.variant_id)
        metrics = {f"baseline.{name}": value for name, value in baseline_metrics.items()}
        metrics.update({f"candidate.{name}": value for name, value in candidate_metrics.items()})

        context = replace(request.evidence_context, candidate_id=request.candidate.variant_id)
        event = EvidenceEvent(
            event_type="evaluation.completed",
            context=context,
            attributes={
                "dataset_digest": request.dataset.manifest_digest,
                "case_count": len(result.case_results),
                "baseline_failed_case_count": len(result.failed_case_ids(request.baseline.variant_id)),
                "candidate_failed_case_count": len(result.failed_case_ids(request.candidate.variant_id)),
                "baseline_variant_id": request.baseline.variant_id,
            },
            metrics=metrics,
        )
        self._emit(event)

    def _emit(self, event: EvidenceEvent) -> None:
        if self._evidence_sink is None:
            return
        try:
            self._evidence_sink.emit(event)
        except Exception:
            logger.warning("Evaluation evidence emission failed", exc_info=True)


__all__ = [
    "EvaluationBackend",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationRequest",
    "EvaluationVariant",
    "LocalEvaluationBackend",
    "Metric",
    "MetricDirection",
    "MetricSpecification",
    "MetricSummary",
    "PairedCaseResult",
    "PairedEvaluationResult",
    "PairedMetricValue",
    "RunOne",
    "VariantCaseResult",
]
