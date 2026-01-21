"""Registry for guardrail rules."""

from __future__ import annotations

from .models import RuleCost
from .protocols import GuardrailRule


class RuleRegistry:
    """Registry for guardrail rules."""

    def __init__(self) -> None:
        self._sync_rules: list[GuardrailRule] = []
        self._async_rules: list[GuardrailRule] = []

    def register(self, rule: GuardrailRule) -> None:
        """Register a rule based on its cost."""

        if rule.cost == RuleCost.FAST:
            self._sync_rules.append(rule)
        else:
            self._async_rules.append(rule)

    def register_sync(self, rule: GuardrailRule) -> None:
        """Force-register a rule as synchronous."""
        self._sync_rules.append(rule)

    def get_sync_rules(self, event_type: str) -> list[GuardrailRule]:
        """Get sync rules applicable to an event type."""

        return [rule for rule in self._sync_rules if rule.enabled and event_type in rule.supports_event_types]

    def get_async_rules(self, event_type: str) -> list[GuardrailRule]:
        """Get async rules applicable to an event type."""

        return [rule for rule in self._async_rules if rule.enabled and event_type in rule.supports_event_types]

    def all_rules(self) -> list[GuardrailRule]:
        """Return all registered rules (sync + async)."""

        return [*self._sync_rules, *self._async_rules]


__all__ = ["RuleRegistry"]
