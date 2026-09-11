"""Safe verification records and the versioned final-answer accuracy rubric."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

VERIFICATION_SCHEMA_VERSION = "learning_verification.v1"
FINAL_ANSWER_RUBRIC_VERSION = "final_answer_accuracy.v1"

CheckStatus = Literal["passed", "partial", "failed", "not_applicable"]
_STATUS_SCORES: dict[CheckStatus, float | None] = {
    "passed": 1.0,
    "partial": 0.5,
    "failed": 0.0,
    "not_applicable": None,
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def _safe_code(value: str, field_name: str) -> str:
    cleaned = _non_empty(value, field_name)
    if _SAFE_TOKEN.fullmatch(cleaned) is None:
        raise ValueError(f"{field_name} must be a safe identifier")
    return cleaned


def _safe_fact(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite")
        return value
    if isinstance(value, str):
        return _safe_code(value, field_name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_fact(item, field_name) for item in value]
    raise ValueError(f"{field_name} must contain only safe scalar facts")


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """One content-free correctness check and its machine-readable outcome."""

    check_id: str
    status: CheckStatus
    reason_codes: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _safe_code(self.check_id, "check_id"))
        if self.status not in _STATUS_SCORES:
            raise ValueError(f"unsupported check status: {self.status!r}")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(_safe_code(code, "reason_code") for code in self.reason_codes),
        )

    @property
    def score(self) -> float | None:
        """Return the normalized score, excluding checks that do not apply."""

        return _STATUS_SCORES[self.status]

    def record(self) -> dict[str, Any]:
        """Return the safe JSON record embedded in an investigation."""

        return {
            "check_id": self.check_id,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class SafeStepEvidence:
    """Allowlisted argument facts, decision reasons, and checks for one tool step."""

    step_index: int
    node_name: str
    argument_facts: Mapping[str, Any] = field(default_factory=dict)
    decision_reason_codes: Sequence[str] = ()
    result_checks: Sequence[VerificationCheck] = ()

    def __post_init__(self) -> None:
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        object.__setattr__(self, "node_name", _safe_code(self.node_name, "node_name"))
        safe_facts = {
            _safe_code(str(name), "argument fact name"): _safe_fact(value, f"argument_facts.{name}")
            for name, value in self.argument_facts.items()
        }
        object.__setattr__(self, "argument_facts", safe_facts)
        object.__setattr__(
            self,
            "decision_reason_codes",
            tuple(_safe_code(code, "decision_reason_code") for code in self.decision_reason_codes),
        )
        object.__setattr__(self, "result_checks", tuple(self.result_checks))

    @property
    def verified(self) -> bool:
        """Return whether at least one result check applies and none failed."""

        applicable = [check for check in self.result_checks if check.score is not None]
        return bool(applicable) and all(check.status == "passed" for check in applicable)

    def record(self) -> dict[str, Any]:
        """Return safe step evidence without raw arguments or tool output."""

        return {
            "step_index": self.step_index,
            "node_name": self.node_name,
            "argument_facts": dict(self.argument_facts),
            "decision_reason_codes": list(self.decision_reason_codes),
            "result_checks": [check.record() for check in self.result_checks],
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    """One weighted part of the final-answer accuracy rubric."""

    criterion_id: str
    weight: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _safe_code(self.criterion_id, "criterion_id"))
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError("rubric criterion weight must be positive and finite")


@dataclass(frozen=True, slots=True)
class FinalAnswerRubricV1:
    """Versioned policy for turning criterion checks into one accuracy decision."""

    criteria: Sequence[RubricCriterion] = (
        RubricCriterion("factual_numerical_correctness", 0.40),
        RubricCriterion("scope_correctness", 0.20),
        RubricCriterion("evidence_grounding", 0.15),
        RubricCriterion("completeness", 0.15),
        RubricCriterion("interpretation_correctness", 0.10),
    )
    minimum_score: float = 0.85
    required_full_score: Sequence[str] = ("factual_numerical_correctness",)
    hard_failure_codes: Sequence[str] = (
        "primary_result_incorrect",
        "wrong_scope",
        "invented_evidence",
        "false_data_unavailable",
        "policy_violation",
    )
    rubric_version: str = FINAL_ANSWER_RUBRIC_VERSION

    def __post_init__(self) -> None:
        criteria = tuple(self.criteria)
        criterion_ids = [criterion.criterion_id for criterion in criteria]
        if not criteria or len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("rubric criteria must be non-empty and unique")
        if not 0 <= self.minimum_score <= 1:
            raise ValueError("minimum_score must be between 0 and 1")
        object.__setattr__(self, "rubric_version", _safe_code(self.rubric_version, "rubric_version"))
        required = tuple(_safe_code(value, "required criterion") for value in self.required_full_score)
        if not set(required).issubset(criterion_ids):
            raise ValueError("required_full_score must name rubric criteria")
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(self, "required_full_score", required)
        object.__setattr__(
            self,
            "hard_failure_codes",
            tuple(_safe_code(value, "hard failure code") for value in self.hard_failure_codes),
        )


@dataclass(frozen=True, slots=True)
class CriterionAssessment:
    """Safe outcome for one rubric criterion."""

    criterion_id: str
    status: CheckStatus
    reason_codes: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _safe_code(self.criterion_id, "criterion_id"))
        if self.status not in _STATUS_SCORES:
            raise ValueError(f"unsupported criterion status: {self.status!r}")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(_safe_code(code, "reason_code") for code in self.reason_codes),
        )

    @property
    def score(self) -> float | None:
        """Return the normalized criterion score, or None when it does not apply."""

        return _STATUS_SCORES[self.status]

    def record(self) -> dict[str, Any]:
        """Return the content-free criterion result."""

        return {
            "criterion_id": self.criterion_id,
            "status": self.status,
            "score": self.score,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class FinalAnswerAssessment:
    """Weighted final-answer score with explicit hard failures and a stable reference."""

    rubric: FinalAnswerRubricV1
    criteria: Sequence[CriterionAssessment]
    hard_failure_codes: Sequence[str] = ()

    def __post_init__(self) -> None:
        criteria = tuple(self.criteria)
        supplied_ids = [criterion.criterion_id for criterion in criteria]
        expected_ids = [criterion.criterion_id for criterion in self.rubric.criteria]
        if supplied_ids != expected_ids:
            raise ValueError("criterion assessments must match rubric order exactly")
        unsupported_failures = set(self.hard_failure_codes) - set(self.rubric.hard_failure_codes)
        if unsupported_failures:
            raise ValueError(f"unsupported hard failure codes: {sorted(unsupported_failures)}")
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(
            self,
            "hard_failure_codes",
            tuple(_safe_code(code, "hard failure code") for code in self.hard_failure_codes),
        )

    @property
    def score(self) -> float:
        """Return the weighted average across criteria that apply to this task."""

        weights = {criterion.criterion_id: criterion.weight for criterion in self.rubric.criteria}
        weighted_score = 0.0
        applicable_weight = 0.0
        for criterion in self.criteria:
            criterion_score = criterion.score
            if criterion_score is None:
                continue
            weight = weights[criterion.criterion_id]
            weighted_score += weight * criterion_score
            applicable_weight += weight
        if applicable_weight == 0:
            return 0.0
        return weighted_score / applicable_weight

    @property
    def passed(self) -> bool:
        """Apply the score threshold, required criteria, and hard-failure gates."""

        if self.hard_failure_codes or self.score < self.rubric.minimum_score:
            return False
        scores = {criterion.criterion_id: criterion.score for criterion in self.criteria}
        return all(scores[criterion_id] == 1.0 for criterion_id in self.rubric.required_full_score)

    @property
    def assessment_ref(self) -> str:
        """Return a stable reference derived only from the safe assessment record."""

        encoded = json.dumps(self.record(), sort_keys=True, separators=(",", ":")).encode()
        return f"assessment:sha256:{hashlib.sha256(encoded).hexdigest()}"

    def record(self) -> dict[str, Any]:
        """Return the safe assessment without the evaluated answer or expected facts."""

        return {
            "rubric_version": self.rubric.rubric_version,
            "minimum_score": self.rubric.minimum_score,
            "score": self.score,
            "passed": self.passed,
            "hard_failure_codes": list(self.hard_failure_codes),
            "criteria": [criterion.record() for criterion in self.criteria],
        }


@dataclass(frozen=True, slots=True)
class InvestigationVerification:
    """Safe verification evidence produced inside a trusted agent integration."""

    step_evidence: Sequence[SafeStepEvidence]
    final_answer: FinalAnswerAssessment | None = None
    policy_compliance: VerificationCheck | None = None
    schema_version: str = field(default=VERIFICATION_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_evidence", tuple(self.step_evidence))

    @property
    def verified_success(self) -> bool:
        """Require checked tool steps, an accurate answer, and no known policy failure."""

        if not self.step_evidence or not all(step.verified for step in self.step_evidence):
            return False
        if self.final_answer is None or not self.final_answer.passed:
            return False
        return self.policy_compliance is None or self.policy_compliance.status == "passed"

    def record(self) -> dict[str, Any]:
        """Return the portable verification extension."""

        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "verified_success": self.verified_success,
            "step_evidence": [step.record() for step in self.step_evidence],
        }
        if self.final_answer is not None:
            record["final_answer"] = self.final_answer.record()
            record["assessment_ref"] = self.final_answer.assessment_ref
        if self.policy_compliance is not None:
            record["policy_compliance"] = self.policy_compliance.record()
        return record


def score_final_answer(
    criterion_assessments: Mapping[str, VerificationCheck],
    *,
    hard_failure_codes: Sequence[str] = (),
    rubric: FinalAnswerRubricV1 | None = None,
) -> FinalAnswerAssessment:
    """Score host-owned checks with the approved versioned rubric."""

    selected_rubric = rubric or FinalAnswerRubricV1()
    expected_ids = [criterion.criterion_id for criterion in selected_rubric.criteria]
    missing = [criterion_id for criterion_id in expected_ids if criterion_id not in criterion_assessments]
    extra = sorted(set(criterion_assessments) - set(expected_ids))
    if missing or extra:
        raise ValueError(f"criterion checks do not match rubric; missing={missing}, extra={extra}")

    criteria = tuple(
        CriterionAssessment(
            criterion_id=criterion_id,
            status=criterion_assessments[criterion_id].status,
            reason_codes=criterion_assessments[criterion_id].reason_codes,
        )
        for criterion_id in expected_ids
    )
    return FinalAnswerAssessment(
        rubric=selected_rubric,
        criteria=criteria,
        hard_failure_codes=hard_failure_codes,
    )


__all__ = [
    "CheckStatus",
    "CriterionAssessment",
    "FINAL_ANSWER_RUBRIC_VERSION",
    "FinalAnswerAssessment",
    "FinalAnswerRubricV1",
    "InvestigationVerification",
    "RubricCriterion",
    "SafeStepEvidence",
    "VERIFICATION_SCHEMA_VERSION",
    "VerificationCheck",
    "score_final_answer",
]
