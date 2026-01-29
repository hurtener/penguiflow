from __future__ import annotations

import pytest

from penguiflow.steering.guard_inbox import InMemoryGuardInbox, SteeringGuardResponse


class _NoopEvaluator:
    async def evaluate(self, event):  # noqa: ANN001 - test stub
        _ = event
        return []


@pytest.mark.asyncio
async def test_guard_inbox_await_response_cached_and_unknown() -> None:
    inbox = InMemoryGuardInbox(_NoopEvaluator())  # type: ignore[arg-type]
    inbox._completed["c1"] = SteeringGuardResponse(correlation_id="c1")  # noqa: SLF001 - test-only access
    assert await inbox.await_response("c1", timeout_s=0.01) == inbox._completed["c1"]  # noqa: SLF001

    with pytest.raises(KeyError, match="Unknown correlation_id"):
        await inbox.await_response("missing", timeout_s=0.01)

