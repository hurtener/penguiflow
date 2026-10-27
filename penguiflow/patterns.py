"""Common orchestration patterns for PenguiFlow."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter

from .node import Node, NodePolicy
from .policies import PolicyLike, RoutingRequest, evaluate_policy
from .types import Message

PayloadT = TypeVar("PayloadT")
ResultT = TypeVar("ResultT")

__all__ = [
    "map_concurrent",
    "join_k",
    "predicate_router",
    "union_router",
]


async def map_concurrent(
    items: Iterable[PayloadT],
    worker: Callable[[PayloadT], Awaitable[ResultT]],
    *,
    max_concurrency: int = 8,
) -> list[ResultT]:
    """Run the async *worker* across *items* with bounded concurrency.

    Materializes ``items`` into a list, then schedules ``worker`` for every
    item behind a semaphore so at most ``max_concurrency`` invocations run
    concurrently. Results are collected in the same order as the input items,
    regardless of completion order.

    Args:
        items: The iterable of payloads to process. Consumed eagerly into a
            list before scheduling.
        worker: An async callable invoked once per item; its return value is
            collected into the result list at the item's original index.
        max_concurrency: Maximum number of ``worker`` calls allowed to run at
            once. Values less than 1 are clamped up to 1. Defaults to 8.

    Returns:
        A list of results from ``worker``, in the same order as ``items``.
    """

    items_list = list(items)
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    results: list[ResultT | None] = [None] * len(items_list)

    async def run(index: int, item: PayloadT) -> None:
        async with semaphore:
            results[index] = await worker(item)

    await asyncio.gather(*(run(idx, item) for idx, item in enumerate(items_list)))
    return [cast(ResultT, result) for result in results]


def predicate_router(
    name: str,
    predicate: Callable[[Any], Sequence[Node | str] | Node | str | None],
    *,
    policy: PolicyLike | None = None,
) -> Node:
    """Create a node that routes messages based on predicate outputs.

    Builds and returns a :class:`~penguiflow.node.Node` whose handler calls
    ``predicate(msg)`` to decide which successor(s) to forward the message to.
    Successors may be named by their node ``name`` (resolved against the
    node's outgoing edges at emit time) or passed directly as :class:`Node`
    instances. If a ``policy`` is supplied, the predicate's proposed targets
    are additionally passed through :func:`~penguiflow.policies.evaluate_policy`
    before the message is emitted, allowing config-driven overrides of the
    routing decision.

    Args:
        name: Name assigned to the generated router node.
        predicate: A callable that inspects the incoming message and returns
            the successor(s) to route to: a single :class:`Node`, a node name
            string, a sequence of nodes/names, or ``None``/an empty sequence
            to drop the message (no emission).
        policy: Optional routing policy consulted after the predicate; may
            veto or rewrite the proposed targets. Defaults to ``None`` (the
            predicate's decision is used as-is).

    Returns:
        A :class:`Node` (with ``NodePolicy(validate="none")``) that performs
        the routing when invoked by the runtime.
    """

    async def router(msg: Any, ctx) -> None:
        targets = predicate(msg)
        if targets is None:
            return

        normalized = _normalize_targets(ctx, targets)
        if not normalized:
            return

        selected = normalized
        if policy is not None:
            request = RoutingRequest(
                message=msg,
                context=ctx,
                node=router_node,
                proposed=tuple(normalized),
                trace_id=getattr(msg, "trace_id", None),
            )
            decision = await evaluate_policy(policy, request)
            if decision is None:
                return
            selected = _normalize_targets(ctx, decision)
            if not selected:
                return

        await ctx.emit(msg, to=selected)

    router_node = Node(router, name=name, policy=NodePolicy(validate="none"))
    return router_node


def union_router(
    name: str,
    union_model: type[BaseModel],
    *,
    policy: PolicyLike | None = None,
) -> Node:
    """Create a node that routes based on a discriminated union Pydantic model.

    Builds and returns a :class:`~penguiflow.node.Node` whose handler validates
    the incoming message against ``union_model`` (a discriminated union), then
    routes to the successor whose name matches the validated model's ``kind``
    attribute (falling back to the model's class name if ``kind`` is absent).
    If a ``policy`` is supplied, the resolved target is additionally passed
    through :func:`~penguiflow.policies.evaluate_policy` before emission.

    Args:
        name: Name assigned to the generated router node.
        union_model: The Pydantic discriminated-union type used to validate
            and classify incoming messages.
        policy: Optional routing policy consulted after the union match; may
            veto or rewrite the proposed target. Defaults to ``None`` (the
            union match is used as-is).

    Returns:
        A :class:`Node` (with ``NodePolicy(validate="none")``) that performs
        the routing when invoked by the runtime.

    Raises:
        KeyError: If no successor's name matches the validated message's
            ``kind`` (or class name) and no policy rewrites the selection.
    """

    adapter = TypeAdapter(union_model)

    async def router(msg: BaseModel, ctx) -> None:
        validated = adapter.validate_python(msg)

        target = getattr(validated, "kind", validated.__class__.__name__)
        normalized = _normalize_targets(ctx, target)
        if not normalized:
            raise KeyError(f"No successor matches '{target}'")

        selected = normalized
        if policy is not None:
            request = RoutingRequest(
                message=validated,
                context=ctx,
                node=router_node,
                proposed=tuple(normalized),
                trace_id=getattr(validated, "trace_id", None),
            )
            decision = await evaluate_policy(policy, request)
            if decision is None:
                return
            selected = _normalize_targets(ctx, decision)
            if not selected:
                return

        await ctx.emit(validated, to=selected)

    router_node = Node(router, name=name, policy=NodePolicy(validate="none"))
    return router_node


def join_k(name: str, k: int) -> Node:
    """Create a node that aggregates *k* messages per ``trace_id``.

    Builds and returns a :class:`~penguiflow.node.Node` that buffers incoming
    messages keyed by their ``trace_id`` attribute until ``k`` messages have
    arrived for a given trace, then emits the aggregated result and clears
    that trace's bucket. If the buffered messages are
    :class:`~penguiflow.types.Message` instances, the result is a copy of the
    first message with its ``payload`` replaced by the list of collected
    payloads; otherwise the result is the raw list of buffered messages.

    Args:
        name: Name assigned to the generated aggregator node.
        k: Number of messages to collect per ``trace_id`` before emitting.
            Must be positive.

    Returns:
        A :class:`Node` (with ``NodePolicy(validate="none")``) that performs
        the aggregation when invoked by the runtime; it returns ``None`` until
        the ``k``-th message for a trace arrives.

    Raises:
        ValueError: If ``k`` is not positive, or (when invoked) if an incoming
            message lacks a ``trace_id``.
    """

    if k <= 0:
        raise ValueError("k must be positive")

    buckets: defaultdict[str, list[Any]] = defaultdict(list)

    async def aggregator(msg: Any, ctx) -> Any:
        trace_id = getattr(msg, "trace_id", None)
        if trace_id is None:
            raise ValueError("join_k requires messages with trace_id")

        bucket = buckets[trace_id]
        bucket.append(msg)
        if len(bucket) < k:
            return None

        buckets.pop(trace_id, None)
        batch = list(bucket)
        first = batch[0]
        if isinstance(first, Message):
            payloads = [item.payload for item in batch]
            aggregated = first.model_copy(update={"payload": payloads})
            return aggregated
        return batch

    return Node(aggregator, name=name, policy=NodePolicy(validate="none"))


def _normalize_targets(context, targets) -> list[Node]:
    if isinstance(targets, Node):
        target_list: Sequence[Node | str] = [targets]
    elif isinstance(targets, str):
        target_list = [targets]
    else:
        target_list = list(targets)

    normalized: list[Node] = []
    candidates = list(getattr(context, "_outgoing", {}).keys())
    for target in target_list:
        if isinstance(target, Node):
            normalized.append(target)
            continue

        if not isinstance(target, str):
            raise TypeError("Targets must be Node or str")

        matched = None
        for node in candidates:
            if isinstance(node, Node) and node.name == target:
                matched = node
                break
        if matched is None:
            raise KeyError(f"No successor named '{target}'")
        normalized.append(matched)

    return normalized
