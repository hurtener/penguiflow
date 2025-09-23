"""Core runtime primitives for PenguiFlow.

Phase 0 placeholder module. Real implementation arrives in Phase 1.
"""

from __future__ import annotations

from typing import Any


class Context:
    """Placeholder Context implementation."""

    async def emit(self, msg: Any, to: Any | None = None) -> None:  # noqa: ANN401
        raise NotImplementedError

    async def fetch(self, from_: Any | None = None) -> Any:  # noqa: ANN401
        raise NotImplementedError


class PenguiFlow:
    """Placeholder flow runtime."""

    def run(self, *, registry: Any | None = None) -> None:  # noqa: ANN401
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def emit(self, msg: Any, to: Any | None = None) -> None:  # noqa: ANN401
        raise NotImplementedError

    async def fetch(self, from_: Any | None = None) -> Any:  # noqa: ANN401
        raise NotImplementedError


__all__ = ["Context", "PenguiFlow"]
