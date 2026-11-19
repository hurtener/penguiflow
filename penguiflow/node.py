"""Node abstractions for planner tools."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NodePolicy:
    """Per-node policy metadata attached to a :class:`Node`.

    Attributes:
        validate: Which sides of the call should be validated against the
            node's registered adapters. One of ``"both"``, ``"in"``, ``"out"``
            or ``"none"``. Defaults to ``"both"``.
        timeout_s: Optional wall-clock timeout hint, in seconds, for a single
            invocation. ``None`` (the default) means no timeout.
        max_retries: Number of retry attempts allowed after the initial
            invocation fails. ``0`` (the default) means no retries.
        backoff_base: Base delay, in seconds, for exponential retry backoff.
            Defaults to ``0.5``.
        backoff_mult: Multiplier applied per retry attempt to ``backoff_base``.
            Defaults to ``2.0``.
        max_backoff: Optional upper bound, in seconds, on the backoff delay.

    Raises:
        ValueError: If ``validate`` is not one of ``"both"``, ``"in"``,
            ``"out"``, or ``"none"``.
    """

    validate: str = "both"
    timeout_s: float | None = None
    max_retries: int = 0
    backoff_base: float = 0.5
    backoff_mult: float = 2.0
    max_backoff: float | None = None

    def __post_init__(self) -> None:
        if self.validate not in {"both", "in", "out", "none"}:
            raise ValueError("validate must be one of 'both', 'in', 'out', 'none'")


@dataclass(slots=True)
class Node:
    """Wraps an async callable so it can be exposed to the planner as a tool.

    A ``Node`` pairs a coroutine function of the form
    ``async def handler(args, ctx) -> result`` with a name (used for registry
    lookups and the tool catalog) and a :class:`NodePolicy`. Nodes are hashable
    by their generated ``node_id``.

    Attributes:
        func: The async callable implementing the tool. Must be declared with
            ``async def`` and accept exactly two positional parameters: the
            validated arguments and the planner
            :class:`~penguiflow.planner.context.ToolContext`.
        name: Human-readable node name used in registry lookups and the tool
            catalog. Defaults to ``func.__name__`` when not provided.
        policy: The :class:`NodePolicy` attached to this node.
        node_id: Unique identifier generated automatically for the node
            instance; used for hashing and equality.

    Raises:
        TypeError: If ``func`` is not declared with ``async def``.
        ValueError: If ``func`` does not accept exactly two positional
            parameters, or if its second parameter (the context) is not
            positional.
    """

    func: Callable[..., Awaitable[Any]]
    name: str | None = None
    policy: NodePolicy = field(default_factory=NodePolicy)
    node_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not asyncio.iscoroutinefunction(self.func):
            raise TypeError("Node function must be declared with async def")

        self.name = self.name or self.func.__name__
        assert self.name is not None  # narrow for type-checkers
        self.node_id = uuid.uuid4().hex

        signature = inspect.signature(self.func)
        params = list(signature.parameters.values())
        if len(params) != 2:
            raise ValueError(f"Node '{self.name}' must accept exactly two parameters (args, ctx); got {len(params)}")

        ctx_param = params[1]
        if ctx_param.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise ValueError("Context parameter must be positional")

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Node(name={self.name!r}, node_id={self.node_id})"


__all__ = ["Node", "NodePolicy"]
