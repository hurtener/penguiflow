"""Public package surface for PenguiFlow."""

from __future__ import annotations

from .core import (
    DEFAULT_QUEUE_MAXSIZE,
    Context,
    CycleError,
    PenguiFlow,
    create,
)
from .node import Node, NodePolicy

__all__ = [
    "__version__",
    "Context",
    "CycleError",
    "PenguiFlow",
    "DEFAULT_QUEUE_MAXSIZE",
    "Node",
    "NodePolicy",
    "create",
]

__version__ = "0.1.0"
