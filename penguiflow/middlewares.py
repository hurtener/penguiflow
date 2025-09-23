"""Middleware hooks for PenguiFlow.

Instrumentation arrives in Phase 3.
"""

from __future__ import annotations

from typing import Protocol


class Middleware(Protocol):
    """Base middleware signature."""

    async def __call__(self, event: str, payload: dict[str, object]) -> None: ...


__all__ = ["Middleware"]
