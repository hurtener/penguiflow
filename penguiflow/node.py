"""Node abstractions for PenguiFlow.

Populated in Phase 1+.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class NodePolicy:
    """Placeholder for per-node execution policy."""

    validate: str = "none"


class Node:
    """Placeholder Node wrapper."""

    def __init__(self, name: str, func: Callable[..., Awaitable[Any]]) -> None:
        self.name = name
        self.func = func

    def to(self, *nodes: Node) -> tuple[Node, tuple[Node, ...]]:
        return self, nodes


__all__ = ["Node", "NodePolicy"]
