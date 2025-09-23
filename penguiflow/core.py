"""Core runtime primitives for PenguiFlow (Phase 1).

Implements Context, Floe, and PenguiFlow runtime with backpressure-aware
queues, cycle detection, and graceful shutdown semantics.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .node import Node

logger = logging.getLogger("penguiflow.core")

DEFAULT_QUEUE_MAXSIZE = 64


class CycleError(RuntimeError):
    """Raised when a cycle is detected in the flow graph."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Synthetic endpoints for PenguiFlow."""

    name: str


OPEN_SEA = Endpoint("OpenSea")
ROOKERY = Endpoint("Rookery")


class Floe:
    """Queue-backed edge between nodes."""

    __slots__ = ("source", "target", "queue")

    def __init__(
        self,
        source: Node | Endpoint | None,
        target: Node | Endpoint | None,
        *,
        maxsize: int,
    ) -> None:
        self.source = source
        self.target = target
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)


class Context:
    """Provides fetch/emit helpers for a node within a flow."""

    __slots__ = ("_owner", "_incoming", "_outgoing", "_buffer")

    def __init__(self, owner: Node | Endpoint) -> None:
        self._owner = owner
        self._incoming: dict[Node | Endpoint, Floe] = {}
        self._outgoing: dict[Node | Endpoint, Floe] = {}
        self._buffer: deque[Any] = deque()

    @property
    def owner(self) -> Node | Endpoint:
        return self._owner

    def add_incoming_floe(self, floe: Floe) -> None:
        if floe.source is None:
            return
        self._incoming[floe.source] = floe

    def add_outgoing_floe(self, floe: Floe) -> None:
        if floe.target is None:
            return
        self._outgoing[floe.target] = floe

    def _resolve_targets(
        self,
        targets: Node | Endpoint | Sequence[Node | Endpoint] | None,
        mapping: dict[Node | Endpoint, Floe],
    ) -> list[Floe]:
        if not mapping:
            return []

        if targets is None:
            return list(mapping.values())

        if isinstance(targets, (Node, Endpoint)):
            targets = [targets]

        resolved: list[Floe] = []
        for node in targets:
            floe = mapping.get(node)
            if floe is None:
                owner = getattr(self._owner, "name", self._owner)
                target_name = getattr(node, "name", node)
                raise KeyError(f"Unknown target {target_name} for {owner}")
            resolved.append(floe)
        return resolved

    async def emit(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        for floe in self._resolve_targets(to, self._outgoing):
            await floe.queue.put(msg)

    def emit_nowait(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        for floe in self._resolve_targets(to, self._outgoing):
            floe.queue.put_nowait(msg)

    def fetch_nowait(self, from_: Node | Sequence[Node] | None = None) -> Any:
        if self._buffer:
            return self._buffer.popleft()
        for floe in self._resolve_targets(from_, self._incoming):
            try:
                return floe.queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
        raise asyncio.QueueEmpty("no messages available")

    async def fetch(self, from_: Node | Sequence[Node] | None = None) -> Any:
        if self._buffer:
            return self._buffer.popleft()

        floes = self._resolve_targets(from_, self._incoming)
        if not floes:
            raise RuntimeError("context has no incoming floes to fetch from")
        if len(floes) == 1:
            return await floes[0].queue.get()
        return await self.fetch_any(from_)

    async def fetch_any(self, from_: Node | Sequence[Node] | None = None) -> Any:
        if self._buffer:
            return self._buffer.popleft()

        floes = self._resolve_targets(from_, self._incoming)
        if not floes:
            raise RuntimeError("context has no incoming floes to fetch from")

        tasks = [asyncio.create_task(floe.queue.get()) for floe in floes]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        try:
            done_results = [task.result() for task in done]
            result = done_results[0]
            for extra in done_results[1:]:
                self._buffer.append(extra)
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        return result

    def outgoing_count(self) -> int:
        return len(self._outgoing)


class PenguiFlow:
    """Coordinates node execution and message routing."""

    def __init__(
        self,
        *adjacencies: tuple[Node, Sequence[Node]],
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        allow_cycles: bool = False,
    ) -> None:
        self._queue_maxsize = queue_maxsize
        self._allow_cycles = allow_cycles
        self._nodes: set[Node] = set()
        self._adjacency: dict[Node, set[Node]] = {}
        self._contexts: dict[Node | Endpoint, Context] = {}
        self._floes: set[Floe] = set()
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False
        self._registry: Any | None = None

        self._build_graph(adjacencies)

    @property
    def registry(self) -> Any | None:
        return self._registry

    def _build_graph(self, adjacencies: Sequence[tuple[Node, Sequence[Node]]]) -> None:
        for start, successors in adjacencies:
            self._nodes.add(start)
            self._adjacency.setdefault(start, set())
            for succ in successors:
                self._nodes.add(succ)
                self._adjacency.setdefault(succ, set())
                self._adjacency[start].add(succ)

        self._detect_cycles()

        # create contexts for nodes and endpoints
        for node in self._nodes:
            self._contexts[node] = Context(node)
        self._contexts[OPEN_SEA] = Context(OPEN_SEA)
        self._contexts[ROOKERY] = Context(ROOKERY)

        incoming: dict[Node, set[Node | Endpoint]] = {
            node: set() for node in self._nodes
        }
        for parent, children in self._adjacency.items():
            for child in children:
                incoming[child].add(parent)
                floe = Floe(parent, child, maxsize=self._queue_maxsize)
                self._floes.add(floe)
                self._contexts[parent].add_outgoing_floe(floe)
                self._contexts[child].add_incoming_floe(floe)

        # Link OpenSea to ingress nodes (no incoming parents)
        for node, parents in incoming.items():
            if not parents:
                ingress_floe = Floe(OPEN_SEA, node, maxsize=self._queue_maxsize)
                self._floes.add(ingress_floe)
                self._contexts[OPEN_SEA].add_outgoing_floe(ingress_floe)
                self._contexts[node].add_incoming_floe(ingress_floe)

        # Link egress nodes (no outgoing successors) to Rookery
        for node in self._nodes:
            if not self._adjacency.get(node):
                egress_floe = Floe(node, ROOKERY, maxsize=self._queue_maxsize)
                self._floes.add(egress_floe)
                self._contexts[node].add_outgoing_floe(egress_floe)
                self._contexts[ROOKERY].add_incoming_floe(egress_floe)

    def _detect_cycles(self) -> None:
        if self._allow_cycles:
            return

        indegree: dict[Node, int] = {node: 0 for node in self._nodes}
        for _parent, children in self._adjacency.items():
            for child in children:
                indegree[child] += 1

        queue = [node for node, deg in indegree.items() if deg == 0]
        visited = 0

        while queue:
            node = queue.pop()
            visited += 1
            for succ in self._adjacency.get(node, set()):
                indegree[succ] -= 1
                if indegree[succ] == 0:
                    queue.append(succ)

        if visited != len(self._nodes):
            raise CycleError("Flow contains a cycle; enable allow_cycles to bypass")

    def run(self, *, registry: Any | None = None) -> None:
        if self._running:
            raise RuntimeError("PenguiFlow already running")
        self._running = True
        self._registry = registry
        loop = asyncio.get_running_loop()

        for node in self._nodes:
            context = self._contexts[node]
            task = loop.create_task(
                self._node_worker(node, context), name=f"penguiflow:{node.name}"
            )
            self._tasks.append(task)

    async def _node_worker(self, node: Node, context: Context) -> None:
        while True:
            try:
                message = await context.fetch()
                result = await node.invoke(message, context, registry=self._registry)
                if result is not None:
                    await context.emit(result)
            except asyncio.CancelledError:
                logger.debug(
                    "node_cancelled",
                    extra={"node_name": node.name, "node_id": node.node_id},
                )
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "node_error",
                    extra={
                        "event": "node_error",
                        "node_name": node.name,
                        "node_id": node.node_id,
                        "exception": exc,
                    },
                )

    async def stop(self) -> None:
        if not self._running:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._running = False

    async def emit(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        await self._contexts[OPEN_SEA].emit(msg, to)

    def emit_nowait(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        self._contexts[OPEN_SEA].emit_nowait(msg, to)

    async def fetch(self, from_: Node | Sequence[Node] | None = None) -> Any:
        return await self._contexts[ROOKERY].fetch(from_)

    async def fetch_any(self, from_: Node | Sequence[Node] | None = None) -> Any:
        return await self._contexts[ROOKERY].fetch_any(from_)


def create(*adjacencies: tuple[Node, Sequence[Node]], **kwargs: Any) -> PenguiFlow:
    """Convenience helper to instantiate a PenguiFlow."""

    return PenguiFlow(*adjacencies, **kwargs)


__all__ = [
    "Context",
    "Floe",
    "PenguiFlow",
    "CycleError",
    "create",
    "DEFAULT_QUEUE_MAXSIZE",
]
