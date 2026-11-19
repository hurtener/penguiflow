"""Tests for guardrails gateway."""

from __future__ import annotations

import asyncio

import pytest
from typing_extensions import override

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
from penguiflow.planner.guardrails.protocols import GuardrailRule
from penguiflow.steering import InMemoryGuardInbox, SteeringGuardEvent


class _SyncAllowRule(GuardrailRule):
    rule_id = "allow"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.FAST
    enabled = True
    severity = GuardrailSeverity.LOW

    @override
    async def evaluate(
        self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1
    ) -> GuardrailDecision | None:
        return GuardrailDecision(
            action=GuardrailAction.ALLOW, rule_id=self.rule_id, reason="ok"
        )


class _AsyncStopRule(GuardrailRule):
    rule_id = "async-stop"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.DEEP
    enabled = True
    severity = GuardrailSeverity.HIGH

    @override
    async def evaluate(
        self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1
    ) -> GuardrailDecision | None:
        return GuardrailDecision(
            action=GuardrailAction.STOP, rule_id=self.rule_id, reason="blocked"
        )


class _SyncErrorRule(GuardrailRule):
    rule_id = "sync-error"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.FAST
    enabled = True
    severity = GuardrailSeverity.LOW

    @override
    async def evaluate(
        self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1
    ) -> GuardrailDecision | None:
        msg = "boom"
        raise RuntimeError(msg)


class _SyncSlowRule(GuardrailRule):
    rule_id = "sync-slow"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    cost = RuleCost.FAST
    enabled = True
    severity = GuardrailSeverity.LOW

    @override
    async def evaluate(
        self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1
    ) -> GuardrailDecision | None:
        await asyncio.sleep(0.05)
        return GuardrailDecision(
            action=GuardrailAction.ALLOW, rule_id=self.rule_id, reason="late"
        )


@pytest.mark.asyncio
async def test_gateway_default_allows_when_no_rules() -> None:
    """Gateway default allows when no rules."""
    registry = RuleRegistry()
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(registry))
    gateway = GuardrailGateway(registry=registry, guard_inbox=inbox)

    decision = await gateway.evaluate(
        GuardrailContext(run_id="run"), GuardrailEvent("llm_before", "run")
    )
    assert decision.action == GuardrailAction.ALLOW


@pytest.mark.asyncio
async def test_gateway_sync_fail_closed_on_rule_error() -> None:
    """Gateway sync fail closed on rule error."""
    registry = RuleRegistry()
    registry.register(_SyncErrorRule())
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(registry))
    gateway = GuardrailGateway(registry=registry, guard_inbox=inbox)
    gateway.config.sync_fail_open = False

    decision = await gateway.evaluate(
        GuardrailContext(run_id="run"), GuardrailEvent("llm_before", "run")
    )
    assert decision.action == GuardrailAction.STOP
    assert decision.rule_id == "sync-error"
    assert decision.stop is not None
    assert decision.stop.error_code == "GUARDRAIL_SYNC_ERROR"


@pytest.mark.asyncio
async def test_gateway_sync_fail_closed_on_rule_timeout() -> None:
    """Gateway sync fail closed on rule timeout."""
    registry = RuleRegistry()
    registry.register(_SyncSlowRule())
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(registry))
    gateway = GuardrailGateway(registry=registry, guard_inbox=inbox)
    gateway.config.sync_fail_open = False
    gateway.config.sync_timeout_ms = 1

    decision = await gateway.evaluate(
        GuardrailContext(run_id="run"), GuardrailEvent("llm_before", "run")
    )
    assert decision.action == GuardrailAction.STOP
    assert decision.rule_id == "sync-slow"
    assert decision.stop is not None
    assert decision.stop.error_code == "GUARDRAIL_SYNC_TIMEOUT"


@pytest.mark.asyncio
async def test_async_evaluator_filters_required_rules() -> None:
    """Async evaluator filters required rules."""
    registry = RuleRegistry()
    registry.register(_AsyncStopRule())
    evaluator = AsyncRuleEvaluator(registry)

    event = SteeringGuardEvent(
        event_type="llm_before",
        run_id="run",
        required_rules=frozenset({"none"}),
    )
    decisions = await evaluator.evaluate(event)
    assert decisions == []


def test_decision_policy_combines_effects() -> None:
    """Decision policy combines effects."""
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
