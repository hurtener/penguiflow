from __future__ import annotations

import pytest

from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    ContextSnapshotV1,
    DefaultDecisionPolicy,
    GuardrailAction,
    GuardrailContext,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailGateway,
    GuardrailSeverity,
    RuleCost,
    RuleRegistry,
)
from penguiflow.steering import InMemoryGuardInbox, SteeringGuardEvent


class _SyncAllowRule:
    rule_id = "allow"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.FAST
    enabled = True
    severity = GuardrailSeverity.LOW

    async def evaluate(self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1) -> GuardrailDecision | None:
        return GuardrailDecision(action=GuardrailAction.ALLOW, rule_id=self.rule_id, reason="ok")


class _AsyncStopRule:
    rule_id = "async-stop"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.DEEP
    enabled = True
    severity = GuardrailSeverity.HIGH

    async def evaluate(self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1) -> GuardrailDecision | None:
        return GuardrailDecision(action=GuardrailAction.STOP, rule_id=self.rule_id, reason="blocked")


@pytest.mark.asyncio
async def test_gateway_default_allows_when_no_rules() -> None:
    registry = RuleRegistry()
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(registry))
    gateway = GuardrailGateway(registry=registry, guard_inbox=inbox)

    decision = await gateway.evaluate(GuardrailContext(run_id="run"), GuardrailEvent("llm_before", "run"))
    assert decision.action == GuardrailAction.ALLOW


@pytest.mark.asyncio
async def test_async_evaluator_filters_required_rules() -> None:
    registry = RuleRegistry()
    registry.register(_AsyncStopRule())
    evaluator = AsyncRuleEvaluator(registry)

    event = SteeringGuardEvent(event_type="llm_before", run_id="run", required_rules=frozenset({"none"}))
    decisions = await evaluator.evaluate(event)
    assert decisions == []


def test_decision_policy_combines_effects() -> None:
    policy = DefaultDecisionPolicy()
    event = GuardrailEvent("llm_before", "run")
    context = ContextSnapshotV1()
    decisions = [
        GuardrailDecision(
            action=GuardrailAction.REDACT,
            rule_id="a",
            reason="r1",
            effects=("flag",),
        ),
        GuardrailDecision(
            action=GuardrailAction.REDACT,
            rule_id="b",
            reason="r2",
            effects=("alert",),
        ),
    ]
    resolved = policy.resolve(decisions, event, context)
    assert resolved.action == GuardrailAction.REDACT
    assert set(resolved.effects) == {"flag", "alert"}
