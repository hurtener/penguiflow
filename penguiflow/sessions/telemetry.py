"""Telemetry contracts for session/task observability.

This provides a minimal, platform-level schema that downstream teams can map to
their logging/metrics/tracing systems.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from typing_extensions import override

from .models import TaskStatus, TaskType


class TaskTelemetryEvent(BaseModel):
    """A task lifecycle event for telemetry sinks."""

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
    """Destination for task lifecycle telemetry events."""

    async def emit(self, event: TaskTelemetryEvent) -> None:
        """Record ``event``."""
        ...


class NoOpTaskTelemetrySink(TaskTelemetrySink):
    """Telemetry sink that discards every event."""

    @override
    async def emit(self, event: TaskTelemetryEvent) -> None:
        return


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
        log = {
            "task_spawned": self._log_spawned,
            "task_completed": self._log_completed,
            "task_failed": self._log_failed,
            "task_cancelled": self._log_cancelled,
            "task_group_completed": self._log_group_completed,
            "task_group_failed": self._log_group_failed,
        }.get(event.event_type)
        if log is not None:
            log(event, _context_string(event))

    def _log_spawned(self, event: TaskTelemetryEvent, ctx_str: str) -> None:
        reason = event.spawn_reason or "unspecified"
        self._logger.info(
            "Task spawned: %s reason=%s %s", event.task_id, reason, ctx_str
        )

    def _log_completed(self, event: TaskTelemetryEvent, ctx_str: str) -> None:
        duration = f" ({event.duration_ms:.0f}ms)" if event.duration_ms else ""
        self._logger.info(
            "Task completed: %s%s %s", event.task_id, duration, ctx_str
        )

    def _log_failed(self, event: TaskTelemetryEvent, ctx_str: str) -> None:
        error = event.extra.get("error", "unknown error")
        self._logger.error(
            "Task FAILED: %s error=%s %s", event.task_id, error, ctx_str
        )
        # Log additional error details if present
        if self._include_extra and event.extra:
            extra_info = {k: v for k, v in event.extra.items() if k != "error"}
            if extra_info:
                self._logger.debug("Task failure details: %s", extra_info)

    def _log_cancelled(self, event: TaskTelemetryEvent, ctx_str: str) -> None:
        reason = event.extra.get("reason", "no reason")
        self._logger.warning(
            "Task cancelled: %s reason=%s %s", event.task_id, reason, ctx_str
        )

    def _log_group_completed(
        self, event: TaskTelemetryEvent, ctx_str: str
    ) -> None:
        group_name = event.extra.get("group_name", "")
        self._logger.info(
            "Task group completed: %s%s completed=%d/%d %s",
            event.extra.get("group_id", "unknown"),
            f" ({group_name})" if group_name else "",
            event.extra.get("completed_count", 0),
            event.extra.get("task_count", 0),
            ctx_str,
        )

    def _log_group_failed(
        self, event: TaskTelemetryEvent, ctx_str: str
    ) -> None:
        group_name = event.extra.get("group_name", "")
        self._logger.error(
            "Task group FAILED: %s%s failed_count=%d %s",
            event.extra.get("group_id", "unknown"),
            f" ({group_name})" if group_name else "",
            event.extra.get("failed_count", 0),
            ctx_str,
        )


def _context_string(event: TaskTelemetryEvent) -> str:
    """Format an event's common fields as ``key=value`` pairs."""
    context: dict[str, Any] = {
        "session_id": event.session_id,
        "task_id": event.task_id,
        "task_type": event.task_type.value if event.task_type else None,
        "trace_id": event.trace_id,
    }
    if event.parent_task_id:
        context["parent_task_id"] = event.parent_task_id
    if event.mode:
        context["mode"] = event.mode
    if event.duration_ms is not None:
        context["duration_ms"] = round(event.duration_ms, 2)
    return " ".join(f"{k}={v}" for k, v in context.items() if v is not None)


__all__ = [
    "LoggingTaskTelemetrySink",
    "NoOpTaskTelemetrySink",
    "TaskTelemetryEvent",
    "TaskTelemetrySink",
]
