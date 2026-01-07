"""Telemetry contracts for session/task observability.

This provides a minimal, platform-level schema that downstream teams can map to
their logging/metrics/tracing systems.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import TaskStatus, TaskType

if TYPE_CHECKING:
    pass


class TaskTelemetryEvent(BaseModel):
    event_type: Literal[
        "task_spawned",
        "task_completed",
        "task_failed",
        "task_cancelled",
        "task_group_completed",
        "task_group_failed",
    ]
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


class LoggingTaskTelemetrySink:
    """Telemetry sink that logs task events to a Python logger.

    This implementation maps task events to appropriate log levels:
    - task_spawned: INFO
    - task_completed: INFO
    - task_failed: ERROR
    - task_cancelled: WARNING
    - task_group_completed: INFO
    - task_group_failed: ERROR

    Each log message includes structured context for debugging and monitoring.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        include_extra: bool = True,
    ) -> None:
        """Initialize the logging sink.

        Args:
            logger: The logger to use. If None, uses module-level logger.
            include_extra: Whether to include the 'extra' dict in log messages.
        """
        self._logger = logger or logging.getLogger(__name__)
        self._include_extra = include_extra

    async def emit(self, event: TaskTelemetryEvent) -> None:
        """Emit a telemetry event to the logger."""
        # Build common context for all events
        context: dict[str, Any] = {
            "session_id": event.session_id,
            "task_id": event.task_id,
            "task_type": event.task_type.value if event.task_type else None,
            "trace_id": event.trace_id,
        }

        # Add optional fields if present
        if event.parent_task_id:
            context["parent_task_id"] = event.parent_task_id
        if event.mode:
            context["mode"] = event.mode
        if event.duration_ms is not None:
            context["duration_ms"] = round(event.duration_ms, 2)

        # Format context for log message
        ctx_str = " ".join(f"{k}={v}" for k, v in context.items() if v is not None)

        # Route to appropriate log level based on event type
        if event.event_type == "task_spawned":
            reason = event.spawn_reason or "unspecified"
            self._logger.info(
                "Task spawned: %s reason=%s %s",
                event.task_id,
                reason,
                ctx_str,
            )

        elif event.event_type == "task_completed":
            duration = f" ({event.duration_ms:.0f}ms)" if event.duration_ms else ""
            self._logger.info(
                "Task completed: %s%s %s",
                event.task_id,
                duration,
                ctx_str,
            )

        elif event.event_type == "task_failed":
            error = event.extra.get("error", "unknown error")
            self._logger.error(
                "Task FAILED: %s error=%s %s",
                event.task_id,
                error,
                ctx_str,
            )
            # Log additional error details if present
            if self._include_extra and event.extra:
                extra_info = {k: v for k, v in event.extra.items() if k != "error"}
                if extra_info:
                    self._logger.debug("Task failure details: %s", extra_info)

        elif event.event_type == "task_cancelled":
            reason = event.extra.get("reason", "no reason")
            self._logger.warning(
                "Task cancelled: %s reason=%s %s",
                event.task_id,
                reason,
                ctx_str,
            )

        elif event.event_type == "task_group_completed":
            group_id = event.extra.get("group_id", "unknown")
            group_name = event.extra.get("group_name", "")
            completed = event.extra.get("completed_count", 0)
            total = event.extra.get("task_count", 0)
            name_part = f" ({group_name})" if group_name else ""
            self._logger.info(
                "Task group completed: %s%s completed=%d/%d %s",
                group_id,
                name_part,
                completed,
                total,
                ctx_str,
            )

        elif event.event_type == "task_group_failed":
            group_id = event.extra.get("group_id", "unknown")
            group_name = event.extra.get("group_name", "")
            failed = event.extra.get("failed_count", 0)
            name_part = f" ({group_name})" if group_name else ""
            self._logger.error(
                "Task group FAILED: %s%s failed_count=%d %s",
                group_id,
                name_part,
                failed,
                ctx_str,
            )


__all__ = [
    "LoggingTaskTelemetrySink",
    "NoOpTaskTelemetrySink",
    "TaskTelemetryEvent",
    "TaskTelemetrySink",
]
