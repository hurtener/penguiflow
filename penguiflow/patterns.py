"""Common orchestration patterns for PenguiFlow."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter

from .node import Node, NodePolicy
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
    """Run the async *worker* across *items* with bounded concurrency."""

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
) -> Node:
    """Create a node that routes messages based on predicate outputs."""

    async def router(msg: Any, ctx) -> None:
        targets = predicate(msg)
        if targets is None:
            return

        normalized = _normalize_targets(ctx, targets)
        if normalized:
            await ctx.emit(msg, to=normalized)

    return Node(router, name=name, policy=NodePolicy(validate="none"))


def union_router(
    name: str,
    union_model: type[BaseModel],
) -> Node:
    """Route based on a discriminated union Pydantic model."""

    adapter = TypeAdapter(union_model)

    async def router(msg: BaseModel, ctx) -> None:
        validated = adapter.validate_python(msg)

        target = getattr(validated, "kind", validated.__class__.__name__)
        normalized = _normalize_targets(ctx, target)
        if not normalized:
            raise KeyError(f"No successor matches '{target}'")
        await ctx.emit(validated, to=normalized)

    return Node(router, name=name, policy=NodePolicy(validate="none"))


def join_k(name: str, k: int) -> Node:
    """Aggregate *k* messages per trace_id and emit the grouped payloads."""

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
