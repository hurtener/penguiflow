"""Planner context wrapper for tool execution."""

from __future__ import annotations

import warnings
from collections import ChainMap, defaultdict
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from ..artifacts import ArtifactStore, ScopedArtifacts
from .artifact_handling import (
    _EventEmittingArtifactStoreProxy,
    _normalise_artifact_value,
)
from .context import PlannerPauseReason, ToolContext
from .models import PlannerEvent, PlannerPause
from .streaming import _ArtifactChunk, _StreamChunk

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from penguiflow.sessions.session_kv import SessionKVFacade

    from .react import ReactPlanner
    from .trajectory import Trajectory


class _PlannerContext(ToolContext):
    __slots__ = (
        "_artifact_chunks",
        "_artifact_proxy",
        "_artifact_seq",
        "_chunks",
        "_kv",
        "_llm_context",
        "_meta_warned",
        "_scoped_artifacts",
        "_tool_context",
        "_trajectory",
        "planner",
    )

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self._llm_context = dict(trajectory.llm_context or {})
        self._tool_context = dict(trajectory.tool_context or {})
        self.planner = planner
        self._trajectory = trajectory
        self._chunks: list[_StreamChunk] = []
        self._artifact_chunks: list[_ArtifactChunk] = []
        self._artifact_seq: defaultdict[str, int] = defaultdict(int)
        self._artifact_proxy = _EventEmittingArtifactStoreProxy(
            store=planner.artifact_store,
            emit_event=planner.emit_event,
            time_source=planner.time_source,
            trajectory=trajectory,
            registry=planner.artifact_registry,
        )
        tc = self._tool_context
        self._scoped_artifacts = ScopedArtifacts(
            self._artifact_proxy,
            tenant_id=str(tc["tenant_id"])
            if tc.get("tenant_id") is not None
            else None,
            user_id=str(tc["user_id"])
            if tc.get("user_id") is not None
            else None,
            session_id=str(tc["session_id"])
            if tc.get("session_id") is not None
            else None,
            trace_id=str(tc["trace_id"])
            if tc.get("trace_id") is not None
            else None,
        )
        self._meta_warned = False
        self._kv: SessionKVFacade | None = None

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return MappingProxyType(self._llm_context)

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> MutableMapping[str, Any]:
        if not self._meta_warned:
            warnings.warn(
                "ctx.meta is deprecated; use ctx.llm_context and "
                "ctx.tool_context instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._meta_warned = True
        return ChainMap(self._tool_context, self._llm_context)

    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""
        return self._artifact_proxy

    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers."""
        return self._scoped_artifacts

    @property
    @override
    def kv(self) -> SessionKVFacade:
        from penguiflow.sessions.session_kv import SessionKVFacade

        if self._kv is None:

            def _emit(event_type: str, payload: Mapping[str, Any]) -> None:
                self.planner.emit_event(
                    PlannerEvent(
                        event_type=event_type,
                        ts=self.planner.time_source(),
                        trajectory_step=len(self._trajectory.steps),
                        extra=dict(payload),
                    )
                )

            self._kv = SessionKVFacade(
                state_store=self.planner.state_store,
                artifacts=self._artifact_proxy,
                tool_context=self._tool_context,
                emit_planner_event=_emit,
            )
        return self._kv

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit streaming chunk during tool execution."""
        combined_meta = {"channel": "thinking"}
        combined_meta.update(meta or {})

        chunk = _StreamChunk(
            stream_id=stream_id,
            seq=seq,
            text=text,
            done=done,
            meta=dict(combined_meta),
            ts=self.planner.time_source(),
        )
        self._chunks.append(chunk)

        self.planner.emit_event(
            PlannerEvent(
                event_type="stream_chunk",
                ts=chunk.ts,
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "stream_id": stream_id,
                    "seq": seq,
                    "text": text,
                    "done": done,
                    "meta": dict(combined_meta),
                },
            )
        )

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: object,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit a streaming artifact chunk during tool execution."""
        serialised_chunk = _normalise_artifact_value(chunk)
        seq = self._artifact_seq[stream_id]
        self._artifact_seq[stream_id] += 1
        record = _ArtifactChunk(
            stream_id=stream_id,
            seq=seq,
            chunk=serialised_chunk,
            done=done,
            artifact_type=artifact_type or type(chunk).__name__,
            meta=dict(meta or {}),
            ts=self.planner.time_source(),
        )
        self._artifact_chunks.append(record)

        self.planner.emit_event(
            PlannerEvent(
                event_type="artifact_chunk",
                ts=record.ts,
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "stream_id": stream_id,
                    "seq": seq,
                    "chunk": serialised_chunk,
                    "done": done,
                    "artifact_type": record.artifact_type,
                    "meta": dict(meta or {}),
                },
            )
        )

    def collect_chunks(self) -> dict[str, list[dict[str, Any]]]:
        """Collect streaming chunks grouped by stream identifier."""
        if not self._chunks and not self._artifact_chunks:
            return {}

        streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in self._chunks:
            streams[chunk.stream_id].append(
                {
                    "seq": chunk.seq,
                    "text": chunk.text,
                    "done": chunk.done,
                    "meta": dict(chunk.meta),
                    "ts": chunk.ts,
                }
            )
        for artifact in self._artifact_chunks:
            streams[artifact.stream_id].append(
                {
                    "seq": artifact.seq,
                    "chunk": artifact.chunk,
                    "artifact_type": artifact.artifact_type,
                    "done": artifact.done,
                    "meta": dict(artifact.meta),
                    "ts": artifact.ts,
                }
            )

        for stream_chunks in streams.values():
            stream_chunks.sort(key=lambda payload: payload["seq"])

        self._chunks.clear()
        self._artifact_chunks.clear()
        self._artifact_seq.clear()
        return dict(streams)

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> PlannerPause:
        return await self.planner.pause_from_context(
            reason,
            dict(payload or {}),
            self._trajectory,
        )
