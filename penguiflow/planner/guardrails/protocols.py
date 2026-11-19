"""Guardrail extension protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
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
    ) -> GuardrailDecision | None:
        """Return a decision for ``event``, or ``None`` if the rule passes."""
        ...


class DecisionPolicy(Protocol):
    """Protocol for custom decision resolution."""

    def resolve(
        self,
        decisions: list[GuardrailDecision],
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision:
        """Resolve the rules' decisions into the single decision to apply."""
        ...


class RiskRouter(Protocol):
    """Protocol for dynamic risk-based routing."""

    def route(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> RiskRoutingDecision:
        """Choose the async-rule wait and what happens on timeout."""
        ...


__all__ = [
    "DecisionPolicy",
    "GuardrailRule",
    "RiskRouter",
]
