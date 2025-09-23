"""Node abstractions for PenguiFlow runtime."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Context


@dataclass(slots=True)
class NodePolicy:
    """Execution policy configuration placeholder."""

    validate: str = "none"
    timeout_s: float | None = None
    max_retries: int = 0
    backoff_base: float = 0.5
    backoff_mult: float = 2.0
    max_backoff: float | None = None


@dataclass(slots=True)
class Node:
    """Wraps an async callable with metadata used by the runtime."""

    func: Callable[..., Awaitable[Any]]
    name: str | None = None
    policy: NodePolicy = field(default_factory=NodePolicy)
    allow_cycle: bool = False
    node_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not asyncio.iscoroutinefunction(self.func):
            raise TypeError("Node function must be declared with async def")

        self.name = self.name or self.func.__name__
        self.node_id = uuid.uuid4().hex

        signature = inspect.signature(self.func)
        params = list(signature.parameters.values())
        if len(params) != 2:
            raise ValueError(
                f"Node '{self.name}' must accept exactly two parameters "
                f"(message, ctx); got {len(params)}"
            )

        ctx_param = params[1]
        if ctx_param.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise ValueError("Context parameter must be positional")

    async def invoke(self, message: Any, ctx: Context, *, registry: Any | None) -> Any:
        """Invoke the underlying coroutine, returning its result."""

        return await self.func(message, ctx)

    def to(self, *nodes: Node) -> tuple[Node, tuple[Node, ...]]:
        return self, nodes

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Node(name={self.name!r}, node_id={self.node_id})"


__all__ = ["Node", "NodePolicy"]
