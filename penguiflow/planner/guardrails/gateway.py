"""Unified guardrail gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from penguiflow.steering.guard_inbox import SteeringGuardInbox
from .context import ContextSnapshotBuilder, ContextSnapshotV1, GuardrailContext, GuardrailEvent
from .models import GuardrailAction, GuardrailDecision, PauseSpec, RedactionSpec, StopSpec
from .protocols import DecisionPolicy, GuardrailRule, RiskRouter
from .registry import RuleRegistry
from .routing import DefaultRiskRouter, RiskRoutingDecision


@dataclass
class GatewayConfig:
    """Gateway configuration."""

    mode: Literal["shadow", "enforce"] = "enforce"
    sync_timeout_ms: float = 15.0
    sync_parallel: bool = True
    async_enabled: bool = True
    sync_fail_open: bool = False
    async_fail_open: bool = True


class DefaultDecisionPolicy:
    """Priority-based resolution with redaction combining and effect merging."""

    def __init__(self, combine_effects: bool = True) -> None:
        self._combine_effects = combine_effects

    def resolve(
        self,
        decisions: list[GuardrailDecision],
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> GuardrailDecision:
        if not decisions:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__default__",
                reason="No rules triggered",
            )

        priority = {
            GuardrailAction.STOP: 5,
            GuardrailAction.PAUSE: 4,
            GuardrailAction.RETRY: 3,
            GuardrailAction.REDACT: 2,
            GuardrailAction.ALLOW: 1,
        }
        sorted_decisions = sorted(
            decisions,
            key=lambda decision: (
                priority.get(decision.action, 0),
                decision.confidence or 0,
            ),
            reverse=True,
        )
        winner = sorted_decisions[0]

        if winner.action == GuardrailAction.REDACT:
            all_redactions: list[RedactionSpec] = []
            for decision in sorted_decisions:
                if decision.redactions:
                    all_redactions.extend(decision.redactions)
            if all_redactions:
                winner = GuardrailDecision(
                    action=GuardrailAction.REDACT,
                    rule_id=winner.rule_id,
                    reason=f"Combined {len(all_redactions)} redactions",
                    severity=winner.severity,
                    redactions=tuple(all_redactions),
                    effects=winner.effects,
                )

        if self._combine_effects:
            all_effects: set[str] = set()
            for decision in sorted_decisions:
                all_effects.update(decision.effects)
            if all_effects and all_effects != set(winner.effects):
                winner = winner.with_effects(*all_effects)

        return winner


@dataclass
class GuardrailGateway:
    """Unified gateway with extensibility hooks."""

    registry: RuleRegistry
    guard_inbox: SteeringGuardInbox
    config: GatewayConfig = field(default_factory=GatewayConfig)
    decision_policy: DecisionPolicy = field(default_factory=DefaultDecisionPolicy)
    risk_router: RiskRouter = field(default_factory=DefaultRiskRouter)
    context_builder: ContextSnapshotBuilder = field(default_factory=ContextSnapshotBuilder)

    async def evaluate(self, ctx: GuardrailContext, event: GuardrailEvent) -> GuardrailDecision:
        context_snapshot = self.context_builder.build(event, ctx)
        routing = self.risk_router.route(event, context_snapshot)

        if routing.skip_evaluation:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__skip__",
                reason="Skipped by risk router",
            )

        sync_decisions = await self._evaluate_sync(event, context_snapshot)
        sync_stops = [d for d in sync_decisions if d.action == GuardrailAction.STOP]
        if sync_stops:
            final = self.decision_policy.resolve(sync_stops, event, context_snapshot)
            self._process_effects(final, ctx)
            return final

        correlation_id: str | None = None
        if self.config.async_enabled:
            correlation_id = await self._submit_async(event, context_snapshot, routing)

        async_decisions: list[GuardrailDecision] = []
        if correlation_id and routing.async_wait_ms > 0:
            async_decisions = await self._wait_for_async(
                correlation_id,
                timeout_ms=routing.async_wait_ms,
                on_timeout=routing.on_timeout,
            )

        all_decisions = sync_decisions + async_decisions
        if not all_decisions:
            return GuardrailDecision(
                action=GuardrailAction.ALLOW,
                rule_id="__default__",
                reason="No rules triggered",
            )

        final = self.decision_policy.resolve(all_decisions, event, context_snapshot)
        self._process_effects(final, ctx)
        return final

    async def _evaluate_sync(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
    ) -> list[GuardrailDecision]:
        rules = self.registry.get_sync_rules(event.event_type)
        if not rules:
            return []

        async def safe_eval(rule: GuardrailRule) -> GuardrailDecision | None:
            try:
                return await asyncio.wait_for(
                    rule.evaluate(event, context_snapshot),
                    timeout=self.config.sync_timeout_ms / 1000,
                )
            except Exception:
                return None

        if self.config.sync_parallel:
            results = await asyncio.gather(*[safe_eval(rule) for rule in rules])
        else:
            results = [await safe_eval(rule) for rule in rules]

        return [decision for decision in results if decision is not None]

    async def _submit_async(
        self,
        event: GuardrailEvent,
        context_snapshot: ContextSnapshotV1,
        routing: RiskRoutingDecision,
    ) -> str:
        from penguiflow.steering.guard_inbox import SteeringGuardEvent

        steering_event = SteeringGuardEvent(
            event_type=event.event_type,
            run_id=event.run_id,
            payload={
                "text_content": event.text_content,
                "tool_name": event.tool_name,
                "tool_args": event.tool_args,
                **event.payload,
            },
            context_snapshot=context_snapshot.to_dict(),
            required_rules=routing.required_async_rules,
        )
        return await self.guard_inbox.submit(steering_event)

    async def _wait_for_async(
        self,
        correlation_id: str,
        timeout_ms: float,
        on_timeout: Literal["allow", "pause", "stop"],
    ) -> list[GuardrailDecision]:
        try:
            response = await self.guard_inbox.await_response(
                correlation_id,
                timeout_s=timeout_ms / 1000,
            )
            for decision in response.decisions:
                decision.was_sync = False
            return response.decisions
        except TimeoutError:
            if on_timeout == "stop":
                return [
                    GuardrailDecision(
                        action=GuardrailAction.STOP,
                        rule_id="__timeout__",
                        reason="Async evaluation timed out (fail-closed)",
                        stop=StopSpec(error_code="GUARDRAIL_TIMEOUT"),
                    )
                ]
            if on_timeout == "pause":
                return [
                    GuardrailDecision(
                        action=GuardrailAction.PAUSE,
                        rule_id="__timeout__",
                        reason="Async evaluation timed out",
                        pause=PauseSpec(prompt="Safety check timed out"),
                    )
                ]
            return []

    def _process_effects(self, decision: GuardrailDecision, ctx: GuardrailContext) -> None:
        for effect in decision.effects:
            if effect == "increment_strike":
                category = decision.rule_id.split("-")[0]
                ctx.strike_counts[category] = ctx.strike_counts.get(category, 0) + 1
            elif effect == "flag_trajectory":
                pass
            elif effect == "emit_alert":
                pass


__all__ = ["DefaultDecisionPolicy", "GatewayConfig", "GuardrailGateway"]
