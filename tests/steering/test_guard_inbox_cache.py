"""Tests for guard inbox cache."""

from __future__ import annotations

import pytest

from penguiflow.planner.guardrails import AsyncRuleEvaluator, RuleRegistry
from penguiflow.steering.guard_inbox import (
    InMemoryGuardInbox,
    SteeringGuardEvent,
)


@pytest.mark.asyncio
async def test_guard_inbox_await_response_cached_and_unknown() -> None:
    """Guard inbox await response cached and unknown."""
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(RuleRegistry()))
    correlation_id = await inbox.submit(
        SteeringGuardEvent(correlation_id="c1", run_id="run")
    )
    first = await inbox.await_response(correlation_id, timeout_s=1.0)
    # The evaluated response is now cached and returned again.
    assert await inbox.await_response("c1", timeout_s=0.01) == first

    with pytest.raises(KeyError, match="Unknown correlation_id"):
        await inbox.await_response("missing", timeout_s=0.01)
