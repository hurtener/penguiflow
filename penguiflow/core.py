"""Core runtime primitives for PenguiFlow (Phase 1).

Implements Context, Floe, and PenguiFlow runtime with backpressure-aware
queues, cycle detection, and graceful shutdown semantics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .middlewares import Middleware
from .node import Node, NodePolicy
from .registry import ModelRegistry
from .types import WM, FinalAnswer, Message

logger = logging.getLogger("penguiflow.core")

BUDGET_EXCEEDED_TEXT = "Hop budget exhausted"
DEADLINE_EXCEEDED_TEXT = "Deadline exceeded"

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

        if isinstance(targets, Node | Endpoint):
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

    async def emit(
        self, msg: Any, to: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> None:
        for floe in self._resolve_targets(to, self._outgoing):
            await floe.queue.put(msg)

    def emit_nowait(
        self, msg: Any, to: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> None:
        for floe in self._resolve_targets(to, self._outgoing):
            floe.queue.put_nowait(msg)

    def fetch_nowait(
        self, from_: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> Any:
        if self._buffer:
            return self._buffer.popleft()
        for floe in self._resolve_targets(from_, self._incoming):
            try:
                return floe.queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
        raise asyncio.QueueEmpty("no messages available")

    async def fetch(
        self, from_: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> Any:
        if self._buffer:
            return self._buffer.popleft()

        floes = self._resolve_targets(from_, self._incoming)
        if not floes:
            raise RuntimeError("context has no incoming floes to fetch from")
        if len(floes) == 1:
            return await floes[0].queue.get()
        return await self.fetch_any(from_)

    async def fetch_any(
        self, from_: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> Any:
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

    def queue_depth_in(self) -> int:
        return sum(floe.queue.qsize() for floe in self._incoming.values())


class PenguiFlow:
    """Coordinates node execution and message routing."""

    def __init__(
        self,
        *adjacencies: tuple[Node, Sequence[Node]],
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        allow_cycles: bool = False,
        middlewares: Sequence[Middleware] | None = None,
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
        self._middlewares: list[Middleware] = list(middlewares or [])

        self._build_graph(adjacencies)

    @property
    def registry(self) -> Any | None:
        return self._registry

    def add_middleware(self, middleware: Middleware) -> None:
        self._middlewares.append(middleware)

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
                if not (parent is child and parent.allow_cycle):
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
            successors_set = self._adjacency.get(node, set())
            if not successors_set or successors_set == {node}:
                egress_floe = Floe(node, ROOKERY, maxsize=self._queue_maxsize)
                self._floes.add(egress_floe)
                self._contexts[node].add_outgoing_floe(egress_floe)
                self._contexts[ROOKERY].add_incoming_floe(egress_floe)

    def _detect_cycles(self) -> None:
        if self._allow_cycles:
            return

        adjacency: dict[Node, set[Node]] = {
            node: set(children) for node, children in self._adjacency.items()
        }

        for node, children in adjacency.items():
            if node.allow_cycle:
                children.discard(node)

        indegree: dict[Node, int] = {node: 0 for node in self._nodes}
        for _parent, children in adjacency.items():
            for child in children:
                indegree[child] += 1

        queue = [node for node, deg in indegree.items() if deg == 0]
        visited = 0

        while queue:
            node = queue.pop()
            visited += 1
            for succ in adjacency.get(node, set()):
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
                await self._execute_with_reliability(node, context, message)
            except asyncio.CancelledError:
                await self._emit_event(
                    event="node_cancelled",
                    node=node,
                    context=context,
                    trace_id=None,
                    attempt=0,
                    latency_ms=None,
                    level=logging.DEBUG,
                )
                raise

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

    async def _execute_with_reliability(
        self,
        node: Node,
        context: Context,
        message: Any,
    ) -> None:
        trace_id = getattr(message, "trace_id", None)
        attempt = 0

        while True:
            start = time.perf_counter()
            await self._emit_event(
                event="node_start",
                node=node,
                context=context,
                trace_id=trace_id,
                attempt=attempt,
                latency_ms=0.0,
                level=logging.DEBUG,
            )

            try:
                invocation = node.invoke(message, context, registry=self._registry)
                if node.policy.timeout_s is not None:
                    result = await asyncio.wait_for(invocation, node.policy.timeout_s)
                else:
                    result = await invocation

                if result is not None:
                    destination, prepared, targets = self._controller_postprocess(
                        node, context, message, result
                    )

                    if destination == "skip":
                        continue
                    if destination == "rookery":
                        await context.emit(prepared, to=[ROOKERY])
                        continue
                    await context.emit(prepared, to=targets)

                latency = (time.perf_counter() - start) * 1000
                await self._emit_event(
                    event="node_success",
                    node=node,
                    context=context,
                    trace_id=trace_id,
                    attempt=attempt,
                    latency_ms=latency,
                    level=logging.INFO,
                )
                return
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                latency = (time.perf_counter() - start) * 1000
                await self._emit_event(
                    event="node_timeout",
                    node=node,
                    context=context,
                    trace_id=trace_id,
                    attempt=attempt,
                    latency_ms=latency,
                    level=logging.WARNING,
                    extra={"exception": repr(exc)},
                )
                if attempt >= node.policy.max_retries:
                    await self._emit_event(
                        event="node_failed",
                        node=node,
                        context=context,
                        trace_id=trace_id,
                        attempt=attempt,
                        latency_ms=latency,
                        level=logging.ERROR,
                        extra={"exception": repr(exc)},
                    )
                    return
                attempt += 1
                delay = self._backoff_delay(node.policy, attempt)
                await self._emit_event(
                    event="node_retry",
                    node=node,
                    context=context,
                    trace_id=trace_id,
                    attempt=attempt,
                    latency_ms=None,
                    level=logging.INFO,
                    extra={"sleep_s": delay, "exception": repr(exc)},
                )
                await asyncio.sleep(delay)
                continue
            except Exception as exc:  # noqa: BLE001
                latency = (time.perf_counter() - start) * 1000
                await self._emit_event(
                    event="node_error",
                    node=node,
                    context=context,
                    trace_id=trace_id,
                    attempt=attempt,
                    latency_ms=latency,
                    level=logging.ERROR,
                    extra={"exception": repr(exc)},
                )
                if attempt >= node.policy.max_retries:
                    await self._emit_event(
                        event="node_failed",
                        node=node,
                        context=context,
                        trace_id=trace_id,
                        attempt=attempt,
                        latency_ms=latency,
                        level=logging.ERROR,
                        extra={"exception": repr(exc)},
                    )
                    return
                attempt += 1
                delay = self._backoff_delay(node.policy, attempt)
                await self._emit_event(
                    event="node_retry",
                    node=node,
                    context=context,
                    trace_id=trace_id,
                    attempt=attempt,
                    latency_ms=None,
                    level=logging.INFO,
                    extra={"sleep_s": delay, "exception": repr(exc)},
                )
                await asyncio.sleep(delay)

    def _backoff_delay(self, policy: NodePolicy, attempt: int) -> float:
        exponent = max(attempt - 1, 0)
        delay = policy.backoff_base * (policy.backoff_mult ** exponent)
        if policy.max_backoff is not None:
            delay = min(delay, policy.max_backoff)
        return delay

    def _controller_postprocess(
        self,
        node: Node,
        context: Context,
        incoming: Any,
        result: Any,
    ) -> tuple[str, Any, list[Node] | None]:
        if isinstance(result, Message):
            payload = result.payload
            if isinstance(payload, WM):
                now = time.time()
                if result.deadline_s is not None and now > result.deadline_s:
                    final = FinalAnswer(text=DEADLINE_EXCEEDED_TEXT)
                    final_msg = result.model_copy(update={"payload": final})
                    return "rookery", final_msg, None

                if payload.hops + 1 >= payload.budget_hops:
                    final = FinalAnswer(text=BUDGET_EXCEEDED_TEXT)
                    final_msg = result.model_copy(update={"payload": final})
                    return "rookery", final_msg, None

                updated_payload = payload.model_copy(update={"hops": payload.hops + 1})
                updated_msg = result.model_copy(update={"payload": updated_payload})
                return "context", updated_msg, [node]

            if isinstance(payload, FinalAnswer):
                return "rookery", result, None

        return "context", result, None

    async def _emit_event(
        self,
        *,
        event: str,
        node: Node,
        context: Context,
        trace_id: str | None,
        attempt: int,
        latency_ms: float | None,
        level: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "event": event,
            "node_name": node.name,
            "node_id": node.node_id,
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "q_depth_in": context.queue_depth_in(),
            "attempt": attempt,
        }
        if extra:
            payload.update(extra)

        logger.log(level, event, extra=payload)

        for middleware in list(self._middlewares):
            try:
                await middleware(event, payload)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "middleware_error",
                    extra={
                        "event": "middleware_error",
                        "node_name": node.name,
                        "node_id": node.node_id,
                        "exception": exc,
                    },
                )


PlaybookFactory = Callable[[], tuple["PenguiFlow", ModelRegistry | None]]


async def call_playbook(
    playbook: PlaybookFactory,
    parent_msg: Message,
    timeout: float | None = None,
) -> Any:
    """Execute a subflow playbook and return the first Rookery payload."""

    flow, registry = playbook()
    flow.run(registry=registry)

    try:
        await flow.emit(parent_msg)
        fetch_coro = flow.fetch()
        if timeout is not None:
            result_msg = await asyncio.wait_for(fetch_coro, timeout)
        else:
            result_msg = await fetch_coro
        if isinstance(result_msg, Message):
            return result_msg.payload
        return result_msg
    finally:
        await asyncio.shield(flow.stop())


def create(*adjacencies: tuple[Node, Sequence[Node]], **kwargs: Any) -> PenguiFlow:
    """Convenience helper to instantiate a PenguiFlow."""

    return PenguiFlow(*adjacencies, **kwargs)


__all__ = [
    "Context",
    "Floe",
    "PenguiFlow",
    "CycleError",
    "call_playbook",
    "create",
    "DEFAULT_QUEUE_MAXSIZE",
]
