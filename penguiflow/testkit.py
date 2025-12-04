"""Utilities for writing concise PenguiFlow tests.

The helpers in this module provide a minimal harness around ``PenguiFlow`` so
unit tests can focus on the behaviour of their nodes instead of the runtime
plumbing.  Each helper intentionally works with the public runtime surface to
avoid relying on private attributes, keeping the harness forward compatible
with the v1 API.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any
from weakref import WeakKeyDictionary

from .core import PenguiFlow
from .errors import FlowErrorCode
from .metrics import FlowEvent
from .types import Headers, Message

__all__ = [
    "run_one",
    "assert_node_sequence",
    "get_recorded_events",
    "simulate_error",
    "assert_preserves_message_envelope",
]


_MAX_TRACE_HISTORY = 64
_TRACE_HISTORY: OrderedDict[str, list[FlowEvent]] = OrderedDict()
_RECORDER_STATE: WeakKeyDictionary[PenguiFlow, _RecorderState] = WeakKeyDictionary()


def _register_trace_history(trace_id: str, events: list[FlowEvent]) -> None:
    if not trace_id:
        return
    if trace_id in _TRACE_HISTORY:
        _TRACE_HISTORY.move_to_end(trace_id)
    _TRACE_HISTORY[trace_id] = events
    while len(_TRACE_HISTORY) > _MAX_TRACE_HISTORY:
        _TRACE_HISTORY.popitem(last=False)


@dataclass(slots=True)
class _RunLog:
    events: list[FlowEvent] = field(default_factory=list)
    traces: dict[str, list[FlowEvent]] = field(default_factory=dict)
    active_traces: set[str] = field(default_factory=set)


class _RecorderState:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._log = _RunLog()
        self._middleware = _Recorder(self)

    @property
    def middleware(self) -> _Recorder:
        return self._middleware

    def begin(self, traces: Iterable[str] | None = None) -> None:
        trace_ids = set(traces or [])
        self._log = _RunLog(active_traces=trace_ids)
        for trace_id in trace_ids:
            bucket: list[FlowEvent] = []
            self._log.traces[trace_id] = bucket
            _register_trace_history(trace_id, bucket)

    async def record(self, event: FlowEvent) -> None:
        async with self._lock:
            self._log.events.append(event)
            trace_id = event.trace_id
            if trace_id is None:
                return
            bucket = self._log.traces.get(trace_id)
            if bucket is None:
                bucket = []
                self._log.traces[trace_id] = bucket
                _register_trace_history(trace_id, bucket)
            bucket.append(event)

    def node_sequence(self, trace_id: str) -> list[str]:
        bucket = self._log.traces.get(trace_id)
        if bucket is None:
            bucket = _TRACE_HISTORY.get(trace_id, [])
        sequence: list[str] = []
        for event in bucket:
            if event.event_type != "node_start":
                continue
            name = event.node_name or event.node_id or "<anonymous>"
            sequence.append(name)
        return sequence


class _Recorder:
    def __init__(self, state: _RecorderState) -> None:
        self._state = state

    async def __call__(self, event: FlowEvent) -> None:
        await self._state.record(event)


class _StubContext:
    async def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def emit_nowait(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def emit_chunk(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            "FlowTestKit stub context does not support emit_chunk; provide a custom context via the 'ctx' parameter"
        )


def _get_state(flow: PenguiFlow) -> _RecorderState:
    state = _RECORDER_STATE.get(flow)
    if state is None:
        state = _RecorderState()
        _RECORDER_STATE[flow] = state
    middlewares = getattr(flow, "_middlewares", None)
    if middlewares is None:
        raise AttributeError("PenguiFlow instance is missing middleware hooks")
    middleware = state.middleware
    if not any(middleware is existing for existing in middlewares):
        middlewares.append(middleware)
    return state


async def run_one(
    flow: PenguiFlow,
    message: Message,
    *,
    registry: Any | None = None,
    timeout_s: float | None = 1.0,
) -> Any:
    """Run ``message`` through ``flow`` and return the first Rookery payload.

    The flow is started and stopped for the caller.  The original message's
    ``trace_id`` is tracked so :func:`assert_node_sequence` can introspect the
    execution order afterwards.
    """

    if not isinstance(message, Message):
        raise TypeError("run_one expects a penguiflow.types.Message instance")

    state = _get_state(flow)
    state.begin([message.trace_id])

    flow.run(registry=registry)
    try:
        await flow.emit(message)
        result_coro = flow.fetch()
        if timeout_s is not None:
            result = await asyncio.wait_for(result_coro, timeout_s)
        else:
            result = await result_coro
    finally:
        await flow.stop()

    return result


async def assert_preserves_message_envelope(
    node: Callable[[Message, Any], Awaitable[Any]] | Any,
    *,
    message: Message | None = None,
    ctx: Any | None = None,
) -> Message:
    """Execute ``node`` and assert it preserves the ``Message`` envelope.

    Parameters
    ----------
    node:
        Either a bare async callable or a :class:`penguiflow.node.Node` whose
        first parameter is a :class:`~penguiflow.types.Message` instance.
    message:
        Optional sample message. When omitted, a minimal envelope is
        synthesised.
    ctx:
        Optional context object passed to the node. By default a stub context
        is used that simply no-ops ``emit``/``emit_nowait``.

    Returns
    -------
    Message
        The resulting message from the node, allowing additional assertions.

    Raises
    ------
    AssertionError
        If the node does not return a ``Message`` or mutates core envelope
        fields (headers or trace_id).
    TypeError
        If ``node`` is not awaitable.
    """

    from .node import Node  # Local import to avoid circular dependency

    if isinstance(node, Node):
        func = node.func
        node_name = node.name or node.func.__name__
    else:
        func = node
        node_name = getattr(node, "__name__", "<anonymous>")

    if not inspect.iscoroutinefunction(func):
        raise TypeError("assert_preserves_message_envelope expects an async node")

    sample = message or Message(payload={}, headers=Headers(tenant="test"))
    context = ctx if ctx is not None else _StubContext()

    result = await func(sample, context)
    if not isinstance(result, Message):
        produced = type(result).__name__
        raise AssertionError(f"Node '{node_name}' must return a Message but produced {produced}")

    mismatches: list[str] = []
    if result.headers != sample.headers:
        mismatches.append("headers")
    if result.trace_id != sample.trace_id:
        mismatches.append("trace_id")

    if mismatches:
        joined = ", ".join(mismatches)
        raise AssertionError(f"Node '{node_name}' altered Message {joined}; preserve the envelope")

    return result


def assert_node_sequence(trace_id: str, expected: Sequence[str]) -> None:
    """Assert that ``expected`` matches the recorded node start order."""

    expected_nodes = list(expected)
    events = _TRACE_HISTORY.get(trace_id, [])
    if not events:
        raise AssertionError(f"No recorded events for trace_id={trace_id!r}; run a flow with run_one first.")

    actual_nodes = [
        event.node_name or event.node_id or "<anonymous>" for event in events if event.event_type == "node_start"
    ]
    actual_nodes = [name for name, _ in groupby(actual_nodes)]
    if actual_nodes != expected_nodes:
        raise AssertionError(f"Node sequence mismatch:\n  expected: {expected_nodes}\n  actual:   {actual_nodes}")


def get_recorded_events(trace_id: str) -> tuple[FlowEvent, ...]:
    """Return the recorded :class:`FlowEvent` history for ``trace_id``.

    The FlowTestKit recorder maintains a bounded cache of trace histories.
    This helper exposes the immutable snapshot so tests can assert on
    diagnostics such as ``node_failed`` payloads or retry attempts without
    touching the private cache directly.
    """

    events = _TRACE_HISTORY.get(trace_id, [])
    return tuple(events)


class _ErrorSimulation:
    def __init__(
        self,
        *,
        node_name: str,
        code: str,
        fail_times: int,
        exception_factory: Callable[[str], Exception],
        result_factory: Callable[[Any], Awaitable[Any] | Any] | None,
    ) -> None:
        self._node_name = node_name
        self._code = code
        self._fail_times = fail_times
        self._exception_factory = exception_factory
        self._result_factory = result_factory
        self._attempts = 0

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def failures(self) -> int:
        return min(self._attempts, self._fail_times)

    async def __call__(self, message: Any, _ctx: Any) -> Any:
        self._attempts += 1
        if self._attempts <= self._fail_times:
            text = f"[{self._code}] simulated failure in {self._node_name} (attempt {self._attempts})"
            raise self._exception_factory(text)

        if self._result_factory is None:
            return message

        result = self._result_factory(message)
        if inspect.isawaitable(result):
            return await result
        return result


def simulate_error(
    node_name: str,
    code: FlowErrorCode | str,
    *,
    fail_times: int = 1,
    result: Any | None = None,
    result_factory: Callable[[Any], Awaitable[Any] | Any] | None = None,
    exception_type: type[Exception] = RuntimeError,
) -> Callable[[Any, Any], Awaitable[Any]]:
    """Return an async callable that fails ``fail_times`` before succeeding.

    The returned coroutine is suitable for wrapping in :class:`~penguiflow.node.Node`
    and is especially useful for retry-centric tests.  By default the callable
    echoes the incoming ``message`` once the simulated failures are exhausted, but
    ``result``/``result_factory`` can override the successful return value.
    """

    if fail_times < 1:
        raise ValueError("fail_times must be >= 1")
    if result is not None and result_factory is not None:
        raise ValueError("Specify only one of result or result_factory")

    resolved_code = code.value if isinstance(code, FlowErrorCode) else str(code)

    def _exception_factory(text: str) -> Exception:
        return exception_type(text)

    if result_factory is None and result is not None:

        async def _const_result(_: Any) -> Any:
            return result

        result_factory = _const_result

    simulation = _ErrorSimulation(
        node_name=node_name,
        code=resolved_code,
        fail_times=fail_times,
        exception_factory=_exception_factory,
        result_factory=result_factory,
    )

    async def _runner(message: Any, ctx: Any) -> Any:
        return await simulation(message, ctx)

    # Attach useful attributes for introspection in tests without exposing the
    # internal class.
    _runner.simulation = simulation  # type: ignore[attr-defined]
    return _runner
