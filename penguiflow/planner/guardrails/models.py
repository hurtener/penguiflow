"""Core guardrail data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from uuid import uuid4


class GuardrailAction(str, Enum):
    """Guardrail decision actions."""

    ALLOW = "ALLOW"
    REDACT = "REDACT"
    RETRY = "RETRY"
    PAUSE = "PAUSE"
    STOP = "STOP"


class GuardrailSeverity(str, Enum):
    """Decision severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleCost(str, Enum):
    """Rule execution cost classification."""

    FAST = "fast"
    DEEP = "deep"


@dataclass(slots=True, frozen=True)
class RedactionSpec:
    """Specification for content redaction."""

    path: str
    replacement: str = "[REDACTED]"
    entity_type: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(slots=True, frozen=True)
class RetrySpec:
    """Specification for LLM/tool retry with corrective guidance."""

    max_attempts: int = 2
    corrective_message: str = ""


@dataclass(slots=True, frozen=True)
class PauseSpec:
    """Specification for human-in-the-loop pause."""

    scope: Literal["run", "step", "tool_call"] = "tool_call"
    approver_roles: tuple[str, ...] = ("admin",)
    prompt: str = ""
    timeout_s: float | None = 300.0


@dataclass(slots=True, frozen=True)
class StopSpec:
    """Specification for run termination."""

    error_code: str = "GUARDRAIL_STOP"
    user_message: str = "I'm unable to complete that request."
    internal_reason: str = ""


@dataclass(slots=True)
class GuardrailDecision:
    """Decision returned by guardrail evaluation."""

    action: GuardrailAction
    rule_id: str
    reason: str
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str | None = None
    severity: GuardrailSeverity = GuardrailSeverity.MEDIUM
    confidence: float | None = None
    was_sync: bool = True
    effects: tuple[str, ...] = ()
    redactions: tuple[RedactionSpec, ...] | None = None
    retry: RetrySpec | None = None
    pause: PauseSpec | None = None
    stop: StopSpec | None = None
    classifier_result: dict[str, Any] | None = None

    def with_effects(self, *new_effects: str) -> GuardrailDecision:
        """Return a new decision with additional effects."""

        return GuardrailDecision(
            action=self.action,
            rule_id=self.rule_id,
            reason=self.reason,
            decision_id=self.decision_id,
            correlation_id=self.correlation_id,
            severity=self.severity,
            confidence=self.confidence,
            was_sync=self.was_sync,
            effects=self.effects + new_effects,
            redactions=self.redactions,
            retry=self.retry,
            pause=self.pause,
            stop=self.stop,
            classifier_result=self.classifier_result,
        )


__all__ = [
    "GuardrailAction",
    "GuardrailDecision",
    "GuardrailSeverity",
    "PauseSpec",
    "RedactionSpec",
    "RetrySpec",
    "RuleCost",
    "StopSpec",
]
