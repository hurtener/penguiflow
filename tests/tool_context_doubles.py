"""A complete in-memory ``ToolContext`` for tests.

Tools and ``ToolNode`` take a ``ToolContext``. ``FakeToolContext``
implements the whole protocol without a planner: artifacts go to an
in-memory store, and pauses and streamed chunks are recorded so tests can
assert on them. Test modules subclass it when they need other behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts
from penguiflow.planner.context import ToolContext
from penguiflow.sessions.session_kv import SessionKVFacade

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from penguiflow.artifacts import ArtifactStore
    from penguiflow.planner.context import PlannerPauseReason


class FakeToolContext(ToolContext):
    """In-memory ToolContext that records pauses and emitted chunks."""

    def __init__(
        self,
        *,
        tool_context: dict[str, Any] | None = None,
        llm_context: dict[str, Any] | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        """Create a context over the given dicts and artifact store.

        Args:
            tool_context: The tool context; the dict itself is kept, so a
                test can still change it afterwards.
            llm_context: The LLM-visible context.
            artifact_store: Where artifacts go; a fresh in-memory store by
                default.
        """
        self._tool_context = tool_context if tool_context is not None else {}
        self._llm_context = llm_context if llm_context is not None else {}
        self._meta: dict[str, Any] = {}
        self.artifact_store = artifact_store or InMemoryArtifactStore()
        self._kv: SessionKVFacade | None = None
        self.paused_payload: dict[str, object] | None = None
        self.chunks: list[dict[str, Any]] = []
        self.artifact_chunks: list[dict[str, Any]] = []

    @property
    @override
    def llm_context(self) -> Mapping[str, Any]:
        return self._llm_context

    @property
    @override
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    @override
    def meta(self) -> MutableMapping[str, Any]:
        return self._meta

    @property
    @override
    def _artifacts(self) -> ArtifactStore:
        return self.artifact_store

    @property
    @override
    def artifacts(self) -> ScopedArtifacts:
        return ScopedArtifacts(
            self.artifact_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )

    @property
    @override
    def kv(self) -> SessionKVFacade:
        if self._kv is None:
            self._kv = SessionKVFacade(
                state_store=None,
                artifacts=self.artifact_store,
                tool_context=self._tool_context,
            )
        return self._kv

    @override
    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> object:
        self.paused_payload = {"reason": reason, "payload": payload}
        return self.paused_payload

    @override
    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        self.chunks.append(
            {
                "stream_id": stream_id,
                "seq": seq,
                "text": text,
                "done": done,
                "meta": dict(meta or {}),
            }
        )

    @override
    async def emit_artifact(
        self,
        stream_id: str,
        chunk: object,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        self.artifact_chunks.append(
            {
                "stream_id": stream_id,
                "chunk": chunk,
                "done": done,
                "artifact_type": artifact_type,
                "meta": dict(meta or {}),
            }
        )
