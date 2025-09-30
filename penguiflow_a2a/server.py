"""Expose PenguiFlow runs through an A2A-compliant HTTP surface."""

from __future__ import annotations

import asyncio
import contextvars
import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from types import MethodType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from penguiflow.core import PenguiFlow, TraceCancelled
from penguiflow.errors import FlowError
from penguiflow.state import RemoteBinding
from penguiflow.streaming import format_sse_event
from penguiflow.types import Headers, Message, StreamChunk

_QUEUE_SHUTDOWN = object()
_TRACE_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "penguiflow_a2a_trace", default=None
)


@dataclass(slots=True)
class RookeryResult:
    trace_id: str
    value: Any


class A2ASkill(BaseModel):
    """Description of a single capability exposed by an agent."""

    name: str
    description: str
    mode: str = Field(
        default="both",
        description="Whether the skill supports message/send, message/stream, or both.",
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class A2AAgentCard(BaseModel):
    """Lightweight Agent Card surfaced at ``GET /agent``."""

    name: str
    description: str
    version: str = "1.0.0"
    schema_version: str = Field(default="1.0")
    tags: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    skills: list[A2ASkill] = Field(default_factory=list)
    contact_url: str | None = None
    documentation_url: str | None = None

    model_config = ConfigDict(extra="allow")

    def to_payload(self) -> dict[str, Any]:
        """Return a serialisable dictionary representation."""

        return self.model_dump()


class A2AMessagePayload(BaseModel):
    """Request payload accepted by ``message/send`` and ``message/stream``."""

    payload: Any
    headers: Mapping[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, alias="traceId")
    context_id: str | None = Field(default=None, alias="contextId")
    task_id: str | None = Field(default=None, alias="taskId")
    deadline_s: float | None = Field(default=None, alias="deadlineSeconds")

    model_config = ConfigDict(populate_by_name=True)


class A2ATaskCancelRequest(BaseModel):
    """JSON body accepted by ``tasks/cancel``."""

    task_id: str = Field(alias="taskId")

    model_config = ConfigDict(populate_by_name=True)


class A2ARequestError(Exception):
    """Exception converted to ``HTTPException`` inside the FastAPI app."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class A2AServerAdapter:
    """Bridge between PenguiFlow and the A2A HTTP surface."""

    def __init__(
        self,
        flow: PenguiFlow,
        *,
        agent_card: A2AAgentCard | Mapping[str, Any],
        agent_url: str,
        target: Sequence[Any] | Any | None = None,
        registry: Any | None = None,
        default_headers: Mapping[str, Any] | None = None,
    ) -> None:
        self._flow = flow
        self._registry = registry
        self._target = target
        self._default_headers = dict(default_headers or {})
        self.agent_card = (
            agent_card
            if isinstance(agent_card, A2AAgentCard)
            else A2AAgentCard.model_validate(agent_card)
        )
        self.agent_url = agent_url
        self._flow_started = False
        self._tasks: dict[str, str] = {}
        self._contexts: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._queue_lock = asyncio.Lock()
        self._trace_queues: dict[str, asyncio.Queue[Any]] = {}
        self._pending_results: dict[str, list[Any]] = {}
        self._cancel_watchers: dict[str, asyncio.Task[None]] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._message_traces: dict[int, str] = {}
        self._patch_flow()

    async def start(self) -> None:
        """Start the underlying flow if it is not running."""

        if self._flow_started:
            return
        self._flow.run(registry=self._registry)
        self._flow_started = True
        self._ensure_dispatcher_task()

    async def stop(self) -> None:
        """Gracefully stop the underlying flow."""

        if not self._flow_started:
            return
        dispatcher = self._dispatcher_task
        self._dispatcher_task = None
        if dispatcher is not None:
            dispatcher.cancel()
        await self._flow.stop()
        if dispatcher is not None:
            with suppress(asyncio.CancelledError):
                await dispatcher
        self._flow_started = False
        async with self._queue_lock:
            queues = list(self._trace_queues.values())
            cancel_watchers = list(self._cancel_watchers.values())
            self._trace_queues.clear()
            self._pending_results.clear()
            self._cancel_watchers.clear()
        for watcher in cancel_watchers:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        for queue in queues:
            queue.put_nowait(_QUEUE_SHUTDOWN)

    def _ensure_started(self) -> None:
        if not self._flow_started:
            raise A2ARequestError(status_code=503, detail="flow is not running")

    async def handle_send(self, request: A2AMessagePayload) -> dict[str, Any]:
        """Execute ``message/send`` and return the final artifact."""

        self._ensure_started()
        message, task_id, context_id = self._prepare_message(request)
        await self._register_task(task_id, message.trace_id, context_id)
        await self._persist_binding(message.trace_id, context_id, task_id)
        result_queue = await self._acquire_trace_queue(message.trace_id)

        try:
            await self._flow.emit(message, to=self._target)
            while True:
                item = await result_queue.get()
                if item is _QUEUE_SHUTDOWN:
                    raise A2ARequestError(
                        status_code=503, detail="flow is shutting down"
                    )
                if isinstance(item, TraceCancelled):
                    raise item
                if isinstance(item, FlowError):
                    raise item
                if isinstance(item, Exception):  # pragma: no cover - defensive
                    raise item
                if isinstance(item, RookeryResult):
                    payload_candidate = item.value
                else:
                    payload_candidate = getattr(item, "payload", item)
                if isinstance(payload_candidate, StreamChunk):
                    continue
                result = item
                break
            if isinstance(result, RookeryResult):
                payload = result.value
            else:
                payload = getattr(result, "payload", result)
            response: dict[str, Any] = {
                "status": "succeeded",
                "taskId": task_id,
                "contextId": context_id,
                "traceId": message.trace_id,
                "output": self._to_jsonable(payload),
            }
            meta = getattr(result, "meta", None)
            if meta:
                response["meta"] = dict(meta)
            return response
        except TraceCancelled:
            return {
                "status": "cancelled",
                "taskId": task_id,
                "contextId": context_id,
                "traceId": message.trace_id,
            }
        except FlowError as exc:
            error_payload = exc.to_payload()
            error_payload.setdefault("trace_id", message.trace_id)
            return {
                "status": "failed",
                "taskId": task_id,
                "contextId": context_id,
                "traceId": message.trace_id,
                "error": error_payload,
            }
        except A2ARequestError:
            raise
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise A2ARequestError(
                status_code=500,
                detail=f"flow execution failed: {exc}",
            ) from exc
        finally:
            await self._release_task(task_id)
            await self._release_trace_queue(message.trace_id)

    async def stream(
        self, request: A2AMessagePayload
    ) -> tuple[AsyncIterator[bytes], str, str]:
        """Execute ``message/stream`` and return an SSE iterator."""

        self._ensure_started()
        message, task_id, context_id = self._prepare_message(request)
        await self._register_task(task_id, message.trace_id, context_id)
        await self._persist_binding(message.trace_id, context_id, task_id)
        await self._acquire_trace_queue(message.trace_id)
        generator = self._stream_generator(message, task_id, context_id)
        return generator, task_id, context_id

    async def cancel(self, request: A2ATaskCancelRequest) -> dict[str, Any]:
        """Cancel an active task."""

        self._ensure_started()
        task_id = request.task_id
        async with self._lock:
            trace_id = self._tasks.get(task_id)
            context_id = self._contexts.get(task_id)
        if trace_id is None:
            return {"taskId": task_id, "cancelled": False}
        cancelled = await self._flow.cancel(trace_id)
        response = {
            "taskId": task_id,
            "cancelled": cancelled,
            "traceId": trace_id,
        }
        if context_id is not None:
            response["contextId"] = context_id
        return response

    def _prepare_message(
        self, request: A2AMessagePayload
    ) -> tuple[Message, str, str]:
        headers_data = {**self._default_headers, **dict(request.headers)}
        try:
            headers = Headers(**headers_data)
        except ValidationError as exc:  # pragma: no cover - pydantic formats nicely
            raise A2ARequestError(status_code=422, detail=str(exc)) from exc

        kwargs: dict[str, Any] = {}
        if request.trace_id is not None:
            kwargs["trace_id"] = request.trace_id
        if request.deadline_s is not None:
            kwargs["deadline_s"] = request.deadline_s
        message = Message(payload=request.payload, headers=headers, **kwargs)
        message.meta.update(request.meta)

        context_id = request.context_id or message.trace_id
        task_id = request.task_id or message.trace_id or uuid.uuid4().hex
        return message, task_id, context_id

    async def _register_task(
        self, task_id: str, trace_id: str, context_id: str
    ) -> None:
        async with self._lock:
            if task_id in self._tasks:
                raise A2ARequestError(
                    status_code=409, detail=f"task {task_id!r} already active"
                )
            self._tasks[task_id] = trace_id
            self._contexts[task_id] = context_id

    async def _release_task(self, task_id: str) -> None:
        async with self._lock:
            self._tasks.pop(task_id, None)
            self._contexts.pop(task_id, None)

    async def _persist_binding(
        self, trace_id: str, context_id: str, task_id: str
    ) -> None:
        binding = RemoteBinding(
            trace_id=trace_id,
            context_id=context_id,
            task_id=task_id,
            agent_url=self.agent_url,
        )
        await self._flow.save_remote_binding(binding)

    async def _stream_generator(
        self, message: Message, task_id: str, context_id: str
    ) -> AsyncIterator[bytes]:
        result_queue = await self._get_trace_queue(message.trace_id)
        try:
            await self._flow.emit(message, to=self._target)
            yield self._format_event(
                "status",
                {
                    "status": "accepted",
                    "taskId": task_id,
                    "contextId": context_id,
                },
            )
            while True:
                item = await result_queue.get()
                if item is _QUEUE_SHUTDOWN:
                    raise A2ARequestError(
                        status_code=503, detail="flow is shutting down"
                    )
                if isinstance(item, TraceCancelled):
                    raise item
                if isinstance(item, FlowError):
                    raise item
                if isinstance(item, Exception):  # pragma: no cover - defensive
                    raise item
                if isinstance(item, RookeryResult):
                    payload = item.value
                else:
                    payload = getattr(item, "payload", item)
                if isinstance(payload, StreamChunk):
                    yield self._format_chunk_event(payload, task_id, context_id)
                    continue
                yield self._format_event(
                    "artifact",
                    {
                        "taskId": task_id,
                        "contextId": context_id,
                        "output": self._to_jsonable(payload),
                    },
                )
                break
            yield self._format_event(
                "done", {"taskId": task_id, "contextId": context_id}
            )
        except TraceCancelled:
            yield self._format_event(
                "error",
                {
                    "taskId": task_id,
                    "contextId": context_id,
                    "code": "TRACE_CANCELLED",
                    "message": "Trace cancelled",
                },
            )
            yield self._format_event(
                "done", {"taskId": task_id, "contextId": context_id}
            )
        except FlowError as exc:
            payload = exc.to_payload()
            payload.update({"taskId": task_id, "contextId": context_id})
            yield self._format_event("error", payload)
            yield self._format_event(
                "done", {"taskId": task_id, "contextId": context_id}
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            yield self._format_event(
                "error",
                {
                    "taskId": task_id,
                    "contextId": context_id,
                    "code": "INTERNAL_ERROR",
                    "message": str(exc) or exc.__class__.__name__,
                },
            )
            yield self._format_event(
                "done", {"taskId": task_id, "contextId": context_id}
            )
        finally:
            await self._release_task(task_id)
            await self._release_trace_queue(message.trace_id)

    def _format_event(self, event: str, data: Mapping[str, Any]) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode()

    def _format_chunk_event(
        self, chunk: StreamChunk, task_id: str, context_id: str
    ) -> bytes:
        meta = dict(chunk.meta)
        meta.setdefault("taskId", task_id)
        meta.setdefault("contextId", context_id)
        enriched = chunk.model_copy(update={"meta": meta})
        return format_sse_event(enriched).encode("utf-8")

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump()
        if isinstance(value, Message):
            return {
                "payload": self._to_jsonable(value.payload),
                "headers": value.headers.model_dump(),
                "trace_id": value.trace_id,
                "meta": dict(value.meta),
            }
        if isinstance(value, RookeryResult):
            return self._to_jsonable(value.value)
        if isinstance(value, Mapping):
            return {k: self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, list | tuple | set):
            return [self._to_jsonable(item) for item in value]
        return value

    def _patch_flow(self) -> None:
        flow = self._flow
        if getattr(flow, "_a2a_adapter_patched", False):
            return
        required = (
            "_emit_to_rookery",
            "_execute_with_reliability",
            "_on_message_enqueued",
        )
        if not all(hasattr(flow, name) for name in required):
            return

        original_emit = flow._emit_to_rookery
        original_execute = flow._execute_with_reliability
        original_on_enqueue = flow._on_message_enqueued

        async def emit_with_trace(
            flow_self: PenguiFlow,
            message: Any,
            *,
            source: Any | None = None,
        ) -> None:
            trace_id = getattr(message, "trace_id", None)
            if trace_id is None:
                context_trace = _TRACE_CONTEXT.get()
                if context_trace is not None:
                    self._message_traces[id(message)] = context_trace
                    message = RookeryResult(trace_id=context_trace, value=message)
            await original_emit(message, source=source)

        async def execute_with_trace(
            flow_self: PenguiFlow,
            node: Any,
            context: Any,
            message: Any,
        ) -> None:
            trace_id = getattr(message, "trace_id", None)
            token = _TRACE_CONTEXT.set(trace_id)
            try:
                return await original_execute(node, context, message)
            finally:
                _TRACE_CONTEXT.reset(token)

        def on_enqueue_with_trace(flow_self: PenguiFlow, message: Any) -> None:
            trace_id = flow_self._get_trace_id(message)
            if trace_id is None:
                context_trace = _TRACE_CONTEXT.get()
                if context_trace is not None:
                    self._message_traces[id(message)] = context_trace
            original_on_enqueue(message)

        object.__setattr__(flow, "_emit_to_rookery", MethodType(emit_with_trace, flow))
        object.__setattr__(
            flow,
            "_execute_with_reliability",
            MethodType(execute_with_trace, flow),
        )
        object.__setattr__(
            flow,
            "_on_message_enqueued",
            MethodType(on_enqueue_with_trace, flow),
        )
        object.__setattr__(flow, "_a2a_adapter_patched", True)

    def _ensure_dispatcher_task(self) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        loop = asyncio.get_running_loop()
        self._dispatcher_task = loop.create_task(self._dispatch_results())

    async def _dispatch_results(self) -> None:
        try:
            while True:
                counts_before = await self._snapshot_trace_counts()
                item = await self._flow.fetch()
                trace_id = getattr(item, "trace_id", None)
                if trace_id is None:
                    trace_id = self._message_traces.pop(id(item), None)
                counts_after = await self._snapshot_trace_counts()
                if trace_id is None:
                    trace_id = self._infer_trace_from_counts(
                        counts_before, counts_after
                    )
                if trace_id is None:
                    async with self._queue_lock:
                        active_traces = list(self._trace_queues.keys())
                    if len(active_traces) == 1:
                        trace_id = active_traces[0]
                if trace_id is None:
                    raise RuntimeError("unable to determine trace for rookery payload")
                async with self._queue_lock:
                    queue = self._trace_queues.get(trace_id)
                    if queue is None:
                        pending = self._pending_results.setdefault(trace_id, [])
                        pending.append(item)
                        continue
                await queue.put(item)
        except asyncio.CancelledError:
            raise

    async def _acquire_trace_queue(self, trace_id: str) -> asyncio.Queue[Any]:
        self._ensure_dispatcher_task()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        cancel_event = self._flow.ensure_trace_event(trace_id)
        watcher = asyncio.create_task(
            self._wait_for_cancellation(trace_id, cancel_event)
        )
        async with self._queue_lock:
            if trace_id in self._trace_queues:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
                raise A2ARequestError(
                    status_code=409, detail=f"trace {trace_id!r} already active"
                )
            self._trace_queues[trace_id] = queue
            self._cancel_watchers[trace_id] = watcher
            pending = self._pending_results.pop(trace_id, [])
        for item in pending:
            await queue.put(item)
        return queue

    async def _get_trace_queue(self, trace_id: str) -> asyncio.Queue[Any]:
        async with self._queue_lock:
            queue = self._trace_queues.get(trace_id)
        if queue is None:
            raise A2ARequestError(status_code=503, detail="trace queue missing")
        return queue

    async def _release_trace_queue(self, trace_id: str) -> None:
        async with self._queue_lock:
            queue = self._trace_queues.pop(trace_id, None)
            self._pending_results.pop(trace_id, None)
            watcher = self._cancel_watchers.pop(trace_id, None)
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()

    async def _wait_for_cancellation(
        self, trace_id: str, event: asyncio.Event
    ) -> None:
        try:
            await event.wait()
            async with self._queue_lock:
                queue = self._trace_queues.get(trace_id)
            if queue is not None:
                await queue.put(TraceCancelled(trace_id))
        except asyncio.CancelledError:
            raise

    async def _snapshot_trace_counts(self) -> dict[str, int]:
        async with self._queue_lock:
            active = list(self._trace_queues.keys())
        return {trace: self._flow._trace_counts.get(trace, 0) for trace in active}

    def _infer_trace_from_counts(
        self, before: Mapping[str, int], after: Mapping[str, int]
    ) -> str | None:
        candidates: list[str] = []
        for trace_id, before_count in before.items():
            after_count = after.get(trace_id)
            if after_count is None or after_count < before_count:
                candidates.append(trace_id)
        if candidates:
            if len(candidates) == 1:
                return candidates[0]
            return None
        new_traces = [trace_id for trace_id in after.keys() if trace_id not in before]
        if len(new_traces) == 1:
            return new_traces[0]
        return None


def create_a2a_app(
    adapter: A2AServerAdapter, *, include_docs: bool = True
):  # pragma: no cover - exercised via tests
    """Create a FastAPI application exposing the A2A surface."""

    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import StreamingResponse
    except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "FastAPI is required for the A2A server adapter."
            " Install penguiflow[a2a-server]."
        ) from exc

    docs_url = "/docs" if include_docs else None
    openapi_url = "/openapi.json" if include_docs else None

    @asynccontextmanager
    async def lifespan(_app):  # pragma: no cover - executed in tests via router context
        await adapter.start()
        try:
            yield
        finally:
            await adapter.stop()

    app = FastAPI(
        title=adapter.agent_card.name,
        description=adapter.agent_card.description,
        version=adapter.agent_card.version,
        docs_url=docs_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    @app.get("/agent")
    async def get_agent() -> dict[str, Any]:
        return adapter.agent_card.to_payload()

    @app.post("/message/send")
    async def message_send(payload: A2AMessagePayload) -> dict[str, Any]:
        try:
            return await adapter.handle_send(payload)
        except A2ARequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/message/stream")
    async def message_stream(payload: A2AMessagePayload):
        try:
            generator, task_id, context_id = await adapter.stream(payload)
        except A2ARequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        response = StreamingResponse(generator, media_type="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-A2A-Task-Id"] = task_id
        response.headers["X-A2A-Context-Id"] = context_id
        return response

    @app.post("/tasks/cancel")
    async def cancel_task(payload: A2ATaskCancelRequest) -> dict[str, Any]:
        try:
            return await adapter.cancel(payload)
        except A2ARequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return app


__all__ = [
    "A2AAgentCard",
    "A2AServerAdapter",
    "A2AMessagePayload",
    "A2ASkill",
    "create_a2a_app",
]
