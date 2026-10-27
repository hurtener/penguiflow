"""Steering event models and inbox for bidirectional control.

Steering lets an external caller (a user or another agent) influence an already-running
PenguiFlow task without cancelling and restarting it. Callers publish typed
:class:`~penguiflow.state.models.SteeringEvent` records (see
:class:`~penguiflow.state.models.SteeringEventType` for the supported kinds: injecting
context, redirecting the goal, pausing/resuming, approving/rejecting a pending action,
adjusting priority, sending a user message, or hard-cancelling) which the running task
consumes via a :class:`SteeringInbox`. This module also provides payload sanitization and
per-event-type validation so steering input can safely originate outside the trusted
process boundary.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from penguiflow.state.models import SteeringEvent, SteeringEventType

MAX_STEERING_PAYLOAD_BYTES = 16_384
MAX_STEERING_DEPTH = 6
MAX_STEERING_KEYS = 64
MAX_STEERING_LIST_ITEMS = 50
MAX_STEERING_STRING = 4_096


class SteeringValidationError(ValueError):
    """Raised when a steering event payload is invalid.

    Attributes:
        errors: List of human-readable validation error messages describing what was wrong
            with the payload for the event's :class:`~penguiflow.state.models.SteeringEventType`.
    """

    def __init__(self, errors: list[str]) -> None:
        """Initialize the error with the list of validation failures.

        Args:
            errors: Human-readable validation error messages.
        """
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
    """Clamp steering payload to a JSON-serialisable, size-bounded shape.

    Recursively truncates nested dicts/lists/strings (see ``MAX_STEERING_*`` limits) and
    falls back to a truncated string summary if the payload cannot be JSON-encoded or
    exceeds ``MAX_STEERING_PAYLOAD_BYTES``.

    Args:
        payload: Raw steering payload to sanitize.

    Returns:
        A JSON-serialisable dict guaranteed to be within the configured size/shape bounds.
    """

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
    """Return a sanitized copy of the steering event.

    Args:
        event: The steering event whose payload should be sanitized.
        max_payload_bytes: Maximum allowed size, in UTF-8 bytes, of the JSON-encoded payload
            after sanitization; larger payloads are replaced with a truncated summary.

    Returns:
        A copy of ``event`` with its ``payload`` replaced by the sanitized version.
    """

    payload = sanitize_payload(dict(event.payload or {}))
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > max_payload_bytes:
        payload = {"truncated": True, "summary": raw[:MAX_STEERING_STRING]}
    return event.model_copy(update={"payload": payload})


def validate_steering_event(event: SteeringEvent) -> None:
    """Validate steering payloads against type-specific expectations.

    Each :class:`~penguiflow.state.models.SteeringEventType` requires a different payload
    shape (e.g. ``INJECT_CONTEXT`` requires non-empty ``text``, ``REDIRECT`` requires an
    ``instruction``/``goal``/``query``, ``PRIORITIZE`` requires an integer ``priority``).

    Args:
        event: The steering event to validate.

    Raises:
        SteeringValidationError: If ``event.payload`` is not a dict, or does not satisfy the
            requirements for ``event.event_type``.
    """

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
    """Async inbox that buffers steering events and exposes cancellation state.

    A running task (typically the ReactPlanner loop) polls or awaits this inbox to react to
    externally published :class:`~penguiflow.state.models.SteeringEvent` records —
    cancelling, pausing/resuming, or draining events to fold into the next planning step.
    ``CANCEL`` and ``PAUSE``/``RESUME`` events are handled specially at :meth:`push` time so
    their effects (``cancelled``, ``wait_if_paused``) are visible even before the event is
    drained from the queue.
    """

    def __init__(self, *, maxsize: int = 100, max_pending_user_messages: int = 2) -> None:
        """Initialize the inbox.

        Args:
            maxsize: Maximum number of buffered steering events before :meth:`push` starts
                returning ``False``.
            max_pending_user_messages: Maximum number of undrained ``USER_MESSAGE`` events
                allowed in the queue at once; additional ones are rejected by :meth:`push`.
        """
        self._queue: asyncio.Queue[SteeringEvent] = asyncio.Queue(maxsize=maxsize)
        self._max_pending_user_messages = max_pending_user_messages
        self._pending_user_message_count = 0
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancel_reason: str | None = None

    @property
    def cancelled(self) -> bool:
        """Whether a ``CANCEL`` steering event has been pushed to this inbox."""
        return self._cancel_event.is_set()

    @property
    def cancel_reason(self) -> str | None:
        """The reason string from the most recent ``CANCEL`` event, if any was pushed."""
        return self._cancel_reason

    @property
    def cancel_event(self) -> asyncio.Event:
        """The underlying :class:`asyncio.Event` set when a ``CANCEL`` event is pushed."""
        return self._cancel_event

    async def push(self, event: SteeringEvent) -> bool:
        """Queue a steering event, returning False if the queue is full.

        ``CANCEL`` events set :attr:`cancel_event` and record :attr:`cancel_reason`;
        ``PAUSE``/``RESUME`` events immediately toggle the pause gate used by
        :meth:`wait_if_paused` — both take effect even if the queue itself is full.
        ``USER_MESSAGE`` events are additionally capped by ``max_pending_user_messages``.

        Args:
            event: The steering event to enqueue.

        Returns:
            ``True`` if the event was enqueued; ``False`` if the queue was full or the
            ``USER_MESSAGE`` backlog limit was reached.
        """

        if event.event_type == SteeringEventType.CANCEL:
            self._cancel_reason = str(event.payload.get("reason") or "cancelled")
            self._cancel_event.set()
        elif event.event_type == SteeringEventType.PAUSE:
            self._pause_event.clear()
        elif event.event_type == SteeringEventType.RESUME:
            self._pause_event.set()

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

    def has_event(self) -> bool:
        """Check if there are queued steering events without draining them.

        Returns:
            ``True`` if at least one event is currently buffered.
        """

        return not self._queue.empty()

    def drain(self) -> list[SteeringEvent]:
        """Drain any queued steering events without blocking.

        Returns:
            All currently buffered events, in FIFO order; an empty list if none are queued.
        """

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
        """Wait for the next steering event.

        Returns:
            The next :class:`~penguiflow.state.models.SteeringEvent` pushed to the inbox.
        """

        return await self._queue.get()

    async def wait_if_paused(self) -> None:
        """Block until a RESUME arrives if the task is paused.

        No-op if the inbox is not currently paused (i.e. no ``PAUSE`` event is outstanding).
        """

        if not self._pause_event.is_set():
            await self._pause_event.wait()


class SteeringCancelled(RuntimeError):
    """Raised when a steering cancel event terminates a task.

    Attributes:
        reason: Human-readable reason for the cancellation (defaults to
            ``"steering_cancelled"`` when none was provided).
    """

    def __init__(self, reason: str | None = None) -> None:
        """Initialize the exception with an optional cancellation reason.

        Args:
            reason: Human-readable reason for the cancellation, taken from the ``CANCEL``
                steering event's payload when available.
        """
        super().__init__(reason or "steering_cancelled")
        self.reason = reason or "steering_cancelled"


__all__ = [
    "MAX_STEERING_PAYLOAD_BYTES",
    "MAX_STEERING_DEPTH",
    "MAX_STEERING_KEYS",
    "MAX_STEERING_LIST_ITEMS",
    "MAX_STEERING_STRING",
    "SteeringValidationError",
    "SteeringCancelled",
    "SteeringEvent",
    "SteeringEventType",
    "SteeringInbox",
    "sanitize_payload",
    "sanitize_steering_event",
    "validate_steering_event",
]
