"""Data models used by StateStore adapters and related subsystems.

This module centralises persistence-facing models to reduce the number of
protocols downstream teams must implement.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from penguiflow.metrics import FlowEvent


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class StoredEvent:
    """Representation of a runtime event persisted by a state store.

    Attributes:
        trace_id: Trace id the event belongs to, if any.
        ts: Unix timestamp (seconds) when the event occurred.
        kind: Event type/kind, mirrors `FlowEvent.event_type`.
        node_name: Name of the node that emitted the event, if applicable.
        node_id: Id of the node instance that emitted the event, if applicable.
        payload: Event-specific payload data.
    """

    trace_id: str | None
    ts: float
    kind: str
    node_name: str | None
    node_id: str | None
    payload: Mapping[str, Any]

    @classmethod
    def from_flow_event(cls, event: FlowEvent) -> StoredEvent:
        """Create a stored representation from a :class:`~penguiflow.metrics.FlowEvent`."""

        return cls(
            trace_id=event.trace_id,
            ts=event.ts,
            kind=event.event_type,
            node_name=event.node_name,
            node_id=event.node_id,
            payload=event.to_payload(),
        )


@dataclass(slots=True)
class RemoteBinding:
    """Association between a trace and a remote worker/agent.

    Attributes:
        trace_id: Local trace id the binding is scoped to.
        context_id: Remote context id, if the remote protocol uses one.
        task_id: Remote task id assigned by the remote agent.
        agent_url: URL of the remote agent handling the task.
        router_session_id: Local session id that initiated the remote call, if any.
        remote_skill: Name of the remote skill/capability invoked, if any.
        tenant_id: Optional tenant scope for the binding.
        user_id: Optional user scope for the binding.
        last_remote_task_id: Most recent remote task id seen for follow-up turns, if any.
        is_terminal: Whether the binding has reached a terminal state and should no
            longer be reused.
        metadata: Additional free-form metadata about the binding.
    """

    trace_id: str
    context_id: str | None
    task_id: str
    agent_url: str
    router_session_id: str | None = None
    remote_skill: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    last_remote_task_id: str | None = None
    is_terminal: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


class UpdateType(str, Enum):
    """Kind of a `StateUpdate` emitted while a task runs.

    Members:
        THINKING: Intermediate reasoning/plan content.
        PROGRESS: Free-form progress notification.
        TOOL_CALL: A tool invocation was made.
        RESULT: Final result content for a task.
        ERROR: An error occurred.
        CHECKPOINT: A resumable checkpoint was recorded.
        STATUS_CHANGE: The task's status changed.
        NOTIFICATION: A user-facing notification.
    """

    THINKING = "THINKING"
    PROGRESS = "PROGRESS"
    TOOL_CALL = "TOOL_CALL"
    RESULT = "RESULT"
    ERROR = "ERROR"
    CHECKPOINT = "CHECKPOINT"
    STATUS_CHANGE = "STATUS_CHANGE"
    NOTIFICATION = "NOTIFICATION"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    FOREGROUND = "FOREGROUND"
    BACKGROUND = "BACKGROUND"


class TaskContextSnapshot(BaseModel):
    session_id: str
    task_id: str
    trace_id: str | None = None
    spawned_from_task_id: str = "foreground"
    spawned_from_event_id: str | None = None
    spawned_at: datetime = Field(default_factory=_utc_now)
    spawn_reason: str | None = None
    query: str | None = None
    propagate_on_cancel: str = "cascade"
    notify_on_complete: bool = True
    context_version: int | None = None
    context_hash: str | None = None
    llm_context: dict[str, Any] = Field(default_factory=dict)
    tool_context: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class StateUpdate(BaseModel):
    """An incremental update about a task's progress, persisted for later retrieval.

    Used to stream status, tool-call, and result information for a task independent of
    the main event log, so clients can poll or subscribe to task-scoped updates.
    """

    session_id: str = Field(description="Session id the update belongs to.")
    task_id: str = Field(description="Task id the update belongs to.")
    trace_id: str | None = Field(default=None, description="Trace id associated with the update, if any.")
    update_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex, description="Unique identifier for this update."
    )
    update_type: UpdateType = Field(description="Kind of update being recorded.")
    content: Any = Field(description="Update payload; shape depends on `update_type`.")
    step_index: int | None = Field(
        default=None, description="Zero-based index of this step, if the task reports progress by step."
    )
    total_steps: int | None = Field(default=None, description="Total number of steps expected, if known.")
    created_at: datetime = Field(default_factory=_utc_now, description="Timestamp the update was created.")


@dataclass(slots=True)
class TaskState:
    task_id: str
    session_id: str
    status: TaskStatus
    task_type: TaskType
    priority: int
    context_snapshot: TaskContextSnapshot
    trace_id: str | None = None
    result: Any | None = None
    error: str | None = None
    description: str | None = None
    progress: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def update_status(self, status: TaskStatus) -> None:
        self.status = status
        self.updated_at = _utc_now()


class TaskStateModel(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    task_type: TaskType
    priority: int
    context_snapshot: TaskContextSnapshot
    trace_id: str | None = None
    result: Any | None = None
    error: str | None = None
    description: str | None = None
    progress: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_state(cls, state: TaskState) -> TaskStateModel:
        return cls(
            task_id=state.task_id,
            session_id=state.session_id,
            status=state.status,
            task_type=state.task_type,
            priority=state.priority,
            context_snapshot=state.context_snapshot,
            trace_id=state.trace_id,
            result=state.result,
            error=state.error,
            description=state.description,
            progress=state.progress,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )


class SteeringEventType(str, Enum):
    INJECT_CONTEXT = "INJECT_CONTEXT"
    REDIRECT = "REDIRECT"
    CANCEL = "CANCEL"
    PRIORITIZE = "PRIORITIZE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    USER_MESSAGE = "USER_MESSAGE"


class SteeringEvent(BaseModel):
    session_id: str
    task_id: str
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: SteeringEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    source: str = "user"
    created_at: datetime = Field(default_factory=_utc_now)

    def to_injection(self) -> str:
        payload = {
            "steering": {
                "event_id": self.event_id,
                "task_id": self.task_id,
                "event_type": self.event_type.value,
                "payload": dict(self.payload),
                "created_at": self.created_at.isoformat(),
            }
        }
        return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "RemoteBinding",
    "StateUpdate",
    "SteeringEvent",
    "SteeringEventType",
    "StoredEvent",
    "TaskContextSnapshot",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskType",
    "UpdateType",
]
