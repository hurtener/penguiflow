"""Steering event models and inbox for bidirectional control."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

MAX_STEERING_PAYLOAD_BYTES = 16_384
MAX_STEERING_DEPTH = 6
MAX_STEERING_KEYS = 64
MAX_STEERING_LIST_ITEMS = 50
MAX_STEERING_STRING = 4_096


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        """Return a JSON payload that can be injected into LLM context."""
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


class SteeringValidationError(ValueError):
    """Raised when a steering event payload is invalid."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid steering payload")
        self.errors = errors


def _sanitize_value(value: Any, *, depth: int) -> Any:
    if depth <= 0:
        return "<truncated>"
    if isinstance(value, str):
        return value[:MAX_STEERING_STRING]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= MAX_STEERING_KEYS:
                sanitized["__truncated_keys__"] = True
                break
            sanitized[str(key)[:64]] = _sanitize_value(item, depth=depth - 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        items = list(value)[:MAX_STEERING_LIST_ITEMS]
        sanitized_list = [_sanitize_value(item, depth=depth - 1) for item in items]
        if len(value) > MAX_STEERING_LIST_ITEMS:
            sanitized_list.append("<truncated>")
        return sanitized_list
    return str(value)[:MAX_STEERING_STRING]


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Clamp steering payload to a JSON-serialisable, size-bounded shape."""
    sanitized = _sanitize_value(payload, depth=MAX_STEERING_DEPTH)
    if not isinstance(sanitized, dict):
        sanitized = {"value": sanitized}
    try:
        raw = json.dumps(sanitized, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"value": str(payload)[:MAX_STEERING_STRING], "truncated": True}
    if len(raw.encode("utf-8")) <= MAX_STEERING_PAYLOAD_BYTES:
        return sanitized
    fallback = {
        "truncated": True,
        "summary": raw[:MAX_STEERING_STRING],
    }
    return fallback


def sanitize_steering_event(
    event: SteeringEvent,
    *,
    max_payload_bytes: int = MAX_STEERING_PAYLOAD_BYTES,
) -> SteeringEvent:
    """Return a sanitized copy of the steering event."""
    payload = sanitize_payload(dict(event.payload or {}))
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > max_payload_bytes:
        payload = {"truncated": True, "summary": raw[:MAX_STEERING_STRING]}
    return event.model_copy(update={"payload": payload})


def validate_steering_event(event: SteeringEvent) -> None:
    """Validate steering payloads against type-specific expectations."""
    payload = event.payload
    errors: list[str] = []
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        raise SteeringValidationError(errors)

    event_type = event.event_type
    if event_type == SteeringEventType.INJECT_CONTEXT:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append("INJECT_CONTEXT requires non-empty 'text'")
        scope = payload.get("scope")
        if scope is not None and scope not in {"foreground", "task_only"}:
            errors.append("INJECT_CONTEXT 'scope' must be 'foreground' or 'task_only'")
        severity = payload.get("severity")
        if severity is not None and severity not in {"note", "correction"}:
            errors.append("INJECT_CONTEXT 'severity' must be 'note' or 'correction'")
    elif event_type == SteeringEventType.REDIRECT:
        instruction = payload.get("instruction") or payload.get("goal") or payload.get("query")
        if not isinstance(instruction, str) or not instruction.strip():
            errors.append("REDIRECT requires non-empty 'instruction' (or 'goal'/'query')")
        constraints = payload.get("constraints")
        if constraints is not None and not isinstance(constraints, dict):
            errors.append("REDIRECT 'constraints' must be an object")
    elif event_type == SteeringEventType.CANCEL:
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            errors.append("CANCEL 'reason' must be a string")
        hard = payload.get("hard")
        if hard is not None and not isinstance(hard, bool):
            errors.append("CANCEL 'hard' must be a boolean")
    elif event_type == SteeringEventType.PRIORITIZE:
        priority = payload.get("priority")
        if not isinstance(priority, int):
            errors.append("PRIORITIZE requires integer 'priority'")
    elif event_type in {SteeringEventType.APPROVE, SteeringEventType.REJECT}:
        token = payload.get("resume_token") or payload.get("patch_id") or payload.get("event_id")
        if not isinstance(token, str) or not token:
            errors.append("APPROVE/REJECT requires 'resume_token' or 'patch_id'")
        decision = payload.get("decision")
        if decision is not None and not isinstance(decision, str):
            errors.append("APPROVE/REJECT 'decision' must be a string")
    elif event_type in {SteeringEventType.PAUSE, SteeringEventType.RESUME}:
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            errors.append("PAUSE/RESUME 'reason' must be a string")
    elif event_type == SteeringEventType.USER_MESSAGE:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append("USER_MESSAGE requires non-empty 'text'")
        active_tasks = payload.get("active_tasks")
        if active_tasks is not None and not isinstance(active_tasks, list):
            errors.append("USER_MESSAGE 'active_tasks' must be a list")

    if errors:
        raise SteeringValidationError(errors)


class SteeringInbox:
    """Async inbox that buffers steering events and exposes cancellation state."""

    def __init__(self, *, maxsize: int = 100, max_pending_user_messages: int = 2) -> None:
        self._queue: asyncio.Queue[SteeringEvent] = asyncio.Queue(maxsize=maxsize)
        self._max_pending_user_messages = max_pending_user_messages
        self._pending_user_message_count = 0
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancel_reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_reason(self) -> str | None:
        return self._cancel_reason

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    async def push(self, event: SteeringEvent) -> bool:
        """Queue a steering event, returning False if the queue is full."""
        if event.event_type == SteeringEventType.CANCEL:
            self._cancel_reason = str(event.payload.get("reason") or "cancelled")
            self._cancel_event.set()
        elif event.event_type == SteeringEventType.PAUSE:
            self._pause_event.clear()
        elif event.event_type == SteeringEventType.RESUME:
            self._pause_event.set()

        # Enforce limit on USER_MESSAGE events
        if event.event_type == SteeringEventType.USER_MESSAGE:
            if self._pending_user_message_count >= self._max_pending_user_messages:
                return False
            self._pending_user_message_count += 1

        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            if event.event_type == SteeringEventType.USER_MESSAGE:
                self._pending_user_message_count -= 1
            return False

    def drain(self) -> list[SteeringEvent]:
        """Drain any queued steering events without blocking."""
        events: list[SteeringEvent] = []
        while True:
            try:
                event = self._queue.get_nowait()
                if event.event_type == SteeringEventType.USER_MESSAGE:
                    self._pending_user_message_count -= 1
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events

    async def next(self) -> SteeringEvent:
        """Wait for the next steering event."""
        return await self._queue.get()

    async def wait_if_paused(self) -> None:
        """Block until a RESUME arrives if the task is paused."""
        if not self._pause_event.is_set():
            await self._pause_event.wait()


class SteeringCancelled(RuntimeError):
    """Raised when a steering cancel event terminates a task."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__(reason or "steering_cancelled")
        self.reason = reason or "steering_cancelled"


__all__ = [
    "MAX_STEERING_PAYLOAD_BYTES",
    "SteeringValidationError",
    "SteeringCancelled",
    "SteeringEvent",
    "SteeringEventType",
    "SteeringInbox",
    "sanitize_payload",
    "sanitize_steering_event",
    "validate_steering_event",
]
