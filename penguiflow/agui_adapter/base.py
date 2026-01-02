"""AG-UI adapter base classes and helpers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    EventType,
    MessagesSnapshotEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

AGUIEvent = (
    RunStartedEvent
    | RunFinishedEvent
    | RunErrorEvent
    | StepStartedEvent
    | StepFinishedEvent
    | TextMessageStartEvent
    | TextMessageContentEvent
    | TextMessageEndEvent
    | ToolCallStartEvent
    | ToolCallArgsEvent
    | ToolCallEndEvent
    | ToolCallResultEvent
    | StateSnapshotEvent
    | StateDeltaEvent
    | CustomEvent
    | MessagesSnapshotEvent
)


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    uid = uuid4().hex[:12]
    return f"{prefix}_{uid}" if prefix else uid


class AGUIAdapter(ABC):
    """Base adapter for wrapping service execution with AG-UI events."""

    def __init__(self) -> None:
        self._thread_id: str | None = None
        self._run_id: str | None = None
        self._current_message_id: str | None = None
        self._message_started: bool = False
        self._active_steps: list[str] = []  # Track active step names

    @abstractmethod
    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
        """Execute the service and yield AG-UI events."""
        raise NotImplementedError

    async def with_run_lifecycle(
        self,
        input: RunAgentInput,
        events: AsyncIterator[AGUIEvent],
        *,
        initial_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[AGUIEvent]:
        """Wrap an event stream with RUN_STARTED/FINISHED/ERROR."""
        self._thread_id = input.thread_id
        self._run_id = input.run_id

        try:
            yield RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=input.thread_id,
                run_id=input.run_id,
            )

            if initial_state is not None:
                yield StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=initial_state,
                )

            async for event in events:
                yield event

            if self._message_started:
                yield self.text_end()

            # Close any active steps before RUN_FINISHED
            for step_name in list(self._active_steps):
                yield self.step_end(step_name)

            yield RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=input.thread_id,
                run_id=input.run_id,
            )
        except Exception as exc:
            # Close any active steps before RUN_ERROR
            for step_name in list(self._active_steps):
                yield self.step_end(step_name)

            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=str(exc),
                code=type(exc).__name__,
            )
            raise
        finally:
            self._thread_id = None
            self._run_id = None
            self._current_message_id = None
            self._message_started = False
            self._active_steps.clear()

    def text_start(
        self,
        message_id: str | None = None,
        role: str = "assistant",
    ) -> TextMessageStartEvent:
        """Start a new text message stream."""
        self._current_message_id = message_id or generate_id("msg")
        self._message_started = True
        return TextMessageStartEvent(
            type=EventType.TEXT_MESSAGE_START,
            message_id=self._current_message_id,
            role=role,  # type: ignore[arg-type]
        )

    def text_content(self, delta: str) -> TextMessageContentEvent:
        """Emit a text content chunk."""
        if not self._message_started:
            raise RuntimeError("Call text_start() before text_content().")
        return TextMessageContentEvent(
            type=EventType.TEXT_MESSAGE_CONTENT,
            message_id=self._current_message_id,  # type: ignore[arg-type]
            delta=delta,
        )

    def text_end(self) -> TextMessageEndEvent:
        """End the current text message stream."""
        if not self._message_started:
            raise RuntimeError("No message to end.")
        self._message_started = False
        return TextMessageEndEvent(
            type=EventType.TEXT_MESSAGE_END,
            message_id=self._current_message_id,  # type: ignore[arg-type]
        )

    def tool_start(
        self,
        name: str,
        tool_call_id: str | None = None,
    ) -> ToolCallStartEvent:
        """Start a tool call."""
        return ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            tool_call_id=tool_call_id or generate_id("call"),
            tool_call_name=name,
            parent_message_id=self._current_message_id,
        )

    def tool_args(self, tool_call_id: str, delta: str) -> ToolCallArgsEvent:
        """Stream tool arguments."""
        return ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            tool_call_id=tool_call_id,
            delta=delta,
        )

    def tool_end(self, tool_call_id: str) -> ToolCallEndEvent:
        """End tool argument streaming."""
        return ToolCallEndEvent(
            type=EventType.TOOL_CALL_END,
            tool_call_id=tool_call_id,
        )

    def tool_result(
        self,
        tool_call_id: str,
        content: str | dict,
        message_id: str | None = None,
    ) -> ToolCallResultEvent:
        """Emit tool execution result."""
        payload = content
        if isinstance(content, dict):
            payload = json.dumps(content)
        return ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT,
            tool_call_id=tool_call_id,
            message_id=message_id or generate_id("msg"),
            role="tool",
            content=str(payload),
        )

    def state_snapshot(self, state: dict[str, Any]) -> StateSnapshotEvent:
        """Emit full state snapshot."""
        return StateSnapshotEvent(
            type=EventType.STATE_SNAPSHOT,
            snapshot=state,
        )

    def state_delta(self, operations: list[dict]) -> StateDeltaEvent:
        """Emit state delta (JSON Patch)."""
        return StateDeltaEvent(
            type=EventType.STATE_DELTA,
            delta=operations,
        )

    def state_set(self, path: str, value: Any) -> StateDeltaEvent:
        """Convenience: replace value at path."""
        return self.state_delta([{"op": "replace", "path": path, "value": value}])

    def state_add(self, path: str, value: Any) -> StateDeltaEvent:
        """Convenience: add value at path."""
        return self.state_delta([{"op": "add", "path": path, "value": value}])

    def step_start(self, name: str, **metadata: Any) -> StepStartedEvent:
        """Mark entry into a named step."""
        self._active_steps.append(name)
        return StepStartedEvent(
            type=EventType.STEP_STARTED,
            step_name=name,
            metadata=metadata if metadata else None,  # type: ignore[call-arg]
        )

    def step_end(self, name: str, **metadata: Any) -> StepFinishedEvent:
        """Mark exit from a named step."""
        if name in self._active_steps:
            self._active_steps.remove(name)
        return StepFinishedEvent(
            type=EventType.STEP_FINISHED,
            step_name=name,
            metadata=metadata if metadata else None,  # type: ignore[call-arg]
        )

    def custom(self, name: str, value: Any) -> CustomEvent:
        """Emit application-specific event."""
        return CustomEvent(
            type=EventType.CUSTOM,
            name=name,
            value=value,
        )
