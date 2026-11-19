"""Risk routing policies for async guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from typing_extensions import override

from .context import ContextSnapshotV1, GuardrailEvent, ToolRiskTier
from .protocols import RiskRouter


@dataclass(slots=True, frozen=True)
class RiskRoutingDecision:
    """Routing decision for an event."""

    async_wait_ms: float = 0.0
    on_timeout: Literal["allow", "pause", "stop"] = "allow"
    required_async_rules: frozenset[str] = frozenset()
    skip_evaluation: bool = False


class DefaultRiskRouter(RiskRouter):
    """Route by tool risk tier and context signals."""

    def __init__(
        self,
        high_risk_wait_ms: float = 200.0,
        medium_risk_wait_ms: float = 100.0,
        critical_fail_closed: bool = True,
    ) -> None:
        """Route with the given waits and critical-risk behavior."""
        self._high_wait = high_risk_wait_ms
        self._medium_wait = medium_risk_wait_ms
        self._critical_fail_closed = critical_fail_closed

    def configure(
        self,
        *,
        high_risk_wait_ms: float | None = None,
        medium_risk_wait_ms: float | None = None,
        critical_fail_closed: bool | None = None,
    ) -> None:
        """Override the given routing settings, keeping the others."""
        if high_risk_wait_ms is not None:
            self._high_wait = high_risk_wait_ms
        if medium_risk_wait_ms is not None:
            self._medium_wait = medium_risk_wait_ms
        if critical_fail_closed is not None:
            self._critical_fail_closed = critical_fail_closed

    @override
    def route(
        self, event: GuardrailEvent, context_snapshot: ContextSnapshotV1
    ) -> RiskRoutingDecision:
        wait_ms = 0.0
        on_timeout: Literal["allow", "pause", "stop"] = "allow"
        required_rules: set[str] = set()

        if context_snapshot.current_tool:
            risk = context_snapshot.current_tool.risk_tier
            if risk == ToolRiskTier.CRITICAL:
                wait_ms = self._high_wait
                on_timeout = "stop" if self._critical_fail_closed else "pause"
            elif risk == ToolRiskTier.HIGH:
                wait_ms = self._high_wait
                on_timeout = "pause"
            elif risk == ToolRiskTier.MEDIUM:
                wait_ms = self._medium_wait

        if (
            event.event_type == "llm_before"
            and context_snapshot.max_tool_risk
            in (ToolRiskTier.CRITICAL, ToolRiskTier.HIGH)
        ):
            wait_ms = max(wait_ms, self._medium_wait)
            on_timeout = "pause" if on_timeout == "allow" else on_timeout

        if context_snapshot.requests_system_info:
            required_rules.add("hf-jailbreak")
            wait_ms = max(wait_ms, self._medium_wait)

        if context_snapshot.requests_capability_change:
            required_rules.add("hf-jailbreak")
            wait_ms = max(wait_ms, self._high_wait)
            on_timeout = "pause" if on_timeout == "allow" else on_timeout

        return RiskRoutingDecision(
            async_wait_ms=wait_ms,
            on_timeout=on_timeout,
            required_async_rules=frozenset(required_rules),
        )


__all__ = ["DefaultRiskRouter", "RiskRoutingDecision"]
