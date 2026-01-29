from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from penguiflow.sessions import StreamingSession, TaskResult, TaskType
from penguiflow.sessions.models import TaskStatus
from penguiflow.sessions.telemetry import (
    LoggingTaskTelemetrySink,
    NoOpTaskTelemetrySink,
    TaskTelemetryEvent,
    TaskTelemetrySink,
)


class BufferSink(TaskTelemetrySink):
    def __init__(self) -> None:
        self.events: list[TaskTelemetryEvent] = []

    async def emit(self, event: TaskTelemetryEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_session_emits_telemetry_for_background_task() -> None:
    sink = BufferSink()
    session = StreamingSession("telemetry-session", telemetry_sink=sink)

    async def pipeline(_runtime):
        await asyncio.sleep(0.05)
        return TaskResult(payload={"answer": "ok"})

    task_id = await session.spawn_task(pipeline, task_type=TaskType.BACKGROUND, query="x")
    # Give it time to complete.
    await asyncio.sleep(0.15)

    types = [event.event_type for event in sink.events if event.task_id == task_id]
    assert "task_spawned" in types
    assert "task_completed" in types


# --- LoggingTaskTelemetrySink tests ---


def _make_event(
    event_type: str,
    outcome: str = "completed",
    **extra_fields,
) -> TaskTelemetryEvent:
    """Helper to create telemetry events for testing."""
    return TaskTelemetryEvent(
        event_type=event_type,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        session_id="test-session",
        task_id="test-task-123",
        task_type=TaskType.BACKGROUND,
        status=TaskStatus.COMPLETE,
        **extra_fields,
    )


@pytest.mark.asyncio
async def test_logging_sink_task_spawned() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_spawned",
        outcome="spawned",
        spawn_reason="user request",
        mode="job",
    )
    await sink.emit(event)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert "Task spawned" in call_args[0][0]
    assert "test-task-123" in call_args[0][1]
    assert "user request" in call_args[0][2]


@pytest.mark.asyncio
async def test_logging_sink_task_completed() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event("task_completed", duration_ms=150.5)
    await sink.emit(event)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert "Task completed" in call_args[0][0]
    assert "test-task-123" in call_args[0][1]
    assert "150ms" in call_args[0][2]


@pytest.mark.asyncio
async def test_logging_sink_task_completed_no_duration() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event("task_completed")
    await sink.emit(event)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert "Task completed" in call_args[0][0]


@pytest.mark.asyncio
async def test_logging_sink_task_failed() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_failed",
        outcome="failed",
        extra={"error": "Connection timeout", "traceback": "..."},
    )
    await sink.emit(event)

    mock_logger.error.assert_called_once()
    call_args = mock_logger.error.call_args
    assert "Task FAILED" in call_args[0][0]
    assert "Connection timeout" in call_args[0][2]


@pytest.mark.asyncio
async def test_logging_sink_task_failed_with_extra() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger, include_extra=True)

    event = _make_event(
        "task_failed",
        outcome="failed",
        extra={"error": "Connection timeout", "retry_count": 3},
    )
    await sink.emit(event)

    mock_logger.error.assert_called_once()
    mock_logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_logging_sink_task_cancelled() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_cancelled",
        outcome="cancelled",
        extra={"reason": "user requested cancellation"},
    )
    await sink.emit(event)

    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert "Task cancelled" in call_args[0][0]
    assert "user requested cancellation" in call_args[0][2]


@pytest.mark.asyncio
async def test_logging_sink_task_group_completed() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_group_completed",
        extra={
            "group_id": "grp-001",
            "group_name": "data_processing",
            "completed_count": 5,
            "task_count": 5,
        },
    )
    await sink.emit(event)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert "Task group completed" in call_args[0][0]
    assert "grp-001" in call_args[0][1]
    assert "data_processing" in call_args[0][2]


@pytest.mark.asyncio
async def test_logging_sink_task_group_failed() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_group_failed",
        outcome="failed",
        extra={
            "group_id": "grp-002",
            "group_name": "batch_upload",
            "failed_count": 2,
        },
    )
    await sink.emit(event)

    mock_logger.error.assert_called_once()
    call_args = mock_logger.error.call_args
    assert "Task group FAILED" in call_args[0][0]
    assert "grp-002" in call_args[0][1]


@pytest.mark.asyncio
async def test_logging_sink_default_logger() -> None:
    sink = LoggingTaskTelemetrySink()
    event = _make_event("task_spawned", outcome="spawned")
    # Should not raise
    await sink.emit(event)


@pytest.mark.asyncio
async def test_logging_sink_with_parent_and_trace() -> None:
    mock_logger = MagicMock(spec=logging.Logger)
    sink = LoggingTaskTelemetrySink(logger=mock_logger)

    event = _make_event(
        "task_spawned",
        outcome="spawned",
        parent_task_id="parent-123",
        trace_id="trace-abc",
        mode="subagent",
    )
    await sink.emit(event)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    ctx_str = call_args[0][3]
    assert "parent_task_id=parent-123" in ctx_str
    assert "trace_id=trace-abc" in ctx_str


@pytest.mark.asyncio
async def test_noop_sink_does_nothing() -> None:
    sink = NoOpTaskTelemetrySink()
    event = _make_event("task_spawned", outcome="spawned")
    result = await sink.emit(event)
    assert result is None

