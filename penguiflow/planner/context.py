"""Typed protocol for planner tool execution context."""

from __future__ import annotations

from _collections_abc import Awaitable, Mapping, MutableMapping
from typing import Any, Literal, Protocol

try:
    # Optional import to help tools that share signatures with flow Context
    from penguiflow.core import Context as FlowContext
except Exception:  # pragma: no cover - defensive import
    FlowContext = object  # type: ignore[misc,assignment]

PlannerPauseReason = Literal[
    "approval_required",
    "await_input",
    "external_event",
    "constraints_conflict",
]


class ToolContext(Protocol):
    """Protocol for planner tool execution context."""

    @property
    def llm_context(self) -> Mapping[str, Any]:
        """Context visible to LLM (read-only mapping)."""

    @property
    def tool_context(self) -> dict[str, Any]:
        """Tool-only context (callbacks, telemetry objects, loggers, etc.)."""

    @property
    def meta(self) -> MutableMapping[str, Any]:
        """Combined context. Deprecated: prefer llm_context/tool_context."""

    def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> Awaitable[Any]:
        """Pause execution for human input or policy decisions."""

    def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        """Emit a streaming chunk."""

    def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        """Emit a streaming artifact chunk (e.g., partial chart config)."""


# Helper alias for tools that can accept either planner ToolContext or flow Context
AnyContext = ToolContext | FlowContext
