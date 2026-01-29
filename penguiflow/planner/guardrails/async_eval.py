"""Async evaluator for deep guardrail rules."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .context import ContextSnapshotV1, GuardrailEvent
from .models import GuardrailDecision
from .protocols import GuardrailRule
from .registry import RuleRegistry

if TYPE_CHECKING:
    from penguiflow.steering.guard_inbox import SteeringGuardEvent


class AsyncRuleEvaluator:
    """Evaluates deep (async) rules for a submitted event."""

    def __init__(self, registry: RuleRegistry) -> None:
        self._registry = registry

    async def evaluate(self, event: SteeringGuardEvent) -> list[GuardrailDecision]:
        rules = self._registry.get_async_rules(event.event_type)
        if event.required_rules:
            rules = [rule for rule in rules if rule.rule_id in event.required_rules]
        if not rules:
            return []

        context_snapshot = (
            ContextSnapshotV1(**event.context_snapshot)
            if event.context_snapshot
            else ContextSnapshotV1()
        )
        guardrail_event = GuardrailEvent(
            event_type=event.event_type,
            run_id=event.run_id,
            text_content=event.payload.get("text_content"),
            tool_name=event.payload.get("tool_name"),
            tool_args=event.payload.get("tool_args"),
            payload=event.payload,
        )

        async def safe_eval(rule: GuardrailRule) -> GuardrailDecision | None:
            try:
                decision = await rule.evaluate(guardrail_event, context_snapshot)
                if decision:
                    decision.was_sync = False
                return decision
            except Exception:
                return None

        results = await asyncio.gather(*[safe_eval(rule) for rule in rules])
        return [decision for decision in results if decision is not None]


__all__ = ["AsyncRuleEvaluator"]
