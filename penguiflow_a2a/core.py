from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MethodType
from typing import Any

from pydantic import BaseModel

from penguiflow.core import PenguiFlow, TraceCancelled
from penguiflow.core import _RookeryResult as FlowRookeryResult
from penguiflow.errors import FlowError
from penguiflow.state import RemoteBinding
from penguiflow.types import Headers, StreamChunk
from penguiflow.types import Message as FlowMessage

from .config import A2AConfig, PayloadMode
from .errors import (
    A2ARequestValidationError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from .models import (
    AgentCard,
    AgentExtension,
    Artifact,
    DataPart,
    ListTaskPushNotificationConfigResponse,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from .push import HttpPushNotificationSender, PushNotificationSender
from .store import InMemoryTaskStore, TaskStore

_QUEUE_SHUTDOWN = object()
_TRACE_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar("penguiflow_a2a_trace", default=None)

TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.REJECTED,
}


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_version(value: str) -> str:
    trimmed = value.strip()
    parts = trimmed.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return trimmed


@dataclass(slots=True)
class RookeryResult:
    trace_id: str
    value: Any


class FlowDispatcher:
    def __init__(self, flow: PenguiFlow) -> None:
        self._flow = flow
        self._queue_lock = asyncio.Lock()
        self._trace_queues: dict[str, asyncio.Queue[Any]] = {}
        self._pending_results: dict[str, list[Any]] = {}
        self._cancel_watchers: dict[str, asyncio.Task[None]] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._message_traces: dict[int, str] = {}
        self._use_trace_queues = hasattr(flow, "_ensure_fetch_dispatcher") and hasattr(flow, "_fetch_trace_queues")
        if not self._use_trace_queues:
            self._patch_flow()

    async def start(self) -> None:
        if self._use_trace_queues:
            return
        self._ensure_dispatcher_task()

    async def stop(self) -> None:
        dispatcher = self._dispatcher_task
        self._dispatcher_task = None
        if dispatcher is not None:
            dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher
        async with self._queue_lock:
            queues = list(self._trace_queues.values())
            active_traces = list(self._trace_queues.keys())
            watchers = list(self._cancel_watchers.values())
            self._trace_queues.clear()
            self._pending_results.clear()
            self._cancel_watchers.clear()
        for watcher in watchers:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if self._use_trace_queues:
            release = getattr(self._flow, "_release_trace_roundtrip", None)
            if release is not None:
                for trace_id in active_traces:
                    await release(trace_id)
            return
        for queue in queues:
            queue.put_nowait(_QUEUE_SHUTDOWN)

    async def acquire_queue(self, trace_id: str) -> asyncio.Queue[Any]:
        if self._use_trace_queues:
            queue = await self._ensure_trace_queue(trace_id)
        else:
            self._ensure_dispatcher_task()
            queue = asyncio.Queue()
        cancel_event = self._flow.ensure_trace_event(trace_id)
        watcher = asyncio.create_task(self._wait_for_cancellation(trace_id, cancel_event))
        async with self._queue_lock:
            if trace_id in self._trace_queues:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
                raise RuntimeError(f"trace {trace_id!r} already active")
            self._trace_queues[trace_id] = queue
            self._cancel_watchers[trace_id] = watcher
            pending = self._pending_results.pop(trace_id, []) if not self._use_trace_queues else []
        if pending:
            for item in pending:
                await queue.put(item)
        return queue

    async def release_queue(self, trace_id: str) -> None:
        async with self._queue_lock:
            queue = self._trace_queues.pop(trace_id, None)
            self._pending_results.pop(trace_id, None)
            watcher = self._cancel_watchers.pop(trace_id, None)
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if self._use_trace_queues:
            release = getattr(self._flow, "_release_trace_roundtrip", None)
            if release is not None:
                await release(trace_id)
            return
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()

    async def _ensure_trace_queue(self, trace_id: str) -> asyncio.Queue[Any]:
        ensure_dispatcher = getattr(self._flow, "_ensure_fetch_dispatcher", None)
        if ensure_dispatcher is None:
            raise RuntimeError("Flow does not support trace-scoped fetch")
        await ensure_dispatcher()
        async with self._flow._fetch_queue_lock:
            self._flow._fetch_subscribed_traces.add(trace_id)
            queue_maxsize = self._flow._fetch_queue_maxsize
            if queue_maxsize is None:
                queue_maxsize = self._flow._queue_maxsize
            queue = self._flow._fetch_trace_queues.setdefault(
                trace_id,
                asyncio.Queue(maxsize=queue_maxsize),
            )
        return queue

    def _patch_flow(self) -> None:
        flow = self._flow
        owner = getattr(flow, "_a2a_patch_owner", None)
        if owner is self:
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
        object.__setattr__(flow, "_a2a_patch_owner", self)

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
                    trace_id = self._infer_trace_from_counts(counts_before, counts_after)
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

    async def _wait_for_cancellation(self, trace_id: str, event: asyncio.Event) -> None:
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

    def _infer_trace_from_counts(self, before: Mapping[str, int], after: Mapping[str, int]) -> str | None:
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


class A2AService:
    def __init__(
        self,
        flow: PenguiFlow,
        *,
        agent_card: AgentCard,
        config: A2AConfig | None = None,
        store: TaskStore | None = None,
        target: Sequence[Any] | Any | None = None,
        registry: Any | None = None,
        default_headers: Mapping[str, Any] | None = None,
        push_sender: PushNotificationSender | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_agent_card_auth: Callable[[Mapping[str, str]], bool] | None = None,
    ) -> None:
        self._flow = flow
        self._agent_card = agent_card
        self._config = config or A2AConfig()
        self._store = store or InMemoryTaskStore()
        self._target = target
        self._registry = registry
        self._default_headers = dict(default_headers or {})
        self._dispatcher = FlowDispatcher(flow)
        self._flow_started = False
        self._owns_flow = False
        self._start_lock = asyncio.Lock()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._push_sender = push_sender or HttpPushNotificationSender()
        self._extended_agent_card = extended_agent_card
        self._extended_agent_card_auth = extended_agent_card_auth
        self._ensure_protocol_versions()

    @property
    def agent_card(self) -> AgentCard:
        return self._agent_card

    @property
    def config(self) -> A2AConfig:
        return self._config

    async def start(self) -> None:
        if self._flow_started:
            return
        if hasattr(self._flow, "_emit_errors_to_rookery"):
            object.__setattr__(self._flow, "_emit_errors_to_rookery", True)
        if getattr(self._flow, "_running", False):
            self._owns_flow = False
        else:
            self._flow.run(registry=self._registry)
            self._owns_flow = True
        self._flow_started = True
        await self._dispatcher.start()

    async def stop(self) -> None:
        if not self._flow_started:
            return
        for task in list(self._running_tasks.values()):
            task.cancel()
        await self._dispatcher.stop()
        if self._owns_flow:
            await self._flow.stop()
        self._flow_started = False

    def validate_version(self, requested_version: str | None) -> str:
        if requested_version is None:
            return self._config.default_version or self._config.supported_versions[0]
        normalized = _normalize_version(requested_version)
        if not self._config.is_version_supported(normalized):
            raise VersionNotSupportedError(normalized, self._config.supported_versions)
        return normalized

    def validate_extensions(self, requested_extensions: list[str]) -> None:
        capabilities = self._agent_card.capabilities
        required = [
            extension.uri
            for extension in (capabilities.extensions or [])
            if isinstance(extension, AgentExtension) and extension.required
        ]
        missing = [uri for uri in required if uri not in requested_extensions]
        if missing:
            raise ExtensionSupportRequiredError(missing)

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        tenant: str | None,
        requested_extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        await self._ensure_started()
        self._validate_push_config(request)
        task_id, context_id, existing_task = await self._resolve_task(request)
        message = self._enrich_message(request.message, task_id, context_id)
        if existing_task is None:
            await self._create_task_record(task_id, context_id, message)
        else:
            await self._store.add_history(task_id, message)
        if request.configuration and request.configuration.push_notification_config:
            await self._register_push_config(task_id, request.configuration.push_notification_config)
        flow_message = self._build_flow_message(
            request,
            message=message,
            task_id=task_id,
            context_id=context_id,
            tenant=tenant,
            requested_extensions=requested_extensions or [],
        )
        blocking = bool(request.configuration and request.configuration.blocking)
        await self._start_task_run(task_id, context_id, flow_message, blocking=blocking)
        task = await self._store.get_task(
            task_id,
            history_length=(request.configuration.history_length if request.configuration else None),
        )
        return SendMessageResponse(task=task)

    async def stream_message(
        self,
        request: SendMessageRequest,
        *,
        tenant: str | None,
        requested_extensions: list[str] | None = None,
    ) -> tuple[Task, asyncio.Queue[StreamResponse], Callable[[], Coroutine[Any, Any, None]]]:
        await self._ensure_started()
        if not self._agent_card.capabilities.streaming:
            raise UnsupportedOperationError("Streaming is not supported by this agent.")
        self._validate_push_config(request)
        task_id, context_id, existing_task = await self._resolve_task(request)
        message = self._enrich_message(request.message, task_id, context_id)
        if existing_task is None:
            await self._create_task_record(task_id, context_id, message)
        else:
            await self._store.add_history(task_id, message)
        if request.configuration and request.configuration.push_notification_config:
            await self._register_push_config(task_id, request.configuration.push_notification_config)
        flow_message = self._build_flow_message(
            request,
            message=message,
            task_id=task_id,
            context_id=context_id,
            tenant=tenant,
            requested_extensions=requested_extensions or [],
        )
        queue, unsubscribe = await self._store.subscribe(task_id)
        await self._start_task_run(task_id, context_id, flow_message, blocking=False)
        task = await self._store.get_task(task_id, history_length=None)
        return task, queue, unsubscribe

    async def get_task(self, task_id: str, *, history_length: int | None) -> Task:
        await self._ensure_started()
        try:
            return await self._store.get_task(task_id, history_length=history_length)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc

    async def list_tasks(
        self,
        *,
        context_id: str | None,
        status: TaskState | None,
        page_size: int,
        page_token: str | None,
        history_length: int | None,
        status_timestamp_after: datetime | None,
        include_artifacts: bool,
    ):
        await self._ensure_started()
        return await self._store.list_tasks(
            context_id=context_id,
            status=status,
            page_size=page_size,
            page_token=page_token,
            history_length=history_length,
            status_timestamp_after=status_timestamp_after,
            include_artifacts=include_artifacts,
        )

    async def cancel_task(self, task_id: str) -> Task:
        await self._ensure_started()
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            raise TaskNotCancelableError(task_id)
        cancelled = await self._flow.cancel(task_id)
        if not cancelled:
            raise TaskNotCancelableError(task_id)
        status = TaskStatus(state=TaskState.CANCELLED, timestamp=_utc_iso())
        await self._update_status(task_id, task.context_id, status, final=True)
        return await self._store.get_task(task_id)

    async def subscribe_task(
        self, task_id: str
    ) -> tuple[Task, asyncio.Queue[StreamResponse], Callable[[], Coroutine[Any, Any, None]]]:
        await self._ensure_started()
        if not self._agent_card.capabilities.streaming:
            raise UnsupportedOperationError("Streaming is not supported by this agent.")
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            raise UnsupportedOperationError("Cannot subscribe to a terminal task.")
        queue, unsubscribe = await self._store.subscribe(task_id)
        return task, queue, unsubscribe

    async def get_extended_agent_card(self) -> AgentCard:
        await self._ensure_started()
        if not self._agent_card.capabilities.extended_agent_card:
            raise UnsupportedOperationError("Extended agent card is not supported by this agent.")
        if self._extended_agent_card is None:
            raise ExtendedAgentCardNotConfiguredError()
        return self._extended_agent_card.model_copy(deep=True)

    def is_extended_agent_card_authorized(self, headers: Mapping[str, str]) -> bool:
        if self._extended_agent_card_auth is None:
            return True
        return bool(self._extended_agent_card_auth(headers))

    async def set_task_push_notification_config(
        self,
        task_id: str,
        *,
        config_id: str,
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        await self._ensure_started()
        self._ensure_push_supported()
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            raise UnsupportedOperationError("Cannot add push config to a terminal task.")
        canonical_name = self._push_config_name(task_id, config_id)
        if config.name and config.name != canonical_name:
            raise A2ARequestValidationError("pushNotificationConfig.name does not match task/config id")
        push_config = config.push_notification_config
        if push_config.id and push_config.id != config_id:
            raise A2ARequestValidationError("pushNotificationConfig.id does not match configId")
        push_config = push_config.model_copy(update={"id": config_id})
        normalized = TaskPushNotificationConfig(name=canonical_name, push_notification_config=push_config)
        await self._store.set_push_config(task_id, config_id, normalized)
        return normalized

    async def get_task_push_notification_config(self, task_id: str, *, config_id: str) -> TaskPushNotificationConfig:
        await self._ensure_started()
        self._ensure_push_supported()
        try:
            return await self._store.get_push_config(task_id, config_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc

    async def list_task_push_notification_configs(
        self,
        task_id: str,
        *,
        page_size: int,
        page_token: str | None,
    ) -> ListTaskPushNotificationConfigResponse:
        await self._ensure_started()
        self._ensure_push_supported()
        if page_size < 1:
            raise A2ARequestValidationError("pageSize must be >= 1")
        try:
            return await self._store.list_push_configs(
                task_id,
                page_size=page_size,
                page_token=page_token,
            )
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        except ValueError as exc:
            raise A2ARequestValidationError("pageToken is invalid") from exc

    async def delete_task_push_notification_config(self, task_id: str, *, config_id: str) -> None:
        await self._ensure_started()
        self._ensure_push_supported()
        try:
            await self._store.delete_push_config(task_id, config_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc

    def _ensure_protocol_versions(self) -> None:
        supported = set(self._config.supported_versions)
        card_versions = set(self._agent_card.protocol_versions)
        if not supported.issubset(card_versions):
            raise ValueError("AgentCard.protocol_versions must include all supported versions")

    async def _ensure_started(self) -> None:
        if self._flow_started:
            return
        async with self._start_lock:
            if not self._flow_started:
                await self.start()

    def _validate_push_config(self, request: SendMessageRequest) -> None:
        if not request.configuration or not request.configuration.push_notification_config:
            return
        if not self._agent_card.capabilities.push_notifications:
            raise PushNotificationNotSupportedError()

    def _ensure_push_supported(self) -> None:
        if not self._agent_card.capabilities.push_notifications:
            raise PushNotificationNotSupportedError()

    async def _create_task_record(self, task_id: str, context_id: str, message: Message) -> Task:
        status = TaskStatus(state=TaskState.SUBMITTED, timestamp=_utc_iso())
        task = Task(
            id=task_id,
            context_id=context_id,
            status=status,
            history=[message],
        )
        return await self._store.create_task(task)

    async def _resolve_task(self, request: SendMessageRequest) -> tuple[str, str, Task | None]:
        message = request.message
        if message.role != Role.USER:
            raise A2ARequestValidationError("message.role must be 'user'")
        if message.task_id:
            try:
                existing = await self._store.get_task(message.task_id)
            except KeyError as exc:
                raise TaskNotFoundError(message.task_id) from exc
            if existing.status.state in TERMINAL_STATES:
                raise UnsupportedOperationError("Cannot send a message to a terminal task.")
            if message.context_id and message.context_id != existing.context_id:
                raise A2ARequestValidationError("message.contextId does not match task context")
            return existing.id, existing.context_id, existing
        task_id = uuid.uuid4().hex
        context_id = message.context_id or uuid.uuid4().hex
        return task_id, context_id, None

    def _enrich_message(self, message: Message, task_id: str, context_id: str) -> Message:
        updates: dict[str, str] = {}
        if message.task_id is None:
            updates["task_id"] = task_id
        if message.context_id is None:
            updates["context_id"] = context_id
        if not updates:
            return message
        return message.model_copy(update=updates)

    async def _register_push_config(self, task_id: str, config: PushNotificationConfig) -> None:
        self._ensure_push_supported()
        config_id = config.id or uuid.uuid4().hex
        canonical_name = self._push_config_name(task_id, config_id)
        push_config = config.model_copy(update={"id": config_id})
        wrapper = TaskPushNotificationConfig(
            name=canonical_name,
            push_notification_config=push_config,
        )
        await self._store.set_push_config(task_id, config_id, wrapper)

    def _push_config_name(self, task_id: str, config_id: str) -> str:
        return f"tasks/{task_id}/pushNotificationConfigs/{config_id}"

    def _build_flow_message(
        self,
        request: SendMessageRequest,
        *,
        message: Message,
        task_id: str,
        context_id: str,
        tenant: str | None,
        requested_extensions: list[str],
    ) -> FlowMessage:
        envelope = {
            "message": message.model_dump(by_alias=True, exclude_none=True),
            "metadata": request.metadata,
            "configuration": request.configuration.model_dump(by_alias=True, exclude_none=True)
            if request.configuration
            else None,
        }
        payload: Any = {"a2a": envelope}
        if self._config.payload_mode == PayloadMode.AUTO:
            payload = self._maybe_unwrap_payload(message, payload)
        headers_data = {**self._default_headers}
        headers_data["tenant"] = tenant or self._config.default_tenant
        headers = Headers(**headers_data)
        flow_message = FlowMessage(payload=payload, headers=headers, trace_id=task_id)
        flow_message.meta.update(
            {
                "a2a_task_id": task_id,
                "a2a_context_id": context_id,
                "a2a_message_id": message.message_id,
                "a2a_role": message.role,
                "a2a": envelope,
                "a2a_requested_extensions": requested_extensions,
            }
        )
        if request.configuration and request.configuration.accepted_output_modes:
            flow_message.meta["a2a_accepted_output_modes"] = list(request.configuration.accepted_output_modes)
        return flow_message

    def _maybe_unwrap_payload(self, message: Message, envelope: Any) -> Any:
        if len(message.parts) != 1:
            return envelope
        part = message.parts[0]
        if part.text is not None:
            return part.text
        if part.data is not None:
            return part.data.data
        return envelope

    async def _start_task_run(
        self,
        task_id: str,
        context_id: str,
        message: FlowMessage,
        *,
        blocking: bool,
    ) -> None:
        async with self._lock:
            existing_task = self._running_tasks.get(task_id)
            if existing_task is not None and not existing_task.done():
                raise UnsupportedOperationError("Task is already running.")
            task = asyncio.create_task(
                self._run_task(task_id, context_id, message),
                name=f"a2a-task-{task_id}",
            )
            self._running_tasks[task_id] = task
            task.add_done_callback(lambda done: self._running_tasks.pop(task_id, None))
        if blocking:
            await task

    async def _run_task(
        self,
        task_id: str,
        context_id: str,
        message: FlowMessage,
    ) -> None:
        queue = await self._dispatcher.acquire_queue(task_id)
        try:
            await self._persist_binding(task_id, context_id)
            await self._update_status(
                task_id,
                context_id,
                TaskStatus(state=TaskState.WORKING, timestamp=_utc_iso()),
                final=False,
            )
            try:
                await self._flow.emit(message, to=self._target, trace_id=task_id)
            except Exception as exc:  # pragma: no cover - defensive
                await self._mark_failed(task_id, context_id, str(exc))
                return
            artifact_id = "output-0"
            while True:
                item = await queue.get()
                if item is _QUEUE_SHUTDOWN:
                    await self._mark_failed(task_id, context_id, "Flow shutting down")
                    break
                if isinstance(item, TraceCancelled):
                    await self._update_status(
                        task_id,
                        context_id,
                        TaskStatus(state=TaskState.CANCELLED, timestamp=_utc_iso()),
                        final=True,
                    )
                    break
                if isinstance(item, FlowError):
                    await self._mark_failed(task_id, context_id, item.message)
                    break
                if isinstance(item, Exception):  # pragma: no cover - defensive
                    await self._mark_failed(task_id, context_id, str(item))
                    break
                payload = self._unwrap_flow_item(item)
                if isinstance(payload, StreamChunk):
                    await self._handle_stream_chunk(
                        task_id,
                        context_id,
                        artifact_id,
                        payload,
                    )
                    if payload.done:
                        await self._update_status(
                            task_id,
                            context_id,
                            TaskStatus(
                                state=TaskState.COMPLETED,
                                timestamp=_utc_iso(),
                            ),
                            final=True,
                        )
                        break
                    continue
                await self._handle_final_payload(task_id, context_id, artifact_id, payload)
                await self._update_status(
                    task_id,
                    context_id,
                    TaskStatus(state=TaskState.COMPLETED, timestamp=_utc_iso()),
                    final=True,
                )
                break
        finally:
            await self._dispatcher.release_queue(task_id)

    async def _persist_binding(self, task_id: str, context_id: str) -> None:
        if not self._config.agent_url:
            return
        binding = RemoteBinding(
            trace_id=task_id,
            context_id=context_id,
            task_id=task_id,
            agent_url=self._config.agent_url,
        )
        await self._flow.save_remote_binding(binding)

    async def _handle_stream_chunk(
        self,
        task_id: str,
        context_id: str,
        artifact_id: str,
        chunk: StreamChunk,
    ) -> None:
        part = Part(text=chunk.text)
        artifact = Artifact(artifact_id=artifact_id, parts=[part])
        await self._store_artifact(
            task_id,
            context_id,
            artifact,
            append=chunk.seq > 0,
            last_chunk=chunk.done,
        )

    async def _handle_final_payload(
        self,
        task_id: str,
        context_id: str,
        artifact_id: str,
        payload: Any,
    ) -> None:
        artifact = self._payload_to_artifact(artifact_id, payload)
        await self._store_artifact(
            task_id,
            context_id,
            artifact,
            append=False,
            last_chunk=True,
        )

    async def _mark_failed(self, task_id: str, context_id: str, detail: str) -> None:
        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.AGENT,
            parts=[Part(text=detail)],
            context_id=context_id,
            task_id=task_id,
        )
        status = TaskStatus(
            state=TaskState.FAILED,
            message=message,
            timestamp=_utc_iso(),
        )
        await self._update_status(task_id, context_id, status, final=True)

    async def _update_status(
        self,
        task_id: str,
        context_id: str,
        status: TaskStatus,
        *,
        final: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._store.update_status(task_id, status, final=final, metadata=metadata)
        event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=status,
            final=final,
            metadata=dict(metadata or {}),
        )
        await self._notify_push_configs(task_id, StreamResponse(status_update=event))

    async def _store_artifact(
        self,
        task_id: str,
        context_id: str,
        artifact: Artifact,
        *,
        append: bool,
        last_chunk: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._store.add_artifact(
            task_id,
            artifact,
            append=append,
            last_chunk=last_chunk,
            metadata=metadata,
        )
        event = TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=append,
            last_chunk=last_chunk,
            metadata=dict(metadata or {}),
        )
        await self._notify_push_configs(task_id, StreamResponse(artifact_update=event))

    async def _notify_push_configs(self, task_id: str, event: StreamResponse) -> None:
        if not self._agent_card.capabilities.push_notifications:
            return
        configs: list[TaskPushNotificationConfig] = []
        page_token: str | None = None
        try:
            while True:
                response = await self._store.list_push_configs(
                    task_id,
                    page_size=100,
                    page_token=page_token,
                )
                configs.extend(response.configs)
                if not response.next_page_token:
                    break
                page_token = response.next_page_token
        except KeyError:
            return
        if not configs:
            return
        await asyncio.gather(
            *(self._push_sender.send(config.push_notification_config, event) for config in configs),
            return_exceptions=True,
        )

    def _payload_to_artifact(self, artifact_id: str, payload: Any) -> Artifact:
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        if isinstance(payload, FlowMessage):
            payload = payload.payload
        if isinstance(payload, bytes):
            payload = {"bytes": base64.b64encode(payload).decode("utf-8")}
        if payload is None:
            payload = {}
        if isinstance(payload, str):
            part = Part(text=payload)
        else:
            part = Part(data=DataPart(data=self._to_jsonable(payload)))
        return Artifact(artifact_id=artifact_id, parts=[part])

    def _unwrap_flow_item(self, item: Any) -> Any:
        if isinstance(item, RookeryResult):
            return item.value
        if isinstance(item, FlowRookeryResult):
            return item.value
        return getattr(item, "payload", item)

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump()
        if isinstance(value, FlowMessage):
            return {
                "payload": self._to_jsonable(value.payload),
                "headers": value.headers.model_dump(),
                "trace_id": value.trace_id,
                "meta": dict(value.meta),
            }
        if isinstance(value, RookeryResult):
            return self._to_jsonable(value.value)
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("utf-8")
        if isinstance(value, Mapping):
            return {key: self._to_jsonable(val) for key, val in value.items()}
        if isinstance(value, list | tuple | set):
            return [self._to_jsonable(item) for item in value]
        return value


__all__ = ["A2AService", "FlowDispatcher"]
