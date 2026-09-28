"""A gain measured against a baseline that scored nothing is a whole gain, not an error."""

from __future__ import annotations

from learning_control_plane.control_plane.control_plane import (
    ConfidenceIntervalRequirement,
    MetricSpecification,
    _confidence_statistic,
)
from learning_control_plane.evaluation import PairedCaseResult, VariantCaseResult


def _pair(case_id: str, baseline: float, candidate: float) -> PairedCaseResult:
    return PairedCaseResult(
        case_id=case_id,
        baseline=VariantCaseResult(variant_id="baseline", metrics={"correct_answer_rate": baseline}),
        candidate=VariantCaseResult(variant_id="candidate", metrics={"correct_answer_rate": candidate}),
    )


def test_relative_statistics_are_defined_when_the_baseline_scored_nothing() -> None:
    pairs = [_pair("c1", 0.0, 1.0), _pair("c2", 0.0, 1.0)]
    specification = MetricSpecification("correct_answer_rate", direction="higher_is_better")

    regression = _confidence_statistic(
        pairs,
        ConfidenceIntervalRequirement("correct_answer_rate", "relative_mean_regression", maximum_upper_bound=0.1),
        specification,
    )
    improvement = _confidence_statistic(
        pairs,
        ConfidenceIntervalRequirement("correct_answer_rate", "relative_mean_improvement", minimum_lower_bound=0.1),
        specification,
    )
    unchanged = _confidence_statistic(
        [_pair("c1", 0.0, 0.0)],
        ConfidenceIntervalRequirement("correct_answer_rate", "relative_mean_improvement", minimum_lower_bound=0.0),
        specification,
    )

    assert regression == -1.0 and improvement == 1.0 and unchanged == 0.0
