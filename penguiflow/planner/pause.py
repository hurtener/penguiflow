"""Pause record helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .trajectory import Trajectory


@dataclass(slots=True)
class _PauseRecord:
    trajectory: Trajectory
    reason: str
    payload: dict[str, Any]
    constraints: dict[str, Any] | None = None
    tool_context: dict[str, Any] | None = None


class _PlannerPauseSignal(Exception):
    def __init__(self, pause: Any) -> None:
        super().__init__(getattr(pause, "reason", "pause"))
        self.pause = pause


__all__ = ["_PauseRecord", "_PlannerPauseSignal"]
