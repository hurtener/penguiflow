"""Guardrail extension protocols."""

from __future__ import annotations

from typing import Protocol

from .context import ContextSnapshotV1, GuardrailEvent
from .models import GuardrailDecision, GuardrailSeverity, RuleCost
from .routing import RiskRoutingDecision


class GuardrailRule(Protocol):
    """Protocol that all guardrail rules must implement."""

    rule_id: str
    version: str
    supports_event_types: frozenset[str]
    cost: RuleCost
    enabled: bool
    severity: GuardrailSeverity

    async def evaluate(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision | None: ...


class DecisionPolicy(Protocol):
    """Protocol for custom decision resolution."""

    def resolve(
        self,
        decisions: list[GuardrailDecision],
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision: ...


class RiskRouter(Protocol):
    """Protocol for dynamic risk-based routing."""

    def route(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> RiskRoutingDecision: ...

__all__ = [
    "DecisionPolicy",
    "GuardrailRule",
    "RiskRouter",
]
