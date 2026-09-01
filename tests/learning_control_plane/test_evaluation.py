from __future__ import annotations

import pytest

from learning_control_plane.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRequest,
    EvaluationVariant,
    LocalEvaluationBackend,
    MetricSpecification,
)
from learning_control_plane.evidence import EvidenceContext, EvidenceEvent


class _EvidenceRecorder:
    def __init__(self) -> None:
        self.events: list[EvidenceEvent] = []

    def emit(self, event: EvidenceEvent) -> bool:
        self.events.append(event)
        return True


def _request() -> EvaluationRequest:
    dataset = EvaluationDataset(
        dataset_id="support-heldout",
        version="v1",
        cases=(
            EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),
            EvaluationCase(case_id="case-2", inputs={"question": "two"}, expected="correct"),
        ),
    )
    context = EvidenceContext(
        agent_id="support-agent",
        deployment_digest="sha256:agent-v1",
        evaluation_id="evaluation-1",
        dataset_version=dataset.version,
        metric_version="quality-v1",
    )
    return EvaluationRequest(
        evaluation_id="evaluation-1",
        evidence_context=context,
        dataset=dataset,
        baseline=EvaluationVariant(variant_id="baseline"),
        candidate=EvaluationVariant(variant_id="candidate-skill", advisory_skill="Use the verified resolution steps."),
    )


@pytest.mark.asyncio
async def test_local_backend_compares_the_same_cases_and_emits_aggregate_evidence() -> None:
    request = _request()
    evidence = _EvidenceRecorder()
    calls: list[tuple[str, str, str | None]] = []

    async def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        calls.append((case.case_id, variant.variant_id, variant.advisory_skill))
        return "correct" if variant.advisory_skill else "incorrect"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": 1.0 if output == case.expected else 0.0}

    result = await LocalEvaluationBackend(evidence_sink=evidence).evaluate(request, run_one, score)

    assert [pair.case_id for pair in result.case_results] == ["case-1", "case-2"]
    assert calls == [
        ("case-1", "baseline", None),
        ("case-1", "candidate-skill", "Use the verified resolution steps."),
        ("case-2", "baseline", None),
        ("case-2", "candidate-skill", "Use the verified resolution steps."),
    ]
    assert result.mean_metrics("baseline") == {"quality_score": 0.0}
    assert result.mean_metrics("candidate-skill") == {"quality_score": 1.0}
    assert result.failed_case_ids("baseline") == ()
    assert [event.event_type for event in evidence.events] == ["evaluation.started", "evaluation.completed"]
    assert evidence.events[1].metrics == {
        "baseline.quality_score": 0.0,
        "candidate.quality_score": 1.0,
    }
    assert evidence.events[1].attributes["dataset_digest"] == request.dataset.manifest_digest


@pytest.mark.asyncio
async def test_local_backend_keeps_a_failed_candidate_case_in_the_result() -> None:
    request = _request()

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        if case.case_id == "case-2" and variant.variant_id == "candidate-skill":
            raise RuntimeError("tool unavailable")
        return "correct"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": 1.0}

    result = await LocalEvaluationBackend().evaluate(request, run_one, score)

    assert len(result.case_results) == 2
    assert result.case_results[1].candidate.error == "RuntimeError: tool unavailable"
    assert result.failed_case_ids("candidate-skill") == ("case-2",)
    assert result.mean_metrics("candidate-skill") == {"quality_score": 1.0}


@pytest.mark.asyncio
async def test_local_backend_rejects_an_unknown_variant_in_result_queries() -> None:
    request = _request()

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "correct"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        return {"quality_score": 1.0}

    result = await LocalEvaluationBackend().evaluate(request, run_one, score)

    with pytest.raises(ValueError, match="unknown evaluation variant"):
        result.mean_metrics("unknown")


def test_dataset_digest_changes_when_a_fixed_case_changes() -> None:
    first = EvaluationDataset(
        dataset_id="support-heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"question": "one"}, expected="correct"),),
    )
    second = EvaluationDataset(
        dataset_id="support-heldout",
        version="v1",
        cases=(EvaluationCase(case_id="case-1", inputs={"question": "changed"}, expected="correct"),),
    )

    assert first.manifest_digest != second.manifest_digest


def test_request_rejects_a_candidate_without_an_advisory_skill() -> None:
    request = _request()

    with pytest.raises(ValueError, match="candidate must include"):
        EvaluationRequest(
            evaluation_id=request.evaluation_id,
            evidence_context=request.evidence_context,
            dataset=request.dataset,
            baseline=request.baseline,
            candidate=EvaluationVariant(variant_id="candidate-without-skill"),
        )


@pytest.mark.asyncio
async def test_metric_summary_retains_paired_values_and_normalizes_lower_latency() -> None:
    request = _request()

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return "candidate" if variant.advisory_skill else "baseline"

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        latency_by_case = {"case-1": 120.0, "case-2": 180.0}
        baseline_latency = latency_by_case[case.case_id]
        return {"latency_ms": baseline_latency - 40.0 if output == "candidate" else baseline_latency}

    result = await LocalEvaluationBackend().evaluate(request, run_one, score)
    summary = result.metric_summary(MetricSpecification("latency_ms", direction="lower_is_better"))

    assert [(value.case_id, value.improvement) for value in summary.paired_values] == [
        ("case-1", 40.0),
        ("case-2", 40.0),
    ]
    assert summary.baseline_mean == 150.0
    assert summary.candidate_mean == 110.0
    assert summary.mean_improvement == 40.0
    assert summary.median_improvement == 40.0
    assert summary.minimum_improvement == 40.0
    assert summary.maximum_improvement == 40.0


def test_metric_specification_rejects_an_unknown_direction() -> None:
    with pytest.raises(ValueError, match="metric direction"):
        MetricSpecification("quality_score", direction="sideways")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_metric_summary_identifies_successful_pairs_missing_a_metric() -> None:
    request = _request()

    def run_one(case: EvaluationCase, variant: EvaluationVariant) -> str:
        return variant.variant_id

    def score(case: EvaluationCase, output: str) -> dict[str, float]:
        if case.case_id == "case-2" and output == "candidate-skill":
            return {}
        return {"cost_usd": 0.20}

    result = await LocalEvaluationBackend().evaluate(request, run_one, score)
    summary = result.metric_summary(MetricSpecification("cost_usd", direction="lower_is_better"))

    assert [value.case_id for value in summary.paired_values] == ["case-1"]
    assert summary.missing_case_ids == ("case-2",)
