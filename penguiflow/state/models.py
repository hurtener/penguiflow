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
    """Representation of a runtime event persisted by a state store."""

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
    """Association between a trace and a remote worker/agent."""

    trace_id: str
    context_id: str | None
    task_id: str
    agent_url: str


class UpdateType(str, Enum):
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
    session_id: str
    task_id: str
    trace_id: str | None = None
    update_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    update_type: UpdateType
    content: Any
    step_index: int | None = None
    total_steps: int | None = None
    created_at: datetime = Field(default_factory=_utc_now)


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
