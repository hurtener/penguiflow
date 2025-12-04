"""Planning hints parsing and prompt rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _PlanningHints:
    ordering_hints: tuple[str, ...]
    parallel_groups: tuple[tuple[str, ...], ...]
    sequential_only: set[str]
    disallow_nodes: set[str]
    prefer_nodes: tuple[str, ...]
    max_parallel: int | None
    budget_hints: dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> _PlanningHints:
        if not payload:
            return cls((), (), set(), set(), (), None, {})
        ordering = tuple(str(item) for item in payload.get("ordering_hints", ()))
        parallel_groups = tuple(tuple(str(node) for node in group) for group in payload.get("parallel_groups", ()))
        sequential = {str(item) for item in payload.get("sequential_only", ())}
        disallow = {str(item) for item in payload.get("disallow_nodes", ())}
        prefer = tuple(str(item) for item in payload.get("prefer_nodes", ()))
        budget_raw = dict(payload.get("budget_hints", {}))
        max_parallel_value = payload.get("max_parallel")
        if not isinstance(max_parallel_value, int):
            candidate = budget_raw.get("max_parallel")
            max_parallel_value = candidate if isinstance(candidate, int) else None
        return cls(
            ordering_hints=ordering,
            parallel_groups=parallel_groups,
            sequential_only=sequential,
            disallow_nodes=disallow,
            prefer_nodes=prefer,
            max_parallel=max_parallel_value,
            budget_hints=budget_raw,
        )

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        constraints: list[str] = []
        if self.max_parallel is not None:
            constraints.append(f"max_parallel={self.max_parallel}")
        if self.sequential_only:
            constraints.append("sequential_only=" + ",".join(sorted(self.sequential_only)))
        if constraints:
            payload["constraints"] = "; ".join(constraints)
        if self.ordering_hints:
            payload["preferred_order"] = list(self.ordering_hints)
        if self.parallel_groups:
            payload["parallel_groups"] = [list(group) for group in self.parallel_groups]
        if self.disallow_nodes:
            payload["disallow_nodes"] = sorted(self.disallow_nodes)
        if self.prefer_nodes:
            payload["preferred_nodes"] = list(self.prefer_nodes)
        if self.budget_hints:
            payload["budget"] = dict(self.budget_hints)
        return payload

    def empty(self) -> bool:
        return not (
            self.ordering_hints
            or self.parallel_groups
            or self.sequential_only
            or self.disallow_nodes
            or self.prefer_nodes
            or self.max_parallel is not None
            or self.budget_hints
        )


__all__ = ["_PlanningHints"]
