"""Typed message models for PenguiFlow.

Real models land in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Headers:
    """Placeholder headers structure."""

    tenant: str


@dataclass(slots=True)
class Message:
    """Placeholder message envelope."""

    payload: Any
    headers: Headers


__all__ = ["Headers", "Message"]
