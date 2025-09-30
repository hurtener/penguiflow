"""Core runtime primitives for PenguiFlow (Phase 1).

Implements Context, Floe, and PenguiFlow runtime with backpressure-aware
queues, cycle detection, and graceful shutdown semantics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

from .bus import BusEnvelope, MessageBus
from .errors import FlowError, FlowErrorCode
from .metrics import FlowEvent
from .middlewares import Middleware
from .node import Node, NodePolicy
from .registry import ModelRegistry
from .state import RemoteBinding, StateStore, StoredEvent
from .types import WM, FinalAnswer, Message, StreamChunk

logger = logging.getLogger("penguiflow.core")

BUDGET_EXCEEDED_TEXT = "Hop budget exhausted"
DEADLINE_EXCEEDED_TEXT = "Deadline exceeded"
TOKEN_BUDGET_EXCEEDED_TEXT = "Token budget exhausted"

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


class TraceCancelled(Exception):
    """Raised when work for a specific trace_id is cancelled."""

    def __init__(self, trace_id: str | None) -> None:
        super().__init__(f"trace cancelled: {trace_id}")
        self.trace_id = trace_id


class Context:
    """Provides fetch/emit helpers for a node within a flow."""

    __slots__ = (
        "_owner",
        "_incoming",
        "_outgoing",
        "_buffer",
        "_stream_seq",
        "_runtime",
    )

    def __init__(
        self, owner: Node | Endpoint, runtime: PenguiFlow | None = None
    ) -> None:
        self._owner = owner
        self._incoming: dict[Node | Endpoint, Floe] = {}
        self._outgoing: dict[Node | Endpoint, Floe] = {}
        self._buffer: deque[Any] = deque()
        self._stream_seq: dict[str, int] = {}
        self._runtime = runtime

    @property
    def owner(self) -> Node | Endpoint:
        return self._owner

    @property
    def runtime(self) -> PenguiFlow | None:
        """Return the runtime this context is attached to, if any."""

        return self._runtime

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
        if self._runtime is None:
            raise RuntimeError("Context is not attached to a running flow")
        for floe in self._resolve_targets(to, self._outgoing):
            await self._runtime._send_to_floe(floe, msg)

    def emit_nowait(
        self, msg: Any, to: Node | Endpoint | Sequence[Node | Endpoint] | None = None
    ) -> None:
        if self._runtime is None:
            raise RuntimeError("Context is not attached to a running flow")
        for floe in self._resolve_targets(to, self._outgoing):
            self._runtime._send_to_floe_nowait(floe, msg)

    async def emit_chunk(
        self,
        *,
        parent: Message,
        text: str,
        stream_id: str | None = None,
        seq: int | None = None,
        done: bool = False,
        meta: dict[str, Any] | None = None,
        to: Node | Endpoint | Sequence[Node | Endpoint] | None = None,
    ) -> StreamChunk:
        """Emit a streaming chunk that inherits routing metadata from ``parent``.

        The helper manages monotonically increasing sequence numbers per
        ``stream_id`` (defaulting to the parent's trace id) unless an explicit
        ``seq`` is provided. It returns the emitted ``StreamChunk`` for
        introspection in tests or downstream logic.
        """

        sid = stream_id or parent.trace_id
        first_chunk = sid not in self._stream_seq
        if seq is None:
            next_seq = self._stream_seq.get(sid, -1) + 1
        else:
            next_seq = seq
        self._stream_seq[sid] = next_seq

        meta_dict = dict(meta) if meta else {}

        chunk = StreamChunk(
            stream_id=sid,
            seq=next_seq,
            text=text,
            done=done,
            meta=meta_dict,
        )

        message_meta = dict(parent.meta)

        message = Message(
            payload=chunk,
            headers=parent.headers,
            trace_id=parent.trace_id,
            deadline_s=parent.deadline_s,
            meta=message_meta,
        )

        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("Context is not attached to a running flow")

        if not first_chunk:
            await runtime._await_trace_capacity(sid, offset=1)

        await self.emit(message, to=to)

        if done:
            self._stream_seq.pop(sid, None)

        return chunk

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

    def queue_depth_out(self) -> int:
        return sum(floe.queue.qsize() for floe in self._outgoing.values())

    async def call_playbook(
        self,
        playbook: PlaybookFactory,
        parent_msg: Message,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Launch a subflow playbook using the current runtime for propagation."""

        return await call_playbook(
            playbook,
            parent_msg,
            timeout=timeout,
            runtime=self._runtime,
        )


class PenguiFlow:
    """Coordinates node execution and message routing."""

    def __init__(
        self,
        *adjacencies: tuple[Node, Sequence[Node]],
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        allow_cycles: bool = False,
        middlewares: Sequence[Middleware] | None = None,
        emit_errors_to_rookery: bool = False,
        state_store: StateStore | None = None,
        message_bus: MessageBus | None = None,
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
        self._trace_counts: dict[str, int] = {}
        self._trace_events: dict[str, asyncio.Event] = {}
        self._trace_invocations: dict[str, set[asyncio.Task[Any]]] = {}
        self._external_tasks: dict[str, set[asyncio.Future[Any]]] = {}
        self._trace_capacity_waiters: dict[str, list[asyncio.Event]] = {}
        self._latest_wm_hops: dict[str, int] = {}
        self._emit_errors_to_rookery = emit_errors_to_rookery
        self._state_store = state_store
        self._message_bus = message_bus
        self._bus_tasks: set[asyncio.Task[None]] = set()

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
            self._contexts[node] = Context(node, self)
        self._contexts[OPEN_SEA] = Context(OPEN_SEA, self)
        self._contexts[ROOKERY] = Context(ROOKERY, self)

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
        if registry is not None:
            self._ensure_registry_covers_nodes(registry)
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
                trace_id = self._get_trace_id(message)
                if self._deadline_expired(message):
                    await self._emit_event(
                        event="deadline_skip",
                        node=node,
                        context=context,
                        trace_id=trace_id,
                        attempt=0,
                        latency_ms=None,
                        level=logging.INFO,
                        extra={"deadline_s": getattr(message, "deadline_s", None)},
                    )
                    if isinstance(message, Message):
                        await self._handle_deadline_expired(context, message)
                    await self._finalize_message(message)
                    continue
                if trace_id is not None and self._is_trace_cancelled(trace_id):
                    await self._emit_event(
                        event="trace_cancel_drop",
                        node=node,
                        context=context,
                        trace_id=trace_id,
                        attempt=0,
                        latency_ms=None,
                        level=logging.INFO,
                    )
                    await self._finalize_message(message)
                    continue

                try:
                    await self._execute_with_reliability(node, context, message)
                except TraceCancelled:
                    await self._emit_event(
                        event="node_trace_cancelled",
                        node=node,
                        context=context,
                        trace_id=trace_id,
                        attempt=0,
                        latency_ms=None,
                        level=logging.INFO,
                    )
                finally:
                    await self._finalize_message(message)
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
        if self._trace_invocations:
            pending: list[asyncio.Task[Any]] = []
            for invocation_tasks in self._trace_invocations.values():
                for task in invocation_tasks:
                    if not task.done():
                        task.cancel()
                    pending.append(task)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._trace_invocations.clear()
        if self._external_tasks:
            pending_ext: list[asyncio.Future[Any]] = []
            for external_tasks in self._external_tasks.values():
                for external_task in external_tasks:
                    if not external_task.done():
                        external_task.cancel()
                    pending_ext.append(external_task)
            if pending_ext:
                await asyncio.gather(*pending_ext, return_exceptions=True)
            self._external_tasks.clear()
        if self._bus_tasks:
            await asyncio.gather(*self._bus_tasks, return_exceptions=True)
            self._bus_tasks.clear()
        self._trace_counts.clear()
        self._trace_events.clear()
        self._trace_invocations.clear()
        for waiters in self._trace_capacity_waiters.values():
            for waiter in waiters:
                waiter.set()
        self._trace_capacity_waiters.clear()
        self._running = False

    async def emit(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        if isinstance(msg, Message):
            payload = msg.payload
            if isinstance(payload, WM):
                last = self._latest_wm_hops.get(msg.trace_id)
                if last is not None and payload.hops == last:
                    return
        await self._contexts[OPEN_SEA].emit(msg, to)

    def emit_nowait(self, msg: Any, to: Node | Sequence[Node] | None = None) -> None:
        if isinstance(msg, Message):
            payload = msg.payload
            if isinstance(payload, WM):
                last = self._latest_wm_hops.get(msg.trace_id)
                if last is not None and payload.hops == last:
                    return
        self._contexts[OPEN_SEA].emit_nowait(msg, to)

    async def emit_chunk(
        self,
        *,
        parent: Message,
        text: str,
        stream_id: str | None = None,
        seq: int | None = None,
        done: bool = False,
        meta: dict[str, Any] | None = None,
        to: Node | Sequence[Node] | None = None,
    ) -> StreamChunk:
        """Emit a streaming chunk from outside a node via OpenSea context."""

        return await self._contexts[OPEN_SEA].emit_chunk(
            parent=parent,
            text=text,
            stream_id=stream_id,
            seq=seq,
            done=done,
            meta=meta,
            to=to,
        )

    async def fetch(self, from_: Node | Sequence[Node] | None = None) -> Any:
        result = await self._contexts[ROOKERY].fetch(from_)
        await self._finalize_message(result)
        return result

    async def fetch_any(self, from_: Node | Sequence[Node] | None = None) -> Any:
        result = await self._contexts[ROOKERY].fetch_any(from_)
        await self._finalize_message(result)
        return result

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Return the persisted history for ``trace_id`` from the state store."""

        if self._state_store is None:
            raise RuntimeError("PenguiFlow was created without a state_store")
        return await self._state_store.load_history(trace_id)

    def ensure_trace_event(self, trace_id: str) -> asyncio.Event:
        """Return (and create if needed) the cancellation event for ``trace_id``."""

        return self._trace_events.setdefault(trace_id, asyncio.Event())

    def register_external_task(self, trace_id: str, task: asyncio.Future[Any]) -> None:
        """Track an externally created task for cancellation bookkeeping."""

        if trace_id is None:
            return
        tasks = self._external_tasks.get(trace_id)
        if tasks is None:
            tasks = set[asyncio.Future[Any]]()
            self._external_tasks[trace_id] = tasks
        tasks.add(task)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            remaining = self._external_tasks.get(trace_id)
            if remaining is None:
                return
            remaining.discard(finished)
            if not remaining:
                self._external_tasks.pop(trace_id, None)

        task.add_done_callback(_cleanup)

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist a remote binding if a state store is configured."""

        if self._state_store is None:
            return
        try:
            await self._state_store.save_remote_binding(binding)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "state_store_binding_failed",
                extra={
                    "event": "state_store_binding_failed",
                    "trace_id": binding.trace_id,
                    "context_id": binding.context_id,
                    "task_id": binding.task_id,
                    "agent_url": binding.agent_url,
                    "exception": repr(exc),
                },
            )

    async def record_remote_event(
        self,
        *,
        event: str,
        node: Node,
        context: Context,
        trace_id: str | None,
        latency_ms: float | None,
        level: int = logging.INFO,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit a structured :class:`FlowEvent` for remote transport activity."""

        payload = dict(extra or {})
        await self._emit_event(
            event=event,
            node=node,
            context=context,
            trace_id=trace_id,
            attempt=0,
            latency_ms=latency_ms,
            level=level,
            extra=payload,
        )

    async def _execute_with_reliability(
        self,
        node: Node,
        context: Context,
        message: Any,
    ) -> None:
        trace_id = getattr(message, "trace_id", None)
        attempt = 0

        while True:
            if trace_id is not None and self._is_trace_cancelled(trace_id):
                raise TraceCancelled(trace_id)

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
                result = await self._invoke_node(
                    node,
                    context,
                    message,
                    trace_id,
                )

                if result is not None:
                    (
                        destination,
                        prepared,
                        targets,
                        deliver_rookery,
                    ) = self._controller_postprocess(
                        node, context, message, result
                    )

                    if deliver_rookery:
                        rookery_msg = (
                            prepared.model_copy(deep=True)
                            if isinstance(prepared, Message)
                            else prepared
                        )
                        await self._emit_to_rookery(
                            rookery_msg, source=context.owner
                        )

                    if destination == "skip":
                        continue

                    if destination == "rookery":
                        await self._emit_to_rookery(
                            prepared, source=context.owner
                        )
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
            except TraceCancelled:
                raise
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
                    timeout_message: str | None = None
                    if node.policy.timeout_s is not None:
                        timeout_message = (
                            f"Node '{node.name}' timed out after"
                            f" {node.policy.timeout_s:.2f}s"
                        )
                    flow_error = self._create_flow_error(
                        node=node,
                        trace_id=trace_id,
                        code=FlowErrorCode.NODE_TIMEOUT,
                        exc=exc,
                        attempt=attempt,
                        latency_ms=latency,
                        message=timeout_message,
                        metadata={"timeout_s": node.policy.timeout_s},
                    )
                    await self._handle_flow_error(
                        node=node,
                        context=context,
                        flow_error=flow_error,
                        latency=latency,
                        attempt=attempt,
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
                    flow_error = self._create_flow_error(
                        node=node,
                        trace_id=trace_id,
                        code=FlowErrorCode.NODE_EXCEPTION,
                        exc=exc,
                        attempt=attempt,
                        latency_ms=latency,
                        message=(
                            f"Node '{node.name}' raised {type(exc).__name__}: {exc}"
                        ),
                        metadata={"exception_repr": repr(exc)},
                    )
                    await self._handle_flow_error(
                        node=node,
                        context=context,
                        flow_error=flow_error,
                        latency=latency,
                        attempt=attempt,
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

    def _create_flow_error(
        self,
        *,
        node: Node,
        trace_id: str | None,
        code: FlowErrorCode,
        exc: BaseException,
        attempt: int,
        latency_ms: float | None,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> FlowError:
        node_name = node.name
        assert node_name is not None
        meta: dict[str, Any] = {"attempt": attempt}
        if latency_ms is not None:
            meta["latency_ms"] = latency_ms
        if metadata:
            meta.update(metadata)
        return FlowError.from_exception(
            trace_id=trace_id,
            node_name=node_name,
            node_id=node.node_id,
            exc=exc,
            code=code,
            message=message,
            metadata=meta,
        )

    async def _handle_flow_error(
        self,
        *,
        node: Node,
        context: Context,
        flow_error: FlowError,
        latency: float | None,
        attempt: int,
    ) -> None:
        original = flow_error.unwrap()
        exception_repr = repr(original) if original is not None else flow_error.message
        extra = {
            "exception": exception_repr,
            "flow_error": flow_error.to_payload(),
        }
        await self._emit_event(
            event="node_failed",
            node=node,
            context=context,
            trace_id=flow_error.trace_id,
            attempt=attempt,
            latency_ms=latency,
            level=logging.ERROR,
            extra=extra,
        )
        if self._emit_errors_to_rookery and flow_error.trace_id is not None:
            await self._emit_to_rookery(flow_error, source=context.owner)

    async def _invoke_node(
        self,
        node: Node,
        context: Context,
        message: Any,
        trace_id: str | None,
    ) -> Any:
        invocation = node.invoke(message, context, registry=self._registry)
        timeout = node.policy.timeout_s

        if trace_id is None:
            if timeout is None:
                return await invocation
            return await asyncio.wait_for(invocation, timeout)

        return await self._await_invocation(node, invocation, trace_id, timeout)

    def _register_invocation_task(
        self, trace_id: str, task: asyncio.Task[Any]
    ) -> None:
        tasks = self._trace_invocations.get(trace_id)
        if tasks is None:
            tasks = set[asyncio.Task[Any]]()
            self._trace_invocations[trace_id] = tasks
        tasks.add(task)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            remaining = self._trace_invocations.get(trace_id)
            if remaining is None:
                return
            remaining.discard(cast(asyncio.Task[Any], finished))
            if not remaining:
                self._trace_invocations.pop(trace_id, None)

        task.add_done_callback(_cleanup)

    async def _await_invocation(
        self,
        node: Node,
        invocation: Awaitable[Any],
        trace_id: str,
        timeout: float | None,
    ) -> Any:
        invocation_task = cast(asyncio.Task[Any], asyncio.ensure_future(invocation))
        self._register_invocation_task(trace_id, invocation_task)

        cancel_event = self._trace_events.get(trace_id)
        cancel_waiter: asyncio.Future[Any] | None = None
        if cancel_event is not None:
            cancel_waiter = asyncio.ensure_future(cancel_event.wait())

        timeout_task: asyncio.Future[Any] | None = None
        if timeout is not None:
            timeout_task = asyncio.ensure_future(asyncio.sleep(timeout))

        wait_tasks: set[asyncio.Future[Any]] = {invocation_task}
        if cancel_waiter is not None:
            wait_tasks.add(cancel_waiter)
        if timeout_task is not None:
            wait_tasks.add(timeout_task)

        pending: set[asyncio.Future[Any]] = set()
        try:
            done, pending = await asyncio.wait(
                wait_tasks, return_when=asyncio.FIRST_COMPLETED
            )

            if invocation_task in done:
                if invocation_task.cancelled():
                    raise TraceCancelled(trace_id)
                return invocation_task.result()

            if cancel_waiter is not None and cancel_waiter in done:
                invocation_task.cancel()
                await asyncio.gather(invocation_task, return_exceptions=True)
                raise TraceCancelled(trace_id)

            if timeout_task is not None and timeout_task in done:
                invocation_task.cancel()
                await asyncio.gather(invocation_task, return_exceptions=True)
                raise TimeoutError

            raise RuntimeError("node invocation wait exited without result")
        except asyncio.CancelledError:
            invocation_task.cancel()
            await asyncio.gather(invocation_task, return_exceptions=True)
            if cancel_waiter is not None:
                cancel_waiter.cancel()
            if timeout_task is not None:
                timeout_task.cancel()
            await asyncio.gather(
                *(task for task in (cancel_waiter, timeout_task) if task is not None),
                return_exceptions=True,
            )
            raise
        finally:
            for task in pending:
                task.cancel()
            watchers = [
                task for task in (cancel_waiter, timeout_task) if task is not None
            ]
            for watcher in watchers:
                watcher.cancel()
            if watchers:
                await asyncio.gather(*watchers, return_exceptions=True)

    def _get_trace_id(self, message: Any) -> str | None:
        return getattr(message, "trace_id", None)

    def _is_trace_cancelled(self, trace_id: str) -> bool:
        event = self._trace_events.get(trace_id)
        return event.is_set() if event is not None else False

    def _on_message_enqueued(self, message: Any) -> None:
        trace_id = self._get_trace_id(message)
        if trace_id is None:
            return
        self._trace_counts[trace_id] = self._trace_counts.get(trace_id, 0) + 1
        self._trace_events.setdefault(trace_id, asyncio.Event())

    def _node_label(self, node: Node | Endpoint | None) -> str | None:
        if node is None:
            return None
        name = getattr(node, "name", None)
        if name:
            return name
        return getattr(node, "node_id", None)

    def _build_bus_envelope(
        self,
        source: Node | Endpoint | None,
        target: Node | Endpoint | None,
        message: Any,
    ) -> BusEnvelope:
        source_name = self._node_label(source)
        target_name = self._node_label(target)
        edge = f"{source_name or '*'}->{target_name or '*'}"
        headers: Mapping[str, Any] | None = None
        meta: Mapping[str, Any] | None = None
        if isinstance(message, Message):
            headers = message.headers.model_dump()
            meta = dict(message.meta)
        return BusEnvelope(
            edge=edge,
            source=source_name,
            target=target_name,
            trace_id=self._get_trace_id(message),
            payload=message,
            headers=headers,
            meta=meta,
        )

    async def _publish_to_bus(
        self,
        source: Node | Endpoint | None,
        target: Node | Endpoint | None,
        message: Any,
    ) -> None:
        if self._message_bus is None:
            return
        envelope = self._build_bus_envelope(source, target, message)
        try:
            await self._message_bus.publish(envelope)
        except Exception as exc:
            logger.exception(
                "message_bus_publish_failed",
                extra={
                    "event": "message_bus_publish_failed",
                    "edge": envelope.edge,
                    "trace_id": envelope.trace_id,
                    "exception": repr(exc),
                },
            )

    def _schedule_bus_publish(
        self,
        source: Node | Endpoint | None,
        target: Node | Endpoint | None,
        message: Any,
    ) -> None:
        if self._message_bus is None:
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._publish_to_bus(source, target, message))
        self._bus_tasks.add(task)

        def _cleanup(done: asyncio.Task[None]) -> None:
            self._bus_tasks.discard(done)

        task.add_done_callback(_cleanup)

    async def _send_to_floe(self, floe: Floe, message: Any) -> None:
        self._on_message_enqueued(message)
        if self._message_bus is not None:
            await self._publish_to_bus(floe.source, floe.target, message)
        await floe.queue.put(message)

    def _send_to_floe_nowait(self, floe: Floe, message: Any) -> None:
        self._on_message_enqueued(message)
        if self._message_bus is not None:
            self._schedule_bus_publish(floe.source, floe.target, message)
        floe.queue.put_nowait(message)

    async def _finalize_message(self, message: Any) -> None:
        trace_id = self._get_trace_id(message)
        if trace_id is None:
            return

        remaining = self._trace_counts.get(trace_id)
        if remaining is None:
            return

        remaining -= 1
        if remaining <= 0:
            self._trace_counts.pop(trace_id, None)
            event = self._trace_events.pop(trace_id, None)
            if event is not None and event.is_set():
                await self._emit_event(
                    event="trace_cancel_finish",
                    node=ROOKERY,
                    context=self._contexts[ROOKERY],
                    trace_id=trace_id,
                    attempt=0,
                    latency_ms=None,
                    level=logging.INFO,
                )
            self._notify_trace_capacity(trace_id)
            self._latest_wm_hops.pop(trace_id, None)
        else:
            self._trace_counts[trace_id] = remaining
            if self._queue_maxsize <= 0 or remaining <= self._queue_maxsize:
                self._notify_trace_capacity(trace_id)

    async def _drop_trace_from_floe(self, floe: Floe, trace_id: str) -> None:
        queue = floe.queue
        retained: list[Any] = []

        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if self._get_trace_id(item) == trace_id:
                await self._finalize_message(item)
                continue

            retained.append(item)

        for item in retained:
            queue.put_nowait(item)

    async def cancel(self, trace_id: str) -> bool:
        if not self._running:
            raise RuntimeError("PenguiFlow is not running")

        active = trace_id in self._trace_counts or trace_id in self._trace_invocations
        if not active:
            return False

        event = self._trace_events.setdefault(trace_id, asyncio.Event())
        if not event.is_set():
            event.set()
            await self._emit_event(
                event="trace_cancel_start",
                node=OPEN_SEA,
                context=self._contexts[OPEN_SEA],
                trace_id=trace_id,
                attempt=0,
                latency_ms=None,
                level=logging.INFO,
                extra={"pending": self._trace_counts.get(trace_id, 0)},
            )
        else:
            event.set()

        for floe in list(self._floes):
            await self._drop_trace_from_floe(floe, trace_id)

        tasks = list(self._trace_invocations.get(trace_id, set()))
        for task in tasks:
            task.cancel()

        return True

    async def _await_trace_capacity(self, trace_id: str, *, offset: int = 0) -> None:
        if self._queue_maxsize <= 0:
            return

        while True:
            pending = self._trace_counts.get(trace_id, 0)
            effective = pending - offset if pending > offset else 0
            if effective < self._queue_maxsize:
                return
            waiter = asyncio.Event()
            waiters = self._trace_capacity_waiters.setdefault(trace_id, [])
            waiters.append(waiter)
            try:
                await waiter.wait()
            finally:
                remaining_waiters = self._trace_capacity_waiters.get(trace_id)
                if remaining_waiters is not None:
                    try:
                        remaining_waiters.remove(waiter)
                    except ValueError:
                        pass
                    if not remaining_waiters:
                        self._trace_capacity_waiters.pop(trace_id, None)

    def _notify_trace_capacity(self, trace_id: str) -> None:
        waiters = self._trace_capacity_waiters.pop(trace_id, None)
        if not waiters:
            return
        for waiter in waiters:
            waiter.set()

    def _controller_postprocess(
        self,
        node: Node,
        context: Context,
        incoming: Any,
        result: Any,
    ) -> tuple[str, Any, list[Node] | None, bool]:
        if isinstance(result, Message):
            payload = result.payload
            if isinstance(payload, WM):
                now = time.time()
                if result.deadline_s is not None and now > result.deadline_s:
                    final = FinalAnswer(text=DEADLINE_EXCEEDED_TEXT)
                    final_msg = result.model_copy(update={"payload": final})
                    return "rookery", final_msg, None, False

                if (
                    payload.budget_tokens is not None
                    and payload.tokens_used >= payload.budget_tokens
                ):
                    final = FinalAnswer(text=TOKEN_BUDGET_EXCEEDED_TEXT)
                    final_msg = result.model_copy(update={"payload": final})
                    return "rookery", final_msg, None, False

                incoming_hops: int | None = None
                if (
                    isinstance(incoming, Message)
                    and isinstance(incoming.payload, WM)
                ):
                    incoming_hops = incoming.payload.hops

                current_hops = payload.hops
                if incoming_hops is not None and current_hops <= incoming_hops:
                    next_hops = incoming_hops + 1
                else:
                    next_hops = current_hops

                if (
                    payload.budget_hops is not None
                    and next_hops >= payload.budget_hops
                ):
                    final = FinalAnswer(text=BUDGET_EXCEEDED_TEXT)
                    final_msg = result.model_copy(update={"payload": final})
                    return "rookery", final_msg, None, False

                if next_hops != current_hops:
                    updated_payload = payload.model_copy(update={"hops": next_hops})
                    prepared = result.model_copy(update={"payload": updated_payload})
                else:
                    prepared = result

                stream_updates = (
                    payload.budget_hops is None
                    and payload.budget_tokens is None
                )
                return "context", prepared, [node], stream_updates

            if isinstance(payload, FinalAnswer):
                return "rookery", result, None, False

        return "context", result, None, False

    def _deadline_expired(self, message: Any) -> bool:
        if isinstance(message, Message) and message.deadline_s is not None:
            return time.time() > message.deadline_s
        return False

    async def _handle_deadline_expired(
        self, context: Context, message: Message
    ) -> None:
        payload = message.payload
        if not isinstance(payload, FinalAnswer):
            payload = FinalAnswer(text=DEADLINE_EXCEEDED_TEXT)
        final_msg = message.model_copy(update={"payload": payload})
        await self._emit_to_rookery(final_msg, source=context.owner)

    async def _emit_to_rookery(
        self, message: Any, *, source: Node | Endpoint | None = None
    ) -> None:
        """Route ``message`` to the Rookery sink regardless of graph edges."""

        rookery_context = self._contexts[ROOKERY]
        incoming = rookery_context._incoming

        floe: Floe | None = None
        if source is not None:
            floe = incoming.get(source)
        if floe is None and incoming:
            floe = next(iter(incoming.values()))

        if floe is not None:
            await self._send_to_floe(floe, message)
        else:
            self._on_message_enqueued(message)
            if self._message_bus is not None:
                await self._publish_to_bus(source, ROOKERY, message)
            buffer = rookery_context._buffer
            buffer.append(message)

        if isinstance(message, Message):
            payload = message.payload
            if isinstance(payload, WM):
                trace_id = message.trace_id
                self._latest_wm_hops[trace_id] = payload.hops

    async def _emit_event(
        self,
        *,
        event: str,
        node: Node | Endpoint,
        context: Context,
        trace_id: str | None,
        attempt: int,
        latency_ms: float | None,
        level: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        node_name = getattr(node, "name", None)
        node_id = getattr(node, "node_id", node_name)
        queue_depth_in = context.queue_depth_in()
        queue_depth_out = context.queue_depth_out()
        outgoing = context.outgoing_count()

        trace_pending: int | None = None
        trace_inflight = 0
        trace_cancelled = False
        if trace_id is not None:
            trace_pending = self._trace_counts.get(trace_id, 0)
            trace_inflight = len(self._trace_invocations.get(trace_id, set()))
            trace_cancelled = self._is_trace_cancelled(trace_id)

        event_obj = FlowEvent(
            event_type=event,
            ts=time.time(),
            node_name=node_name,
            node_id=node_id,
            trace_id=trace_id,
            attempt=attempt,
            latency_ms=latency_ms,
            queue_depth_in=queue_depth_in,
            queue_depth_out=queue_depth_out,
            outgoing_edges=outgoing,
            queue_maxsize=self._queue_maxsize,
            trace_pending=trace_pending,
            trace_inflight=trace_inflight,
            trace_cancelled=trace_cancelled,
            extra=extra or {},
        )

        logger.log(level, event, extra=event_obj.to_payload())

        if self._state_store is not None:
            stored_event = StoredEvent.from_flow_event(event_obj)
            try:
                await self._state_store.save_event(stored_event)
            except Exception as exc:
                logger.exception(
                    "state_store_save_failed",
                    extra={
                        "event": "state_store_save_failed",
                        "trace_id": stored_event.trace_id,
                        "kind": stored_event.kind,
                        "exception": repr(exc),
                    },
                )

        for middleware in list(self._middlewares):
            try:
                await middleware(event_obj)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "middleware_error",
                    extra={
                        "event": "middleware_error",
                        "node_name": node_name,
                        "node_id": node_id,
                        "exception": exc,
                    },
                )

    def _ensure_registry_covers_nodes(self, registry: ModelRegistry) -> None:
        missing: list[str] = []
        for node in self._nodes:
            if node.policy.validate == "none":
                continue
            node_name = node.name
            if node_name is None:
                continue
            try:
                registry.adapters(node_name)
            except KeyError:
                missing.append(node_name)

        if missing:
            formatted = ", ".join(sorted(missing))
            raise RuntimeError(
                "ModelRegistry is missing entries for nodes requiring validation: "
                f"{formatted}"
            )


PlaybookFactory = Callable[[], tuple["PenguiFlow", ModelRegistry | None]]


async def call_playbook(
    playbook: PlaybookFactory,
    parent_msg: Message,
    timeout: float | None = None,
    *,
    runtime: PenguiFlow | None = None,
) -> Any:
    """Execute a subflow playbook and return the first Rookery payload."""

    flow, registry = playbook()
    flow.run(registry=registry)

    trace_id = getattr(parent_msg, "trace_id", None)
    cancel_watch: asyncio.Task[None] | None = None
    pre_cancelled = False

    if runtime is not None and trace_id is not None:
        parent_event = runtime._trace_events.setdefault(trace_id, asyncio.Event())

        if parent_event.is_set():
            pre_cancelled = True
        else:

            async def _mirror_cancel() -> None:
                try:
                    await parent_event.wait()
                except asyncio.CancelledError:
                    return
                with suppress(Exception):
                    await flow.cancel(trace_id)

            cancel_watch = asyncio.create_task(_mirror_cancel())

    try:
        if pre_cancelled:
            raise TraceCancelled(trace_id)
        await flow.emit(parent_msg)
        fetch_coro = flow.fetch()
        if timeout is not None:
            result_msg = await asyncio.wait_for(fetch_coro, timeout)
        else:
            result_msg = await fetch_coro
        if isinstance(result_msg, Message):
            return result_msg.payload
        return result_msg
    except TraceCancelled:
        if trace_id is not None and not pre_cancelled:
            with suppress(Exception):
                await flow.cancel(trace_id)
        raise
    except asyncio.CancelledError:
        if trace_id is not None:
            with suppress(Exception):
                await flow.cancel(trace_id)
        raise
    finally:
        if cancel_watch is not None:
            cancel_watch.cancel()
            await asyncio.gather(cancel_watch, return_exceptions=True)
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
