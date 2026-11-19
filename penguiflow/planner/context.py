"""Typed protocol for planner tool execution context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping, MutableMapping

    from penguiflow.artifacts import ArtifactStore, ScopedArtifacts

PlannerPauseReason = Literal[
    "approval_required",
    "await_input",
    "external_event",
    "constraints_conflict",
]

KVScope = Literal["session", "task"]


class SessionKV(Protocol):
    """Durable key/value storage scoped to a session or a task."""

    async def get(
        self,
        key: str,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
    ) -> object:
        """Return the value stored under ``key``, or ``None``."""
        ...

    async def set(
        self,
        key: str,
        value: object,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> object:
        """Store ``value`` under ``key`` and return it."""
        ...

    async def patch(
        self,
        key: str,
        patch: Mapping[str, Any],
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> object:
        """Merge ``patch`` into the mapping under ``key`` and return it."""
        ...

    async def get_or_init(
        self,
        key: str,
        default: object,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> object:
        """Return the value under ``key``, storing ``default`` if unset."""
        ...

    async def delete(
        self,
        key: str,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> bool:
        """Delete ``key`` and return whether it existed."""
        ...


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
        chunk: object,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> Awaitable[None]:
        """Emit a streaming artifact chunk (e.g., partial chart config)."""


# Backwards-compatible alias; tools only ever receive a planner ToolContext.
AnyContext = ToolContext
