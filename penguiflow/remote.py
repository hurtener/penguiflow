"""Remote agent transport protocols and task models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from .types import Message


@dataclass(slots=True)
class RemoteCallRequest:
    """Input to :class:`RemoteTransport` implementations.

    Attributes:
        message: The PenguiFlow :class:`~penguiflow.types.Message` being
            forwarded to the remote agent.
        skill: Name of the remote skill/capability to invoke.
        agent_url: Base URL of the remote agent to call.
        agent_card: Optional agent card metadata (e.g. capabilities descriptor)
            for the target agent.
        metadata: Optional request metadata forwarded alongside the message
            (typically ``message.meta``).
        timeout_s: Optional per-call timeout in seconds; ``None`` means no
            explicit timeout override.
        context_id: Optional remote conversation/context identifier to continue
            an existing context.
        task_id: Optional remote task identifier to continue or resume an
            existing task.
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
        context_id: Remote conversation/context identifier assigned or reused
            by the remote agent, if any.
        task_id: Remote task identifier assigned by the remote agent, if any.
        agent_url: Agent URL that actually served the request (may differ from
            the requested ``agent_url``).
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
        meta: Optional metadata associated with this chunk (e.g. token usage,
            reasoning tags).
        context_id: Remote conversation/context identifier associated with this
            event, if known.
        task_id: Remote task identifier associated with this event, if known.
        agent_url: Agent URL that produced this event, if known (may change
            across retries/redirects).
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
        SUBMITTED: Task was accepted by the remote agent but has not started
            running yet.
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
        timestamp: Optional ISO-8601 timestamp of when this status was
            reported.
        raw: Optional raw, transport-specific status payload for debugging.
    """

    state: RemoteTaskState
    message: str | None = None
    timestamp: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(slots=True)
class RemoteTaskSnapshot:
    """Normalized task returned by task-oriented remote transports.

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
        """Return whether ``status.state`` is a terminal task state."""
        return self.status.state in REMOTE_TERMINAL_TASK_STATES


@dataclass(slots=True)
class RemoteTaskEvent:
    """Normalized event emitted by task subscriptions.

    Attributes:
        kind: Discriminator describing the kind of event (transport-specific
            string).
        task: Optional full task snapshot attached to this event.
        status: Optional status update attached to this event.
        text: Optional incremental text chunk attached to this event.
        result: Optional result payload attached to this event.
        artifact: Optional artifact attached to this event.
        done: Whether this event marks the end of the subscription stream.
        context_id: Remote conversation/context identifier associated with this
            event, if known.
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
        next_page_token: Opaque token to fetch the next page; empty string if
            there is no next page.
        page_size: Number of items requested per page.
        total_size: Total number of tasks available across all pages, if
            reported by the transport.
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
        config: The push notification configuration payload (transport-specific
            shape).
    """

    name: str | None
    config: Mapping[str, Any]


class RemoteTaskInputRequired(RuntimeError):
    """Raised when a remote task needs user clarification to continue.

    Attributes:
        snapshot: The :class:`RemoteTaskSnapshot` that triggered this
            exception, carrying the remote task's current state (including any
            status message).
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
        snapshot: The :class:`RemoteTaskSnapshot` that triggered this
            exception, carrying the remote task's current state (including any
            status message).
    """

    def __init__(self, snapshot: RemoteTaskSnapshot) -> None:
        """Initialize the exception from a remote task snapshot.

        Args:
            snapshot: Task snapshot whose status indicated ``AUTH_REQUIRED``.
        """
        detail = (
            snapshot.status.message or "Remote agent requires authorization."
        )
        super().__init__(detail)
        self.snapshot = snapshot


class RemoteTransport(Protocol):
    """Protocol describing the minimal remote invocation surface.

    Implement this protocol to plug a custom remote-agent transport (e.g. HTTP,
    gRPC, A2A) into planner toolsets such as
    ``penguiflow_a2a.A2AAgentToolset``. Only ``send``, ``stream``, and
    ``cancel`` are required; richer task-oriented operations belong to
    :class:`SupportsRemoteTasks`.
    """

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        """Perform a unary remote call.

        Args:
            request: The call parameters, including the message, skill, and
                target agent URL.

        Returns:
            The remote agent's result wrapped in a :class:`RemoteCallResult`.

        Raises:
            Exception: Implementations should raise on transport failure;
                PenguiFlow logs the error via ``remote_call_error`` events and
                re-raises it to the caller.
        """

    def stream(
        self, request: RemoteCallRequest
    ) -> AsyncIterator[RemoteStreamEvent]:
        """Perform a remote call that yields streaming events.

        Args:
            request: The call parameters, including the message, skill, and
                target agent URL.

        Yields:
            :class:`RemoteStreamEvent` instances as the remote agent produces
            output; the final event should set ``done=True`` and may include a
            terminal ``result``.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        """Cancel a remote task identified by ``task_id`` at ``agent_url``.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to cancel.

        Raises:
            Exception: Implementations should raise on transport failure;
                callers in this module log but otherwise swallow cancellation
                errors defensively.
        """


@runtime_checkable
class SupportsRemoteTasks(Protocol):
    """Optional task-oriented remote execution surface.

    Implement this protocol in addition to :class:`RemoteTransport` when the
    remote agent supports long-running, resumable tasks
    (create/fetch/list/subscribe, plus push notification configuration).
    Runtime checks use ``isinstance(transport, SupportsRemoteTasks)`` to detect
    support for these operations.
    """

    async def send_task(
        self, request: RemoteCallRequest, *, blocking: bool = False
    ) -> RemoteTaskSnapshot:
        """Create or continue a remote task and return its current snapshot.

        Args:
            request: The call parameters, including the message, skill, and
                target agent URL.
            blocking: If ``True``, wait for the task to reach a terminal state
                before returning.

        Returns:
            The current :class:`RemoteTaskSnapshot` for the created/continued
            task.

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
            history_length: Optional cap on the number of history entries to
                return.

        Returns:
            The current :class:`RemoteTaskSnapshot` for the task.

        Raises:
            Exception: Implementations should raise if the task cannot be found
                or fetched.
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
            history_length: Optional cap on the number of history entries per
                task.
            include_artifacts: Whether to include task artifacts in the
                response.

        Returns:
            A :class:`RemoteTaskPage` with the matching tasks and pagination
            info.

        Raises:
            Exception: Implementations should raise on transport failure.
        """

    def subscribe_task(
        self, *, agent_url: str, task_id: str
    ) -> AsyncIterator[RemoteTaskEvent]:
        """Subscribe to task updates.

        Args:
            agent_url: Base URL of the remote agent that owns the task.
            task_id: Identifier of the remote task to subscribe to.

        Yields:
            :class:`RemoteTaskEvent` instances as the remote task progresses;
            the final event should set ``done=True``.

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
            config: The push notification configuration payload
                (transport-specific shape).

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
            config_id: Identifier of the push notification configuration to
                fetch.

        Returns:
            The matching :class:`RemotePushNotificationBinding`.

        Raises:
            Exception: Implementations should raise if the configuration cannot
                be found.
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
            config_id: Identifier of the push notification configuration to
                delete.

        Raises:
            Exception: Implementations should raise on transport failure.
        """


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
    "SupportsRemoteTasks",
]
