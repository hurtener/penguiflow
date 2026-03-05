"""Typed protocol for planner tool execution context."""

from __future__ import annotations

from _collections_abc import Awaitable, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactStore, ScopedArtifacts

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

KVScope = Literal["session", "task"]


class SessionKV(Protocol):
    async def get(self, key: str, *, scope: KVScope = "session", namespace: str | None = None) -> Any | None: ...

    async def set(
        self,
        key: str,
        value: Any,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any: ...

    async def patch(
        self,
        key: str,
        patch: Mapping[str, Any],
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any: ...

    async def get_or_init(
        self,
        key: str,
        default: Any,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any: ...

    async def delete(
        self,
        key: str,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> bool: ...


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

    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""

    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers.

        Example:
            ref = await ctx.artifacts.upload(
                pdf_bytes,
                mime_type="application/pdf",
                filename="report.pdf",
            )
            return {"artifact": ref, "summary": "Downloaded PDF"}
        """

    @property
    def kv(self) -> SessionKV:
        """Durable session key/value facade.

        Backed by the configured StateStore's optional memory persistence.
        Default scope is session-scoped with no TTL. Task scope is opt-in and
        uses a fixed TTL of 3600 seconds.
        """

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
