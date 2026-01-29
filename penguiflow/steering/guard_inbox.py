"""Async guardrail inbox implementations."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from penguiflow.planner.guardrails.async_eval import AsyncRuleEvaluator
from penguiflow.planner.guardrails.models import GuardrailDecision


class SteeringGuardInbox(ABC):
    """Interface for async guardrail evaluation."""

    @abstractmethod
    async def submit(self, event: SteeringGuardEvent) -> str:
        """Submit event for async evaluation. Returns correlation_id."""
        raise NotImplementedError

    @abstractmethod
    async def await_response(self, correlation_id: str, timeout_s: float) -> SteeringGuardResponse:
        """Wait for response. Raises TimeoutError if exceeded."""
        raise NotImplementedError

    @abstractmethod
    def drain_responses(self, run_id: str) -> list[SteeringGuardResponse]:
        """Drain pending responses for late decision handling."""
        raise NotImplementedError


@dataclass(slots=True)
class SteeringGuardEvent:
    """Event submitted for async evaluation."""

    event_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = ""
    run_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    required_rules: frozenset[str] = frozenset()


@dataclass(slots=True)
class SteeringGuardResponse:
    """Response from async evaluation."""

    correlation_id: str
    decisions: list[GuardrailDecision] = field(default_factory=list)
    error: str | None = None


class InMemoryGuardInbox(SteeringGuardInbox):
    """In-process async evaluation."""

    def __init__(self, evaluator: AsyncRuleEvaluator) -> None:
        self._evaluator = evaluator
        self._pending: dict[str, asyncio.Future[SteeringGuardResponse]] = {}
        self._completed: dict[str, SteeringGuardResponse] = {}

    async def submit(self, event: SteeringGuardEvent) -> str:
        future: asyncio.Future[SteeringGuardResponse] = asyncio.Future()
        self._pending[event.correlation_id] = future
        asyncio.create_task(self._evaluate(event, future))
        return event.correlation_id

    async def _evaluate(
        self,
        event: SteeringGuardEvent,
        future: asyncio.Future[SteeringGuardResponse],
    ) -> None:
        try:
            decisions = await self._evaluator.evaluate(event)
            response = SteeringGuardResponse(
                correlation_id=event.correlation_id,
                decisions=decisions,
            )
        except Exception as exc:  # pragma: no cover - defensive
            response = SteeringGuardResponse(
                correlation_id=event.correlation_id,
                error=str(exc),
            )

        self._completed[event.correlation_id] = response
        self._pending.pop(event.correlation_id, None)

        if not future.done():
            future.set_result(response)

    async def await_response(self, correlation_id: str, timeout_s: float) -> SteeringGuardResponse:
        if correlation_id in self._completed:
            return self._completed[correlation_id]

        future = self._pending.get(correlation_id)
        if future is None:
            raise KeyError(f"Unknown correlation_id: {correlation_id}")

        return await asyncio.wait_for(future, timeout=timeout_s)

    def drain_responses(self, run_id: str) -> list[SteeringGuardResponse]:
        # NOTE: Simple implementation drains globally.
        responses = list(self._completed.values())
        self._completed.clear()
        return responses
