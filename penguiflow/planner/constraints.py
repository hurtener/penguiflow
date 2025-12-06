"""Budget and deadline tracking for planner runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import prompts


class _ConstraintTracker:
    __slots__ = (
        "_deadline_at",
        "_hop_budget",
        "_hops_used",
        "_time_source",
        "deadline_triggered",
        "hop_exhausted",
    )

    def __init__(
        self,
        *,
        deadline_s: float | None,
        hop_budget: int | None,
        time_source: Callable[[], float],
    ) -> None:
        now = time_source()
        self._deadline_at = now + deadline_s if deadline_s is not None else None
        self._hop_budget = hop_budget
        self._hops_used = 0
        self._time_source = time_source
        self.deadline_triggered = False
        self.hop_exhausted = hop_budget == 0 and hop_budget is not None

    def check_deadline(self) -> str | None:
        if self._deadline_at is None:
            return None
        if self._time_source() >= self._deadline_at:
            self.deadline_triggered = True
            return prompts.render_deadline_exhausted()
        return None

    def has_budget_for_next_tool(self) -> bool:
        if self._hop_budget is None:
            return True
        return self._hops_used < self._hop_budget

    def record_hop(self) -> None:
        if self._hop_budget is None:
            return
        self._hops_used += 1
        if self._hops_used >= self._hop_budget:
            self.hop_exhausted = True

    def snapshot(self) -> dict[str, Any]:
        remaining: float | None = None
        if self._deadline_at is not None:
            remaining = max(self._deadline_at - self._time_source(), 0.0)
        return {
            "deadline_at": self._deadline_at,
            "deadline_remaining_s": remaining,
            "hop_budget": self._hop_budget,
            "hops_used": self._hops_used,
            "deadline_triggered": self.deadline_triggered,
            "hop_exhausted": self.hop_exhausted,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any], *, time_source: Callable[[], float]) -> _ConstraintTracker:
        deadline_remaining = snapshot.get("deadline_remaining_s")
        hop_budget = snapshot.get("hop_budget")
        tracker = cls(
            deadline_s=deadline_remaining,
            hop_budget=hop_budget,
            time_source=time_source,
        )
        tracker._hops_used = int(snapshot.get("hops_used", 0))
        tracker._hop_budget = hop_budget
        if deadline_remaining is None and snapshot.get("deadline_at") is None:
            tracker._deadline_at = None
        elif deadline_remaining is not None:
            tracker._deadline_at = time_source() + max(float(deadline_remaining), 0.0)
        else:
            tracker._deadline_at = snapshot.get("deadline_at")
        tracker.deadline_triggered = bool(snapshot.get("deadline_triggered", False))
        tracker.hop_exhausted = bool(snapshot.get("hop_exhausted", False))
        if tracker._hop_budget is not None and tracker._hops_used >= tracker._hop_budget:
            tracker.hop_exhausted = True
        return tracker


class _CostTracker:
    """Track LLM costs across a planning session."""

    _total_cost_usd: float = 0.0
    _main_llm_calls: int = 0
    _reflection_llm_calls: int = 0
    _summarizer_llm_calls: int = 0

    def record_main_call(self, cost: float) -> None:
        self._total_cost_usd += cost
        self._main_llm_calls += 1

    def record_reflection_call(self, cost: float) -> None:
        self._total_cost_usd += cost
        self._reflection_llm_calls += 1

    def record_summarizer_call(self, cost: float) -> None:
        self._total_cost_usd += cost
        self._summarizer_llm_calls += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_cost_usd": round(self._total_cost_usd, 6),
            "main_llm_calls": self._main_llm_calls,
            "reflection_llm_calls": self._reflection_llm_calls,
            "summarizer_llm_calls": self._summarizer_llm_calls,
        }


__all__ = ["_ConstraintTracker", "_CostTracker"]
