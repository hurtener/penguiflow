"""Model registry for PenguiFlow.

Actual implementation comes in Phase 2.
"""

from __future__ import annotations

from typing import Any


class ModelRegistry:
    """Placeholder registry."""

    def register(self, node_name: str, in_model: Any, out_model: Any) -> None:  # noqa: ANN401
        raise NotImplementedError

    def adapters(self, node_name: str) -> tuple[Any, Any]:  # noqa: ANN401
        raise NotImplementedError


__all__ = ["ModelRegistry"]
