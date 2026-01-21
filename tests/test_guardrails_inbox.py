from __future__ import annotations

import pytest

from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailSeverity,
    RuleCost,
    RuleRegistry,
)
from penguiflow.steering import InMemoryGuardInbox, SteeringGuardEvent


class _AsyncRedactRule:
    rule_id = "async-redact"
    version = "1"
    supports_event_types = frozenset({"tool_call_result"})
    cost = RuleCost.DEEP
    enabled = True
    severity = GuardrailSeverity.MEDIUM

    async def evaluate(self, event: GuardrailEvent, context_snapshot: object) -> GuardrailDecision | None:
        return GuardrailDecision(
            action=GuardrailAction.REDACT,
            rule_id=self.rule_id,
            reason="redact",
        )


@pytest.mark.asyncio
async def test_inmemory_guard_inbox_roundtrip() -> None:
    registry = RuleRegistry()
    registry.register(_AsyncRedactRule())
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(registry))

    event = SteeringGuardEvent(event_type="tool_call_result", run_id="run")
    correlation_id = await inbox.submit(event)
    response = await inbox.await_response(correlation_id, timeout_s=1.0)

    assert response.correlation_id == correlation_id
    assert response.decisions
    assert response.decisions[0].action == GuardrailAction.REDACT

    drained = inbox.drain_responses("run")
    assert drained
