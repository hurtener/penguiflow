"""Remote transport protocol and helper node for PenguiFlow."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from .core import TraceCancelled
from .node import Node, NodePolicy
from .state import RemoteBinding
from .types import Message

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from .core import Context, PenguiFlow


@dataclass(slots=True)
class RemoteCallRequest:
    """Input to :class:`RemoteTransport` implementations.

    Attributes:
        message: The PenguiFlow :class:`~penguiflow.types.Message` being forwarded to the remote agent.
        skill: Name of the remote skill/capability to invoke.
        agent_url: Base URL of the remote agent to call.
        agent_card: Optional agent card metadata (e.g. capabilities descriptor) for the target agent.
        metadata: Optional request metadata forwarded alongside the message (typically ``message.meta``).
        timeout_s: Optional per-call timeout in seconds; ``None`` means no explicit timeout override.
        context_id: Optional remote conversation/context identifier to continue an existing context.
        task_id: Optional remote task identifier to continue or resume an existing task.
    """

    message: Message
    skill: str
    agent_url: str
    agent_card: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    timeout_s: float | None = None
    context_id: str | None = None
    task_id: str | None = None


@dataclass(slots=True)
class RemoteCallResult:
    """Return value for :meth:`RemoteTransport.send`.

    Attributes:
        result: The unary result payload returned by the remote agent.
        context_id: Remote conversation/context identifier assigned or reused by the remote agent, if any.
        task_id: Remote task identifier assigned by the remote agent, if any.
        agent_url: Agent URL that actually served the request (may differ from the requested ``agent_url``).
        meta: Optional additional metadata returned by the transport.
    """

    result: Any
    context_id: str | None = None
    task_id: str | None = None
    agent_url: str | None = None
    meta: Mapping[str, Any] | None = None


@dataclass(slots=True)
class RemoteStreamEvent:
    """Streaming event yielded by :meth:`RemoteTransport.stream`.

    Attributes:
        text: Incremental text chunk emitted by the remote agent, if any.
        done: Whether this event marks the end of the stream.
        meta: Optional metadata associated with this chunk (e.g. token usage, reasoning tags).
        context_id: Remote conversation/context identifier associated with this event, if known.
        task_id: Remote task identifier associated with this event, if known.
        agent_url: Agent URL that produced this event, if known (may change across retries/redirects).
        result: Optional final result payload attached to a terminal event.
    """

    text: str | None = None
    done: bool = False
    meta: Mapping[str, Any] | None = None
    context_id: str | None = None
    task_id: str | None = None
    agent_url: str | None = None
    result: Any | None = None


class RemoteTaskState(str, Enum):
    """Task lifecycle states exposed by task-oriented remote transports.

    Attributes:
        UNSPECIFIED: State was not reported by the remote transport.
        SUBMITTED: Task was accepted by the remote agent but has not started running yet.
        WORKING: Task is actively being processed by the remote agent.
        COMPLETED: Task finished successfully (terminal state).
        FAILED: Task finished with an error (terminal state).
        CANCELLED: Task was cancelled before completion (terminal state).
        INPUT_REQUIRED: Task is paused pending additional user input.
        AUTH_REQUIRED: Task is paused pending authorization/credentials.
        REJECTED: Task was rejected by the remote agent (terminal state).
    """

    UNSPECIFIED = "unspecified"
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INPUT_REQUIRED = "input-required"
    AUTH_REQUIRED = "auth-required"
    REJECTED = "rejected"


REMOTE_TERMINAL_TASK_STATES = frozenset(
    {
        RemoteTaskState.COMPLETED,
        RemoteTaskState.FAILED,
        RemoteTaskState.CANCELLED,
        RemoteTaskState.REJECTED,
    }
)


@dataclass(slots=True)
class RemoteTaskStatus:
    """Normalized status for a remote task.

    Attributes:
        state: Current lifecycle state of the remote task.
        message: Optional human-readable status message from the remote agent.
        timestamp: Optional ISO-8601 timestamp of when this status was reported.
        raw: Optional raw, transport-specific status payload for debugging.
    """

    state: RemoteTaskState
    message: str | None = None
    timestamp: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(slots=True)
class RemoteTaskSnapshot:
    """Normalized task representation returned by task-oriented remote transports.

    Attributes:
        task_id: Remote task identifier.
        context_id: Remote conversation/context identifier the task belongs to.
        status: Current normalized status of the task.
        result: Optional result payload once the task has produced output.
        artifacts: Optional list of artifacts produced by the task.
        history: Optional list of historical events/messages for the task.
        agent_url: Optional agent URL that owns this task.
        meta: Optional additional metadata about the task.
    """

    task_id: str
    context_id: str
    status: RemoteTaskStatus
    result: Any | None = None
    artifacts: list[Any] | None = None
    history: list[Any] | None = None
    agent_url: str | None = None
    meta: Mapping[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        """Return whether ``status.state`` is one of the terminal task states."""
        return self.status.state in REMOTE_TERMINAL_TASK_STATES


@dataclass(slots=True)
class RemoteTaskEvent:
    """Normalized event emitted by task subscriptions.

    Attributes:
        kind: Discriminator describing the kind of event (transport-specific string).
        task: Optional full task snapshot attached to this event.
        status: Optional status update attached to this event.
        text: Optional incremental text chunk attached to this event.
        result: Optional result payload attached to this event.
        artifact: Optional artifact attached to this event.
        done: Whether this event marks the end of the subscription stream.
        context_id: Remote conversation/context identifier associated with this event, if known.
        task_id: Remote task identifier associated with this event, if known.
        agent_url: Agent URL that produced this event, if known.
        meta: Optional additional metadata for this event.
        raw: Optional raw, transport-specific event payload for debugging.
    """

    kind: str
    task: RemoteTaskSnapshot | None = None
    status: RemoteTaskStatus | None = None
    text: str | None = None
    result: Any | None = None
    artifact: Any | None = None
    done: bool = False
    context_id: str | None = None
    task_id: str | None = None
    agent_url: str | None = None
    meta: Mapping[str, Any] | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(slots=True)
class RemoteTaskPage:
    """Paginated task list response.

    Attributes:
        tasks: Page of normalized task snapshots.
        next_page_token: Opaque token to fetch the next page; empty string if there is no next page.
        page_size: Number of items requested per page.
        total_size: Total number of tasks available across all pages, if reported by the transport.
    """

    tasks: list[RemoteTaskSnapshot]
    next_page_token: str = ""
    page_size: int = 0
    total_size: int = 0


@dataclass(slots=True)
class RemotePushNotificationBinding:
    """Normalized push notification config returned by remote transports.

    Attributes:
        name: Optional identifier/name of the push notification configuration.
        config: The push notification configuration payload (transport-specific shape).
    """

    name: str | None
    config: Mapping[str, Any]


class RemoteTaskInputRequired(RuntimeError):
    """Raised when a remote task needs user clarification before it can continue.

    Attributes:
        snapshot: The :class:`RemoteTaskSnapshot` that triggered this exception, carrying the
            remote task's current state (including any status message).
    """

    def __init__(self, snapshot: RemoteTaskSnapshot) -> None:
        """Initialize the exception from a remote task snapshot.

        Args:
            snapshot: Task snapshot whose status indicated ``INPUT_REQUIRED``.
        """
        detail = snapshot.status.message or "Remote agent requires more input."
        super().__init__(detail)
        self.snapshot = snapshot


class RemoteTaskAuthRequired(RuntimeError):
    """Raised when a remote task requires authorization before it can continue.

    Attributes:
        snapshot: The :class:`RemoteTaskSnapshot` that triggered this exception, carrying the
            remote task's current state (including any status message).
    """

    def __init__(self, snapshot: RemoteTaskSnapshot) -> None:
        """Initialize the exception from a remote task snapshot.

        Args:
            snapshot: Task snapshot whose status indicated ``AUTH_REQUIRED``.
        """
        detail = snapshot.status.message or "Remote agent requires authorization."
        super().__init__(detail)
        self.snapshot = snapshot


class RemoteTransport(Protocol):
    """Protocol describing the minimal remote invocation surface.

    Implement this protocol to plug a custom remote-agent transport (e.g. HTTP, gRPC, A2A)
    into :func:`RemoteNode`. Only ``send``, ``stream``, and ``cancel`` are required; richer
    task-oriented operations belong to :class:`SupportsRemoteTasks`.
    """

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        """Perform a unary remote call.

        Args:
            request: The call parameters, including the message, skill, and target agent URL.

        Returns:
            The remote agent's result wrapped in a :class:`RemoteCallResult`.

        Raises:
            Exception: Implementations should raise on transport failure; PenguiFlow logs the
                error via ``remote_call_error`` events and re-raises it to the caller.
        """

    def stream(self, request: RemoteCallRequest) -> AsyncIterator[RemoteStreamEvent]:
        """Perform a remote call that yields streaming events.

        Args:
            request: The call parameters, including the message, skill, and target agent URL.

        Yields:
            :class:`RemoteStreamEvent` instances as the remote agent produces output; the final
            event should set ``done=True`` and may include a terminal ``result``.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        """Cancel a remote task identified by ``task_id`` at ``agent_url``.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to cancel.

        Raises:
            Exception: Implementations should raise on transport failure; callers in this
                module log but otherwise swallow cancellation errors defensively.
        """


@runtime_checkable
class SupportsRemoteTasks(Protocol):
    """Optional task-oriented remote execution surface.

    Implement this protocol in addition to :class:`RemoteTransport` when the remote agent
    supports long-running, resumable tasks (create/fetch/list/subscribe, plus push
    notification configuration). Runtime checks use ``isinstance(transport, SupportsRemoteTasks)``
    to detect support for these operations.
    """

    async def send_task(self, request: RemoteCallRequest, *, blocking: bool = False) -> RemoteTaskSnapshot:
        """Create or continue a remote task and return its current snapshot.

        Args:
            request: The call parameters, including the message, skill, and target agent URL.
            blocking: If ``True``, wait for the task to reach a terminal state before returning.

        Returns:
            The current :class:`RemoteTaskSnapshot` for the created/continued task.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def get_task(
        self,
        *,
        agent_url: str,
        task_id: str,
        history_length: int | None = None,
    ) -> RemoteTaskSnapshot:
        """Fetch a remote task snapshot.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to fetch.
            history_length: Optional cap on the number of history entries to return.

        Returns:
            The current :class:`RemoteTaskSnapshot` for the task.

        Raises:
            Exception: Implementations should raise if the task cannot be found or fetched.
        """

    async def list_tasks(
        self,
        *,
        agent_url: str,
        context_id: str | None = None,
        status: RemoteTaskState | str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        history_length: int | None = None,
        include_artifacts: bool = False,
    ) -> RemoteTaskPage:
        """List remote tasks, optionally filtered by context and status.

        Args:
            agent_url: Base URL of the remote agent to query.
            context_id: Optional remote context identifier to filter by.
            status: Optional task state (or raw string) to filter by.
            page_size: Maximum number of tasks to return per page.
            page_token: Optional opaque token to resume from a previous page.
            history_length: Optional cap on the number of history entries per task.
            include_artifacts: Whether to include task artifacts in the response.

        Returns:
            A :class:`RemoteTaskPage` with the matching tasks and pagination info.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    def subscribe_task(self, *, agent_url: str, task_id: str) -> AsyncIterator[RemoteTaskEvent]:
        """Subscribe to task updates.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to subscribe to.

        Yields:
            :class:`RemoteTaskEvent` instances as the remote task progresses; the final event
            should set ``done=True``.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def set_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
        config: Mapping[str, Any],
    ) -> RemotePushNotificationBinding:
        """Set a push notification config for a remote task.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to configure.
            config_id: Identifier for the push notification configuration.
            config: The push notification configuration payload (transport-specific shape).

        Returns:
            The stored :class:`RemotePushNotificationBinding`.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def get_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
    ) -> RemotePushNotificationBinding:
        """Fetch a push notification config for a remote task.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task.
            config_id: Identifier of the push notification configuration to fetch.

        Returns:
            The matching :class:`RemotePushNotificationBinding`.

        Raises:
            Exception: Implementations should raise if the configuration cannot be found.
        """

    async def list_task_push_notification_configs(
        self,
        *,
        agent_url: str,
        task_id: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> list[RemotePushNotificationBinding]:
        """List push notification configs for a remote task.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task.
            page_size: Maximum number of configurations to return per page.
            page_token: Optional opaque token to resume from a previous page.

        Returns:
            A list of :class:`RemotePushNotificationBinding` entries.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def delete_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
    ) -> None:
        """Delete a push notification config for a remote task.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task.
            config_id: Identifier of the push notification configuration to delete.

        Raises:
            Exception: Implementations should raise on transport failure.
        """


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return repr(value)


def _estimate_bytes(value: Any) -> int | None:
    """Best-effort size estimation for observability metrics."""

    if value is None:
        return None
    try:
        if isinstance(value, BaseModel):
            payload = value.model_dump(mode="json")
        else:
            payload = value
        encoded = json.dumps(payload, default=_json_default).encode("utf-8")
    except Exception:
        try:
            encoded = str(value).encode("utf-8")
        except Exception:
            return None
    return len(encoded)


def _text_bytes(text: str | None) -> int:
    if text is None:
        return 0
    return len(text.encode("utf-8"))


def _string_meta(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _merge_remote_extra(
    base: Mapping[str, Any],
    *,
    agent_url: str | None,
    context_id: str | None,
    task_id: str | None,
    additional: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    extra = dict(base)
    if agent_url is not None:
        extra["remote_agent_url"] = agent_url
    if context_id is not None:
        extra["remote_context_id"] = context_id
    if task_id is not None:
        extra["remote_task_id"] = task_id
    if additional:
        for key, value in additional.items():
            if value is not None:
                extra[key] = value
    return extra


def RemoteNode(
    *,
    transport: RemoteTransport,
    skill: str,
    agent_url: str,
    name: str,
    agent_card: Mapping[str, Any] | None = None,
    policy: NodePolicy | None = None,
    streaming: bool = False,
    record_binding: bool = True,
) -> Node:
    """Create a node that proxies work to a remote agent via ``transport``.

    The returned :class:`~penguiflow.node.Node` forwards incoming
    :class:`~penguiflow.types.Message` payloads to the remote agent, mirrors trace
    cancellation onto the remote task (when ``record_binding`` is enabled and the transport
    reports a ``task_id``), and emits ``remote_call_*``/``remote_stream_event`` observability
    events via the owning runtime.

    Args:
        transport: The :class:`RemoteTransport` implementation used to call the remote agent.
        skill: Name of the remote skill/capability to invoke.
        agent_url: Base URL of the remote agent to call.
        name: Name assigned to the returned node.
        agent_card: Optional agent card metadata forwarded with each request.
        policy: Optional :class:`~penguiflow.node.NodePolicy`; defaults to ``NodePolicy()``.
        streaming: If ``True``, use ``transport.stream`` and forward chunks via
            ``ctx.emit_chunk``; otherwise use ``transport.send`` for a single unary call.
        record_binding: If ``True``, persist a remote binding for the trace (via
            ``runtime.save_remote_binding``) and mirror trace-level cancellation onto the
            remote task.

    Returns:
        A :class:`~penguiflow.node.Node` wrapping the remote call.

    Raises:
        TypeError: If the node receives an input that is not a
            :class:`~penguiflow.types.Message`.
        RuntimeError: If the node's context is not bound to a running
            :class:`~penguiflow.core.PenguiFlow` runtime, or the context owner is not a
            :class:`~penguiflow.node.Node`.
    """

    node_policy = policy or NodePolicy()

    async def _record_binding(
        *,
        runtime: PenguiFlow,
        context: Context,
        node_owner: Node,
        trace_id: str,
        context_id: str | None,
        task_id: str | None,
        agent_url_override: str | None,
        base_extra: Mapping[str, Any],
        router_session_id: str | None,
        tenant_id: str | None,
        user_id: str | None,
    ) -> tuple[asyncio.Task[None], asyncio.Event] | None:
        if task_id is None:
            return None

        agent_ref = agent_url_override or agent_url

        if record_binding:
            binding = RemoteBinding(
                trace_id=trace_id,
                context_id=context_id,
                task_id=task_id,
                agent_url=agent_ref,
                router_session_id=router_session_id,
                remote_skill=skill,
                tenant_id=tenant_id,
                user_id=user_id,
                last_remote_task_id=task_id,
                metadata={"source": "remote_node"},
            )
            await runtime.save_remote_binding(binding)

        cancel_event = runtime.ensure_trace_event(trace_id)

        async def _issue_cancel(reason: str) -> None:
            start_cancel = time.perf_counter()
            try:
                await transport.cancel(agent_url=agent_ref, task_id=task_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                latency = (time.perf_counter() - start_cancel) * 1000
                extra = _merge_remote_extra(
                    base_extra,
                    agent_url=agent_ref,
                    context_id=context_id,
                    task_id=task_id,
                    additional={
                        "remote_cancel_reason": reason,
                        "remote_error": repr(exc),
                        "remote_status": "cancel_error",
                    },
                )
                await runtime.record_remote_event(
                    event="remote_cancel_error",
                    node=node_owner,
                    context=context,
                    trace_id=trace_id,
                    latency_ms=latency,
                    level=logging.ERROR,
                    extra=extra,
                )
                return

            latency = (time.perf_counter() - start_cancel) * 1000
            extra = _merge_remote_extra(
                base_extra,
                agent_url=agent_ref,
                context_id=context_id,
                task_id=task_id,
                additional={
                    "remote_cancel_reason": reason,
                    "remote_status": "cancelled",
                },
            )
            await runtime.record_remote_event(
                event="remote_call_cancelled",
                node=node_owner,
                context=context,
                trace_id=trace_id,
                latency_ms=latency,
                level=logging.INFO,
                extra=extra,
            )

        if cancel_event.is_set():
            await _issue_cancel("pre_cancelled")
            raise TraceCancelled(trace_id)

        async def _mirror_cancel() -> None:
            try:
                await cancel_event.wait()
            except asyncio.CancelledError:
                return
            await _issue_cancel("trace_cancel")

        cancel_task = asyncio.create_task(_mirror_cancel())
        runtime.register_external_task(trace_id, cancel_task)
        return cancel_task, cancel_event

    async def _remote_impl(message: Message, ctx: Context) -> Any:
        if not isinstance(message, Message):
            raise TypeError("Remote nodes require penguiflow.types.Message inputs")

        runtime = ctx.runtime
        if runtime is None:
            raise RuntimeError("Context is not bound to a running PenguiFlow")

        owner = ctx.owner
        if not isinstance(owner, Node):  # pragma: no cover - defensive safety
            raise RuntimeError("Remote context owner must be a Node")

        trace_id = message.trace_id
        cancel_task: asyncio.Task[None] | None = None
        cancel_event: asyncio.Event | None = None
        binding_registered = False

        remote_context_id: str | None = None
        remote_task_id: str | None = None
        remote_agent_url_final = agent_url
        response_bytes = 0
        stream_events = 0

        base_extra: dict[str, Any] = {
            "remote_skill": skill,
            "remote_transport": type(transport).__name__,
            "remote_streaming": streaming,
        }
        request_bytes = _estimate_bytes(message)
        if request_bytes is not None:
            base_extra["remote_request_bytes"] = request_bytes

        router_session_id = _string_meta(message.meta, "session_id")
        tenant_id = _string_meta(message.meta, "tenant_id", "tenant") or message.headers.tenant
        user_id = _string_meta(message.meta, "user_id")

        request = RemoteCallRequest(
            message=message,
            skill=skill,
            agent_url=agent_url,
            agent_card=agent_card,
            metadata=message.meta,
            timeout_s=node_policy.timeout_s,
            context_id=_string_meta(message.meta, "remote_context_id", "context_id"),
            task_id=_string_meta(message.meta, "remote_task_id"),
        )

        async def _ensure_binding(
            *,
            context_id: str | None,
            task_id: str | None,
            agent_url_override: str | None,
        ) -> None:
            nonlocal cancel_task, cancel_event, binding_registered
            nonlocal remote_context_id, remote_task_id, remote_agent_url_final
            if context_id is not None:
                remote_context_id = context_id
            if task_id is not None:
                remote_task_id = task_id
            if agent_url_override is not None:
                remote_agent_url_final = agent_url_override
            if binding_registered:
                return
            if task_id is None:
                return
            record = await _record_binding(
                runtime=runtime,
                context=ctx,
                node_owner=owner,
                trace_id=trace_id,
                context_id=context_id,
                task_id=task_id,
                agent_url_override=agent_url_override,
                base_extra=base_extra,
                router_session_id=router_session_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if record is None:
                return
            cancel_task, cancel_event = record
            binding_registered = True

        async def _cleanup_cancel_task() -> None:
            if cancel_task is not None:
                try:
                    if cancel_event is not None and cancel_event.is_set():
                        await cancel_task
                        return
                    if not cancel_task.done():
                        cancel_task.cancel()
                    await cancel_task
                except BaseException:  # pragma: no cover - cleanup guard
                    pass

        async def _run_stream() -> Any | None:
            nonlocal response_bytes, stream_events, remote_agent_url_final
            final_result: Any | None = None
            stream_idx = 0
            async for event in transport.stream(request):
                stream_events = stream_idx + 1
                await _ensure_binding(
                    context_id=event.context_id or remote_context_id or request.context_id,
                    task_id=event.task_id,
                    agent_url_override=event.agent_url,
                )
                if event.agent_url is not None:
                    remote_agent_url_final = event.agent_url

                chunk_bytes = 0
                if event.text is not None:
                    meta = dict(event.meta) if event.meta is not None else None
                    chunk_bytes += _text_bytes(event.text)
                    meta_bytes = _estimate_bytes(event.meta)
                    if meta_bytes is not None:
                        chunk_bytes += meta_bytes
                    await ctx.emit_chunk(
                        parent=message,
                        text=event.text,
                        done=event.done,
                        meta=meta,
                    )

                if runtime is not None:
                    meta_keys = None
                    if event.meta:
                        meta_keys = sorted(event.meta.keys())
                    extra = _merge_remote_extra(
                        base_extra,
                        agent_url=remote_agent_url_final,
                        context_id=remote_context_id,
                        task_id=remote_task_id,
                        additional={
                            "remote_stream_seq": stream_idx,
                            "remote_chunk_bytes": chunk_bytes if chunk_bytes else None,
                            "remote_chunk_done": event.done,
                            "remote_chunk_meta_keys": meta_keys,
                        },
                    )
                    await runtime.record_remote_event(
                        event="remote_stream_event",
                        node=owner,
                        context=ctx,
                        trace_id=trace_id,
                        latency_ms=(time.perf_counter() - call_start) * 1000,
                        level=logging.DEBUG,
                        extra=extra,
                    )

                if chunk_bytes:
                    response_bytes += chunk_bytes

                if event.result is not None:
                    result_bytes = _estimate_bytes(event.result)
                    if result_bytes is not None:
                        response_bytes += result_bytes
                    final_result = event.result

                stream_idx += 1

            return final_result

        call_start = time.perf_counter()

        await runtime.record_remote_event(
            event="remote_call_start",
            node=owner,
            context=ctx,
            trace_id=trace_id,
            latency_ms=0.0,
            level=logging.DEBUG,
            extra=_merge_remote_extra(
                base_extra,
                agent_url=remote_agent_url_final,
                context_id=None,
                task_id=None,
            ),
        )

        try:
            if streaming:
                final_result = await _run_stream()
                result_payload = final_result
            else:
                result = await transport.send(request)
                await _ensure_binding(
                    context_id=result.context_id or request.context_id,
                    task_id=result.task_id,
                    agent_url_override=result.agent_url,
                )
                if result.context_id is not None:
                    remote_context_id = result.context_id
                if result.task_id is not None:
                    remote_task_id = result.task_id
                if result.agent_url is not None:
                    remote_agent_url_final = result.agent_url
                result_payload = result.result
                response_size = _estimate_bytes(result_payload)
                if response_size is not None:
                    response_bytes += response_size
        except TraceCancelled:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            latency = (time.perf_counter() - call_start) * 1000
            extra = _merge_remote_extra(
                base_extra,
                agent_url=remote_agent_url_final,
                context_id=remote_context_id,
                task_id=remote_task_id,
                additional={
                    "remote_error": repr(exc),
                    "remote_status": "error",
                },
            )
            await runtime.record_remote_event(
                event="remote_call_error",
                node=owner,
                context=ctx,
                trace_id=trace_id,
                latency_ms=latency,
                level=logging.ERROR,
                extra=extra,
            )
            raise
        else:
            latency = (time.perf_counter() - call_start) * 1000
            extra = _merge_remote_extra(
                base_extra,
                agent_url=remote_agent_url_final,
                context_id=remote_context_id,
                task_id=remote_task_id,
                additional={
                    "remote_response_bytes": response_bytes,
                    "remote_stream_events": stream_events,
                    "remote_status": "success",
                },
            )
            await runtime.record_remote_event(
                event="remote_call_success",
                node=owner,
                context=ctx,
                trace_id=trace_id,
                latency_ms=latency,
                level=logging.INFO,
                extra=extra,
            )
            return result_payload
        finally:
            await _cleanup_cancel_task()

    return Node(_remote_impl, name=name, policy=node_policy)


__all__ = [
    "REMOTE_TERMINAL_TASK_STATES",
    "RemoteCallRequest",
    "RemoteCallResult",
    "RemotePushNotificationBinding",
    "RemoteStreamEvent",
    "RemoteTaskAuthRequired",
    "RemoteTaskEvent",
    "RemoteTaskInputRequired",
    "RemoteTaskPage",
    "RemoteTaskSnapshot",
    "RemoteTaskState",
    "RemoteTaskStatus",
    "RemoteTransport",
    "RemoteNode",
    "SupportsRemoteTasks",
]
