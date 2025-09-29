"""Middleware hooks for PenguiFlow."""

from __future__ import annotations

from typing import Protocol

from .metrics import FlowEvent


class Middleware(Protocol):
    """Base middleware signature receiving :class:`FlowEvent` objects."""

    async def __call__(self, event: FlowEvent) -> None: ...


__all__ = ["Middleware", "FlowEvent"]
