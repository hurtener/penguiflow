"""Session/task models for bidirectional streaming and background work.

Most persistence-facing task/steering models live in `penguiflow.state.models`.
This module re-exports them for backward compatibility.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from penguiflow.state.models import (
    StateUpdate,
    TaskContextSnapshot,
    TaskState,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


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

