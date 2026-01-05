"""Telemetry contracts for session/task observability.

This provides a minimal, platform-level schema that downstream teams can map to
their logging/metrics/tracing systems.
"""

from __future__ import annotations

import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import TaskStatus, TaskType


class TaskTelemetryEvent(BaseModel):
    event_type: Literal["task_spawned", "task_completed", "task_failed", "task_cancelled"]
    outcome: Literal["spawned", "completed", "failed", "cancelled"]
    session_id: str
    task_id: str
    parent_task_id: str | None = None
    trace_id: str | None = None
    task_type: TaskType
    status: TaskStatus
    mode: Literal["foreground", "subagent", "job"] | None = None
    spawn_reason: str | None = None
    duration_ms: float | None = None
    created_at_s: float = Field(default_factory=time.time)
    extra: dict[str, Any] = Field(default_factory=dict)


class TaskTelemetrySink(Protocol):
    async def emit(self, event: TaskTelemetryEvent) -> None: ...


class NoOpTaskTelemetrySink:
    async def emit(self, event: TaskTelemetryEvent) -> None:
        _ = event
        return None


__all__ = ["NoOpTaskTelemetrySink", "TaskTelemetryEvent", "TaskTelemetrySink"]
