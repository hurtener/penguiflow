from __future__ import annotations

import pytest

from learning_control_plane.verification import (
    InvestigationVerification,
    SafeStepEvidence,
    VerificationCheck,
    score_final_answer,
)


def _passing_criteria() -> dict[str, VerificationCheck]:
    return {
        "factual_numerical_correctness": VerificationCheck(
            "factual_numerical_correctness", "passed", ("requested_numeric_values_correct",)
        ),
        "scope_correctness": VerificationCheck(
            "scope_correctness", "passed", ("requested_scope_present",)
        ),
        "evidence_grounding": VerificationCheck(
            "evidence_grounding", "passed", ("all_claims_match_expected_facts",)
        ),
        "completeness": VerificationCheck(
            "completeness", "passed", ("all_requested_metrics_answered",)
        ),
        "interpretation_correctness": VerificationCheck(
            "interpretation_correctness", "not_applicable", ("no_directional_interpretation",)
        ),
    }


def test_final_answer_rubric_renormalizes_criteria_that_do_not_apply() -> None:
    assessment = score_final_answer(_passing_criteria())

    assert assessment.score == 1.0
    assert assessment.passed is True
    assert assessment.assessment_ref.startswith("assessment:sha256:")


def test_hard_failure_rejects_an_answer_even_when_its_average_is_high() -> None:
    assessment = score_final_answer(_passing_criteria(), hard_failure_codes=("invented_evidence",))

    assert assessment.score == 1.0
    assert assessment.passed is False


def test_required_primary_criterion_must_be_fully_correct() -> None:
    criteria = _passing_criteria()
    criteria["factual_numerical_correctness"] = VerificationCheck(
        "factual_numerical_correctness", "partial", ("some_requested_numeric_values_incorrect",)
    )

    assessment = score_final_answer(criteria)

    assert assessment.passed is False


def test_safe_step_evidence_rejects_free_text_argument_values() -> None:
    with pytest.raises(ValueError, match="safe identifier"):
        SafeStepEvidence(
            step_index=0,
            node_name="aggregate_report",
            argument_facts={"campaign": "Private Customer Name"},
        )


def test_investigation_verification_requires_tool_and_answer_checks() -> None:
    step = SafeStepEvidence(
        step_index=0,
        node_name="aggregate_report",
        result_checks=(VerificationCheck("aggregate_output_shape", "passed"),),
    )

    verification = InvestigationVerification(
        step_evidence=(step,),
        final_answer=score_final_answer(_passing_criteria()),
    )

    assert verification.verified_success is True
    assert verification.record()["verified_success"] is True


def test_unchecked_steps_cannot_become_verified_successes() -> None:
    step = SafeStepEvidence(step_index=0, node_name="aggregate_report")

    verification = InvestigationVerification(
        step_evidence=(step,),
        final_answer=score_final_answer(_passing_criteria()),
    )

    assert verification.verified_success is False
