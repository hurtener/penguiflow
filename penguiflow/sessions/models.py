"""Session/task models for bidirectional streaming and background work."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


class ContextPatch(BaseModel):
    task_id: str
    spawned_from_event_id: str | None = None
    source_context_version: int | None = None
    source_context_hash: str | None = None
    context_diverged: bool = False
    completed_at: datetime = Field(default_factory=_utc_now)
    digest: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class TaskContextSnapshot(BaseModel):
    session_id: str
    task_id: str
    trace_id: str | None = None
    spawned_from_task_id: str = "foreground"
    spawned_from_event_id: str | None = None
    spawned_at: datetime = Field(default_factory=_utc_now)
    spawn_reason: str | None = None
    query: str | None = None
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"
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


class MergeStrategy(str, Enum):
    APPEND = "append"
    REPLACE = "replace"
    HUMAN_GATED = "human_gated"


class NotificationAction(BaseModel):
    id: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationPayload(BaseModel):
    severity: Literal["info", "warning", "error"] = "info"
    title: str
    body: str
    actions: list[NotificationAction] = Field(default_factory=list)


__all__ = [
    "ContextPatch",
    "MergeStrategy",
    "NotificationAction",
    "NotificationPayload",
    "StateUpdate",
    "TaskContextSnapshot",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskType",
    "UpdateType",
]
