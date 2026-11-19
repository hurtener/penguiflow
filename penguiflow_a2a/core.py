"""A2A service that serves a ReactPlanner agent."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import logging
import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause
from penguiflow.state import RemoteBinding
from penguiflow.steering import (
    SteeringCancelled,
    SteeringEvent,
    SteeringEventType,
    SteeringInbox,
)

from .config import A2AConfig
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
    ListTasksResponse,
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

if TYPE_CHECKING:
    from penguiflow.planner import PlannerEventCallback, ToolVisibilityPolicy

logger = logging.getLogger("penguiflow_a2a")

_OUTPUT_ARTIFACT_ID = "output-0"
_ANSWER_KEYS = ("raw_answer", "answer", "text", "content", "message")
_PAUSE_TEXT_KEYS = ("prompt", "message", "question", "detail")

TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.REJECTED,
}


def _utc_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _normalize_version(value: str) -> str:
    trimmed = value.strip()
    parts = trimmed.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return trimmed


def _answer_text(payload: object) -> str | None:
    """Return the human-readable answer of a planner payload, if any."""
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        for key in _ANSWER_KEYS:
            value = payload.get(key)
            if value is not None:
                return str(value)
        return None
    for attr in _ANSWER_KEYS:
        value = getattr(payload, attr, None)
        if value is not None:
            return str(value)
    return None


def _pause_text(pause: PlannerPause) -> str:
    for key in _PAUSE_TEXT_KEYS:
        value = pause.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return f"Input required ({pause.reason})."


class A2APlanner(Protocol):
    """The planner surface :class:`A2AService` drives.

    :class:`~penguiflow.planner.ReactPlanner` provides it. The service runs
    or resumes the planner per task and temporarily wraps
    ``event_callback`` to stream answer chunks.
    """

    event_callback: PlannerEventCallback | None

    async def run(
        self,
        query: str,
        *,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Run the planner on ``query`` until it finishes or pauses."""
        ...

    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Resume the paused run identified by ``token``."""
        ...


@dataclass(slots=True)
class _PlannerInput:
    query: str
    tool_context: dict[str, Any]


@dataclass(slots=True)
class _TaskRun:
    """Per-task state for one planner run (or resume)."""

    task_id: str
    context_id: str
    stream: bool
    inbox: SteeringInbox = field(default_factory=SteeringInbox)
    chunks: asyncio.Queue[tuple[str, Any] | None] | None = None
    streamed: bool = False
    action_seq: Any = None


# The planner invokes its event callback synchronously from inside the task
# that runs ``planner.run``; this context variable routes each event back to
# the A2A task it belongs to.
_TASK_RUN: contextvars.ContextVar[_TaskRun | None] = contextvars.ContextVar(
    "penguiflow_a2a_task_run", default=None
)


class A2AService:
    """Expose a :class:`~penguiflow.planner.ReactPlanner` as an A2A agent.

    Each A2A task drives one planner run:

    * ``PlannerFinish(reason="answer_complete")`` completes the task with the
      answer as artifact ``output-0``; other finish reasons fail it.
    * ``PlannerPause`` moves the task to ``input-required``; the next message
      sent to that task resumes the planner with the message text as
      ``user_input``.
    * ``tasks/cancel`` cancels the run through a per-task
      :class:`SteeringInbox`.
    * With ``stream_final_response=True`` on the planner, ``message/stream``
      streams the final answer as appended ``output-0`` chunks before the final
      artifact replaces them.

    The A2A ``contextId`` is passed as ``tool_context["session_id"]``, so the
    planner serializes runs within one conversation and runs different
    conversations in parallel.
    """

    def __init__(
        self,
        planner: A2APlanner,
        *,
        agent_card: AgentCard,
        config: A2AConfig | None = None,
        store: TaskStore | None = None,
        state_store: object | None = None,
        tool_context: Mapping[str, Any] | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
        push_sender: PushNotificationSender | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_agent_card_auth: Callable[[Mapping[str, str]], bool]
        | None = None,
    ) -> None:
        """Serve ``planner`` as the A2A agent described by ``agent_card``."""
        self._planner = planner
        self._agent_card = agent_card
        self._config = config or A2AConfig()
        self._store = store or InMemoryTaskStore()
        self._state_store = (
            state_store
            if state_store is not None
            else getattr(planner, "state_store", None)
        )
        self._tool_context = dict(tool_context or {})
        self._tool_visibility = tool_visibility
        self._started = False
        self._stopping = False
        self._start_lock = asyncio.Lock()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._runs: dict[str, _TaskRun] = {}
        self._paused: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._push_sender = push_sender or HttpPushNotificationSender()
        self._extended_agent_card = extended_agent_card
        self._extended_agent_card_auth = extended_agent_card_auth
        self._previous_event_callback: (
            Callable[[PlannerEvent], None] | None
        ) = None
        self._event_bridge: Callable[[PlannerEvent], None] | None = None
        self._ensure_protocol_versions()

    @property
    def started(self) -> bool:
        """Whether the service has started and not yet stopped."""
        return self._started

    @property
    def agent_card(self) -> AgentCard:
        """The public agent card."""
        return self._agent_card

    @property
    def config(self) -> A2AConfig:
        """The service configuration."""
        return self._config

    @property
    def planner(self) -> A2APlanner:
        """The planner serving the requests."""
        return self._planner

    async def start(self) -> None:
        """Start the service (idempotent)."""
        if self._started:
            return
        self._install_event_bridge()
        self._started = True

    async def stop(self) -> None:
        """Stop the service, cancelling the running tasks."""
        if not self._started:
            return
        self._stopping = True
        try:
            tasks = [
                task
                for task in self._running_tasks.values()
                if not task.done()
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks)
        finally:
            self._uninstall_event_bridge()
            self._stopping = False
            self._started = False

    def validate_version(self, requested_version: str | None) -> str:
        """Return the protocol version to use for a request."""
        if requested_version is None:
            return (
                self._config.default_version
                or self._config.supported_versions[0]
            )
        normalized = _normalize_version(requested_version)
        if not self._config.is_version_supported(normalized):
            raise VersionNotSupportedError(
                normalized, self._config.supported_versions
            )
        return normalized

    def validate_extensions(self, requested_extensions: list[str]) -> None:
        """Reject requests for extensions the agent does not support."""
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
        """Handle a message, blocking until its task finishes if asked to."""
        await self._ensure_started()
        self._validate_push_config(request)
        task_id, context_id, planner_input = await self._accept_message(
            request,
            tenant=tenant,
            requested_extensions=requested_extensions or [],
        )
        blocking = bool(
            request.configuration and request.configuration.blocking
        )
        await self._start_task_run(
            task_id, context_id, planner_input, stream=False, blocking=blocking
        )
        task = await self._store.get_task(
            task_id,
            history_length=(
                request.configuration.history_length
                if request.configuration
                else None
            ),
        )
        return SendMessageResponse(task=task)

    async def stream_message(
        self,
        request: SendMessageRequest,
        *,
        tenant: str | None,
        requested_extensions: list[str] | None = None,
    ) -> tuple[
        Task,
        asyncio.Queue[StreamResponse],
        Callable[[], Coroutine[Any, Any, None]],
    ]:
        """Handle a message; return its task and a queue of its events."""
        await self._ensure_started()
        if not self._agent_card.capabilities.streaming:
            msg = "Streaming is not supported by this agent."
            raise UnsupportedOperationError(msg)
        self._validate_push_config(request)
        task_id, context_id, planner_input = await self._accept_message(
            request,
            tenant=tenant,
            requested_extensions=requested_extensions or [],
        )
        queue, unsubscribe = await self._store.subscribe(task_id)
        await self._start_task_run(
            task_id, context_id, planner_input, stream=True, blocking=False
        )
        task = await self._store.get_task(task_id, history_length=None)
        return task, queue, unsubscribe

    async def get_task(
        self, task_id: str, *, history_length: int | None
    ) -> Task:
        """Return a task, with at most ``history_length`` messages."""
        await self._ensure_started()
        try:
            return await self._store.get_task(
                task_id, history_length=history_length
            )
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
    ) -> ListTasksResponse:
        """Return a page of tasks, optionally filtered."""
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
        """Cancel a running task."""
        await self._ensure_started()
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            raise TaskNotCancelableError(task_id)
        run_task = self._running_tasks.get(task_id)
        run = self._runs.get(task_id)
        if run_task is not None and not run_task.done() and run is not None:
            await run.inbox.push(
                SteeringEvent(
                    session_id=run.context_id,
                    task_id=task_id,
                    trace_id=task_id,
                    event_type=SteeringEventType.CANCEL,
                    payload={"reason": "a2a_cancel"},
                    source="a2a",
                )
            )
            run_task.cancel()
            await asyncio.wait([run_task])
            return await self._store.get_task(task_id)
        if self._paused.pop(task_id, None) is not None:
            status = TaskStatus(
                state=TaskState.CANCELLED, timestamp=_utc_iso()
            )
            await self._update_status(
                task_id, task.context_id, status, final=True
            )
            return await self._store.get_task(task_id)
        raise TaskNotCancelableError(task_id)

    async def subscribe_task(
        self, task_id: str
    ) -> tuple[
        Task,
        asyncio.Queue[StreamResponse],
        Callable[[], Coroutine[Any, Any, None]],
    ]:
        """Subscribe to the events of a running task."""
        await self._ensure_started()
        if not self._agent_card.capabilities.streaming:
            msg = "Streaming is not supported by this agent."
            raise UnsupportedOperationError(msg)
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            msg = "Cannot subscribe to a terminal task."
            raise UnsupportedOperationError(msg)
        queue, unsubscribe = await self._store.subscribe(task_id)
        return task, queue, unsubscribe

    async def get_extended_agent_card(self) -> AgentCard:
        """Return the extended agent card."""
        await self._ensure_started()
        if not self._agent_card.capabilities.extended_agent_card:
            msg = "Extended agent card is not supported by this agent."
            raise UnsupportedOperationError(msg)
        if self._extended_agent_card is None:
            raise ExtendedAgentCardNotConfiguredError()
        return self._extended_agent_card.model_copy(deep=True)

    def is_extended_agent_card_authorized(
        self, headers: Mapping[str, str]
    ) -> bool:
        """Whether the request headers authorize the extended agent card."""
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
        """Store a push notification config of a task."""
        await self._ensure_started()
        self._ensure_push_supported()
        try:
            task = await self._store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        if task.status.state in TERMINAL_STATES:
            msg = "Cannot add push config to a terminal task."
            raise UnsupportedOperationError(msg)
        canonical_name = self._push_config_name(task_id, config_id)
        if config.name and config.name != canonical_name:
            msg = "pushNotificationConfig.name does not match task/config id"
            raise A2ARequestValidationError(msg)
        push_config = config.push_notification_config
        if push_config.id and push_config.id != config_id:
            msg = "pushNotificationConfig.id does not match configId"
            raise A2ARequestValidationError(msg)
        push_config = push_config.model_copy(update={"id": config_id})
        normalized = TaskPushNotificationConfig(
            name=canonical_name, push_notification_config=push_config
        )
        await self._store.set_push_config(task_id, config_id, normalized)
        return normalized

    async def get_task_push_notification_config(
        self, task_id: str, *, config_id: str
    ) -> TaskPushNotificationConfig:
        """Return a push notification config of a task."""
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
        """Return a page of the push notification configs of a task."""
        await self._ensure_started()
        self._ensure_push_supported()
        if page_size < 1:
            msg = "pageSize must be >= 1"
            raise A2ARequestValidationError(msg)
        try:
            return await self._store.list_push_configs(
                task_id,
                page_size=page_size,
                page_token=page_token,
            )
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        except ValueError as exc:
            msg = "pageToken is invalid"
            raise A2ARequestValidationError(msg) from exc

    async def delete_task_push_notification_config(
        self, task_id: str, *, config_id: str
    ) -> None:
        """Delete a push notification config of a task."""
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
            msg = (
                "AgentCard.protocol_versions must include all supported "
                "versions"
            )
            raise ValueError(msg)

    async def _ensure_started(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if not self._started:
                await self.start()

    def _validate_push_config(self, request: SendMessageRequest) -> None:
        if (
            not request.configuration
            or not request.configuration.push_notification_config
        ):
            return
        if not self._agent_card.capabilities.push_notifications:
            raise PushNotificationNotSupportedError()

    def _ensure_push_supported(self) -> None:
        if not self._agent_card.capabilities.push_notifications:
            raise PushNotificationNotSupportedError()

    async def _accept_message(
        self,
        request: SendMessageRequest,
        *,
        tenant: str | None,
        requested_extensions: list[str],
    ) -> tuple[str, str, _PlannerInput]:
        """Validate an incoming message and record it on its task.

        Returns:
            The task id, the context id and the planner input.
        """
        task_id, context_id, existing_task = await self._resolve_task(request)
        message = self._enrich_message(request.message, task_id, context_id)
        planner_input = self._build_planner_input(
            request,
            message=message,
            task_id=task_id,
            context_id=context_id,
            tenant=tenant,
            requested_extensions=requested_extensions,
        )
        if existing_task is None:
            await self._create_task_record(task_id, context_id, message)
        else:
            running = self._running_tasks.get(task_id)
            if running is not None and not running.done():
                msg = "Task is already running."
                raise UnsupportedOperationError(msg)
            await self._store.add_history(task_id, message)
        if (
            request.configuration
            and request.configuration.push_notification_config
        ):
            await self._register_push_config(
                task_id, request.configuration.push_notification_config
            )
        return task_id, context_id, planner_input

    async def _create_task_record(
        self, task_id: str, context_id: str, message: Message
    ) -> Task:
        status = TaskStatus(state=TaskState.SUBMITTED, timestamp=_utc_iso())
        task = Task(
            id=task_id,
            context_id=context_id,
            status=status,
            history=[message],
        )
        return await self._store.create_task(task)

    async def _resolve_task(
        self, request: SendMessageRequest
    ) -> tuple[str, str, Task | None]:
        message = request.message
        if message.role != Role.USER:
            msg = "message.role must be 'user'"
            raise A2ARequestValidationError(msg)
        if message.task_id:
            try:
                existing = await self._store.get_task(message.task_id)
            except KeyError as exc:
                raise TaskNotFoundError(message.task_id) from exc
            if existing.status.state in TERMINAL_STATES:
                msg = "Cannot send a message to a terminal task."
                raise UnsupportedOperationError(msg)
            if (
                message.context_id
                and message.context_id != existing.context_id
            ):
                msg = "message.contextId does not match task context"
                raise A2ARequestValidationError(msg)
            return existing.id, existing.context_id, existing
        task_id = uuid.uuid4().hex
        context_id = message.context_id or uuid.uuid4().hex
        return task_id, context_id, None

    def _enrich_message(
        self, message: Message, task_id: str, context_id: str
    ) -> Message:
        updates: dict[str, str] = {}
        if message.task_id is None:
            updates["task_id"] = task_id
        if message.context_id is None:
            updates["context_id"] = context_id
        if not updates:
            return message
        return message.model_copy(update=updates)

    async def _register_push_config(
        self, task_id: str, config: PushNotificationConfig
    ) -> None:
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

    def _build_planner_input(
        self,
        request: SendMessageRequest,
        *,
        message: Message,
        task_id: str,
        context_id: str,
        tenant: str | None,
        requested_extensions: list[str],
    ) -> _PlannerInput:
        query = self._message_to_query(message)
        envelope = {
            "message": message.model_dump(by_alias=True, exclude_none=True),
            "metadata": request.metadata,
            "configuration": request.configuration.model_dump(
                by_alias=True, exclude_none=True
            )
            if request.configuration
            else None,
        }
        tool_context: dict[str, Any] = {
            "delivery_channel": "a2a",
            **self._tool_context,
        }
        tool_context.update(
            {
                "tenant": tenant or self._config.default_tenant,
                "session_id": context_id,
                "a2a_task_id": task_id,
                "a2a_context_id": context_id,
                "a2a_message_id": message.message_id,
                "a2a_role": str(getattr(message.role, "value", message.role)),
                "a2a": envelope,
                "a2a_requested_extensions": list(requested_extensions),
            }
        )
        if (
            request.configuration
            and request.configuration.accepted_output_modes
        ):
            tool_context["a2a_accepted_output_modes"] = list(
                request.configuration.accepted_output_modes
            )
        return _PlannerInput(query=query, tool_context=tool_context)

    def _message_to_query(self, message: Message) -> str:
        texts = [part.text for part in message.parts if part.text]
        if texts:
            return "\n".join(texts)
        data = [
            self._to_jsonable(part.data.data)
            for part in message.parts
            if part.data is not None
        ]
        if data:
            return json.dumps(
                data[0] if len(data) == 1 else data,
                ensure_ascii=False,
                default=str,
            )
        msg = "message must include at least one text or data part"
        raise A2ARequestValidationError(msg)

    async def _start_task_run(
        self,
        task_id: str,
        context_id: str,
        planner_input: _PlannerInput,
        *,
        stream: bool,
        blocking: bool,
    ) -> None:
        async with self._lock:
            existing_task = self._running_tasks.get(task_id)
            if existing_task is not None and not existing_task.done():
                msg = "Task is already running."
                raise UnsupportedOperationError(msg)
            resume_token = self._paused.pop(task_id, None)
            run = _TaskRun(
                task_id=task_id, context_id=context_id, stream=stream
            )
            task = asyncio.create_task(
                self._run_task(run, planner_input, resume_token=resume_token),
                name=f"a2a-task-{task_id}",
            )
            self._runs[task_id] = run
            self._running_tasks[task_id] = task

            def _forget(done: asyncio.Task[None]) -> None:
                if self._running_tasks.get(task_id) is done:
                    self._running_tasks.pop(task_id, None)
                    self._runs.pop(task_id, None)

            task.add_done_callback(_forget)
        if blocking:
            # Shield the run so a dropped client connection does not cancel the
            # agent.
            await asyncio.shield(task)

    async def _run_task(
        self,
        run: _TaskRun,
        planner_input: _PlannerInput,
        *,
        resume_token: str | None,
    ) -> None:
        token = _TASK_RUN.set(run)
        pump: asyncio.Task[None] | None = None
        if run.stream:
            run.chunks = asyncio.Queue()
            pump = asyncio.create_task(
                self._pump_answer_chunks(run), name=f"a2a-stream-{run.task_id}"
            )
        try:
            await self._persist_binding(run.task_id, run.context_id)
            await self._update_status(
                run.task_id,
                run.context_id,
                TaskStatus(state=TaskState.WORKING, timestamp=_utc_iso()),
                final=False,
            )
            try:
                result = await self._invoke_planner(
                    run, planner_input, resume_token=resume_token
                )
            except (SteeringCancelled, asyncio.CancelledError):
                await self._drain_answer_chunks(run, pump)
                if self._stopping:
                    await self._mark_failed(
                        run.task_id, run.context_id, "Agent shutting down"
                    )
                else:
                    await self._update_status(
                        run.task_id,
                        run.context_id,
                        TaskStatus(
                            state=TaskState.CANCELLED, timestamp=_utc_iso()
                        ),
                        final=True,
                    )
                return
            except Exception as exc:
                logger.exception(
                    "a2a_planner_run_failed", extra={"task_id": run.task_id}
                )
                await self._drain_answer_chunks(run, pump)
                await self._mark_failed(
                    run.task_id, run.context_id, str(exc) or type(exc).__name__
                )
                return
            await self._drain_answer_chunks(run, pump)
            await self._handle_planner_result(run, result)
        finally:
            _TASK_RUN.reset(token)
            if pump is not None and not pump.done():
                pump.cancel()

    async def _invoke_planner(
        self,
        run: _TaskRun,
        planner_input: _PlannerInput,
        *,
        resume_token: str | None,
    ) -> PlannerFinish | PlannerPause:
        if resume_token is not None:
            return await self._planner.resume(
                resume_token,
                user_input=planner_input.query,
                tool_context=planner_input.tool_context,
                steering=run.inbox,
                tool_visibility=self._tool_visibility,
            )
        return await self._planner.run(
            planner_input.query,
            tool_context=planner_input.tool_context,
            steering=run.inbox,
            tool_visibility=self._tool_visibility,
        )

    async def _handle_planner_result(
        self, run: _TaskRun, result: PlannerFinish | PlannerPause
    ) -> None:
        if isinstance(result, PlannerPause):
            self._paused[run.task_id] = result.resume_token
            message = Message(
                message_id=uuid.uuid4().hex,
                role=Role.AGENT,
                parts=[Part(text=_pause_text(result))],
                context_id=run.context_id,
                task_id=run.task_id,
            )
            await self._update_status(
                run.task_id,
                run.context_id,
                TaskStatus(
                    state=TaskState.INPUT_REQUIRED,
                    message=message,
                    timestamp=_utc_iso(),
                ),
                final=True,
                metadata={
                    "pause_reason": result.reason,
                    "pause_payload": self._to_jsonable(result.payload),
                },
            )
            return
        if result.payload is not None:
            await self._store_artifact(
                run.task_id,
                run.context_id,
                self._result_to_artifact(result.payload),
                append=False,
                last_chunk=True,
            )
        if result.reason == "answer_complete":
            await self._update_status(
                run.task_id,
                run.context_id,
                TaskStatus(state=TaskState.COMPLETED, timestamp=_utc_iso()),
                final=True,
            )
            return
        error = (
            result.metadata.get("error")
            if isinstance(result.metadata, Mapping)
            else None
        )
        detail = (
            str(error)
            if error
            else f"Planner finished without an answer ({result.reason})."
        )
        await self._mark_failed(
            run.task_id,
            run.context_id,
            detail,
            metadata={"finish_reason": result.reason},
        )

    # -- answer streaming -----------------------------------------------------

    def _install_event_bridge(self) -> None:
        previous = getattr(self._planner, "event_callback", None)
        if previous is not None and previous is self._event_bridge:
            return
        self._previous_event_callback = previous

        def _bridge(event: PlannerEvent) -> None:
            if previous is not None:
                try:
                    previous(event)
                except Exception:
                    logger.exception(
                        "a2a_event_callback_error",
                        extra={"event_type": event.event_type},
                    )
            self._on_planner_event(event)

        self._event_bridge = _bridge
        self._planner.event_callback = _bridge

    def _uninstall_event_bridge(self) -> None:
        current = getattr(self._planner, "event_callback", None)
        if current is not None and current is self._event_bridge:
            self._planner.event_callback = self._previous_event_callback
        self._previous_event_callback = None
        self._event_bridge = None

    def _on_planner_event(self, event: PlannerEvent) -> None:
        if event.event_type != "llm_stream_chunk":
            return
        run = _TASK_RUN.get()
        if run is None or run.chunks is None:
            return
        extra = event.extra or {}
        if extra.get("channel") != "answer":
            return
        text = extra.get("text")
        if not isinstance(text, str) or not text:
            return
        run.chunks.put_nowait((text, extra.get("action_seq")))

    async def _pump_answer_chunks(self, run: _TaskRun) -> None:
        assert run.chunks is not None
        while True:
            item = await run.chunks.get()
            if item is None:
                return
            text, action_seq = item
            # A new action_seq means the planner restarted its answer (e.g.
            # after a repair), so the streamed artifact is replaced rather than
            # appended to.
            restart = not run.streamed or action_seq != run.action_seq
            run.streamed = True
            run.action_seq = action_seq
            metadata: dict[str, Any] = {"streaming": True}
            if action_seq is not None:
                metadata["action_seq"] = action_seq
            await self._store_artifact(
                run.task_id,
                run.context_id,
                Artifact(
                    artifact_id=_OUTPUT_ARTIFACT_ID, parts=[Part(text=text)]
                ),
                append=not restart,
                last_chunk=False,
                metadata=metadata,
            )

    async def _drain_answer_chunks(
        self, run: _TaskRun, pump: asyncio.Task[None] | None
    ) -> None:
        if pump is None or run.chunks is None or pump.done():
            return
        run.chunks.put_nowait(None)
        await asyncio.wait([pump])

    # -- persistence and events -----------------------------------------------

    async def _persist_binding(self, task_id: str, context_id: str) -> None:
        if not self._config.agent_url or self._state_store is None:
            return
        save = getattr(self._state_store, "save_remote_binding", None)
        if save is None:
            return
        binding = RemoteBinding(
            trace_id=task_id,
            context_id=context_id,
            task_id=task_id,
            agent_url=self._config.agent_url,
        )
        try:
            await save(binding)
        except Exception:
            # Binding persistence is best-effort: a failing store must not
            # strand the task.
            logger.exception(
                "a2a_remote_binding_save_failed", extra={"task_id": task_id}
            )

    async def _mark_failed(
        self,
        task_id: str,
        context_id: str,
        detail: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
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
        await self._update_status(
            task_id, context_id, status, final=True, metadata=metadata
        )

    async def _update_status(
        self,
        task_id: str,
        context_id: str,
        status: TaskStatus,
        *,
        final: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._store.update_status(
            task_id, status, final=final, metadata=metadata
        )
        event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=status,
            final=final,
            metadata=dict(metadata or {}),
        )
        await self._notify_push_configs(
            task_id, StreamResponse(status_update=event)
        )

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
        await self._notify_push_configs(
            task_id, StreamResponse(artifact_update=event)
        )

    async def _notify_push_configs(
        self, task_id: str, event: StreamResponse
    ) -> None:
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
            *(
                self._push_sender.send(config.push_notification_config, event)
                for config in configs
            ),
            return_exceptions=True,
        )

    # -- payload conversion ---------------------------------------------------

    def _result_to_artifact(self, payload: object) -> Artifact:
        """Turn a planner payload into the task's output artifact.

        The answer text becomes a text part; for structured payloads the full
        payload is kept in the artifact metadata under ``planner_payload``.
        Payloads without an answer text become a data part.
        """
        text = _answer_text(payload)
        if text is None:
            return self._payload_to_artifact(_OUTPUT_ARTIFACT_ID, payload)
        metadata = (
            None
            if isinstance(payload, str)
            else {"planner_payload": self._to_jsonable(payload)}
        )
        return Artifact(
            artifact_id=_OUTPUT_ARTIFACT_ID,
            parts=[Part(text=text)],
            metadata=metadata,
        )

    def _payload_to_artifact(
        self, artifact_id: str, payload: object
    ) -> Artifact:
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        if isinstance(payload, bytes):
            payload = {"bytes": base64.b64encode(payload).decode("utf-8")}
        if payload is None:
            payload = {}
        if isinstance(payload, str):
            part = Part(text=payload)
        else:
            part = Part(data=DataPart(data=self._to_jsonable(payload)))
        return Artifact(artifact_id=artifact_id, parts=[part])

    def _to_jsonable(self, value: object) -> object:
        if isinstance(value, BaseModel):
            return self._to_jsonable(value.model_dump(mode="json"))
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("utf-8")
        if isinstance(value, Mapping):
            return {
                str(key): self._to_jsonable(val) for key, val in value.items()
            }
        if isinstance(value, list | tuple | set):
            return [self._to_jsonable(item) for item in value]
        return value


__all__ = ["A2APlanner", "A2AService"]
