"""Pause record helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import PlannerPause
    from .trajectory import Trajectory


@dataclass(slots=True)
class _PauseRecord:
    trajectory: Trajectory
    reason: str
    payload: dict[str, Any]
    constraints: dict[str, Any] | None = None
    tool_context: dict[str, Any] | None = None


class _PlannerPauseError(Exception):
    """Unwinds a tool call so the planner can return a pause."""

    def __init__(self, pause: PlannerPause) -> None:
        super().__init__(pause.reason)
        self.pause = pause


__all__ = ["_PauseRecord", "_PlannerPauseError"]
