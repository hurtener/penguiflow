"""Node abstractions for PenguiFlow runtime."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic.type_adapter import TypeAdapter

    from .core import Context
    from .registry import ModelRegistry


@dataclass(slots=True)
class NodePolicy:
    """Per-node execution policy controlling validation, timeouts, and retries.

    A ``NodePolicy`` is attached to each :class:`Node` and consulted by the runtime
    on every invocation: it decides whether the registry-backed Pydantic adapters
    validate the inbound message and/or outbound result, how long a single
    attempt may run before it is treated as a timeout, and how failed attempts
    are retried with exponential backoff.

    Attributes:
        validate: Which sides of the call to validate against the node's
            registered adapters. One of ``"both"`` (validate message in and
            result out), ``"in"`` (message only), ``"out"`` (result only), or
            ``"none"`` (skip validation entirely). Defaults to ``"both"``.
        timeout_s: Wall-clock timeout in seconds applied to each invocation
            attempt. ``None`` (the default) disables the timeout, letting the
            node run to completion.
        max_retries: Number of retry attempts allowed after the initial
            invocation fails or times out. ``0`` (the default) means no
            retries; the runtime surfaces the first failure as a
            :class:`~penguiflow.errors.FlowError`.
        backoff_base: Base delay, in seconds, used to compute the exponential
            backoff before each retry. Defaults to ``0.5``.
        backoff_mult: Multiplier applied per retry attempt to
            ``backoff_base`` when computing the exponential backoff delay.
            Defaults to ``2.0``.
        max_backoff: Optional upper bound, in seconds, clamping the computed
            backoff delay. ``None`` (the default) leaves the delay unbounded.

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
    """Wraps an async callable with the metadata the PenguiFlow runtime needs.

    A ``Node`` is the unit of work in a flow graph: it pairs a coroutine function
    of the form ``async def handler(message, ctx) -> result`` with a name (used
    for routing and registry lookups), a :class:`NodePolicy` governing
    validation/timeouts/retries, and a flag indicating whether it may
    participate in a routing cycle. Nodes are hashable by their generated
    ``node_id`` so they can be used as graph vertices/dict keys.

    Attributes:
        func: The async callable implementing the node's logic. Must be
            declared with ``async def`` and accept exactly two positional
            parameters: the incoming message and the :class:`~penguiflow.core.Context`.
        name: Human-readable node name used in routing, logs, and registry
            lookups. Defaults to ``func.__name__`` when not provided.
        policy: The :class:`NodePolicy` controlling validation, timeout, and
            retry behavior for this node. Defaults to a policy with no
            retries and full validation.
        allow_cycle: Whether this node is permitted to be part of a routing
            cycle (e.g. a controller loop) without the flow raising a
            :class:`~penguiflow.errors.CycleError`. Defaults to ``False``.
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
    allow_cycle: bool = False
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
            raise ValueError(f"Node '{self.name}' must accept exactly two parameters (message, ctx); got {len(params)}")

        ctx_param = params[1]
        if ctx_param.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise ValueError("Context parameter must be positional")

    def _maybe_validate(
        self,
        adapter: TypeAdapter[Any] | None,
        value: Any,
        *,
        enforce: bool,
    ) -> Any:
        if not enforce or adapter is None:
            return value
        return adapter.validate_python(value)

    async def invoke(
        self,
        message: Any,
        ctx: Context,
        *,
        registry: ModelRegistry | None,
    ) -> Any:
        """Invoke the underlying coroutine, applying optional validation.

        Looks up the node's input/output adapters from ``registry`` (unless the
        policy's ``validate`` is ``"none"``), validates the message according to
        ``self.policy.validate``, calls ``self.func``, and validates the result
        (if any) on the way out.

        Args:
            message: The inbound payload to pass to ``self.func``.
            ctx: The runtime :class:`~penguiflow.core.Context` for this invocation,
                passed through unchanged to ``self.func``.
            registry: The :class:`~penguiflow.registry.ModelRegistry` used to
                resolve validation adapters for this node's name, or ``None``
                to skip adapter lookup entirely.

        Returns:
            The result produced by ``self.func``, validated against the
            output adapter when applicable, or ``None`` if the node produced
            no result.
        """

        adapter_in: TypeAdapter[Any] | None = None
        adapter_out: TypeAdapter[Any] | None = None

        if registry is not None and self.policy.validate != "none":
            node_name = self.name
            assert node_name is not None
            adapter_in, adapter_out = registry.adapters(node_name)

        enforce_in = self.policy.validate in {"in", "both"}
        enforce_out = self.policy.validate in {"out", "both"}

        validated_msg = self._maybe_validate(adapter_in, message, enforce=enforce_in)
        result = await self.func(validated_msg, ctx)

        if result is None:
            return None

        return self._maybe_validate(adapter_out, result, enforce=enforce_out)

    def to(self, *nodes: Node) -> tuple[Node, tuple[Node, ...]]:
        """Declare this node's successors for building the flow graph.

        Args:
            *nodes: Zero or more downstream nodes that this node may emit to.

        Returns:
            A ``(self, nodes)`` tuple, the adjacency-list entry format expected
            when constructing a flow's edge list.
        """
        return self, nodes

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Node(name={self.name!r}, node_id={self.node_id})"


__all__ = ["Node", "NodePolicy"]
