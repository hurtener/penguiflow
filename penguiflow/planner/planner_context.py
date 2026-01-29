"""Planner context wrapper for tool execution."""

from __future__ import annotations

import warnings
from collections import ChainMap, defaultdict
from collections.abc import Mapping, MutableMapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..artifacts import ArtifactStore
from .artifact_handling import _EventEmittingArtifactStoreProxy, _normalise_artifact_value
from .context import PlannerPauseReason, ToolContext
from .models import PlannerEvent, PlannerPause
from .streaming import _ArtifactChunk, _StreamChunk

if TYPE_CHECKING:
    from .react import ReactPlanner
    from .trajectory import Trajectory


class _PlannerContext(ToolContext):
    __slots__ = (
        "_llm_context",
        "_tool_context",
        "_planner",
        "_trajectory",
        "_chunks",
        "_artifact_chunks",
        "_artifact_seq",
        "_artifact_proxy",
        "_meta_warned",
    )

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self._llm_context = dict(trajectory.llm_context or {})
        self._tool_context = dict(trajectory.tool_context or {})
        self._planner = planner
        self._trajectory = trajectory
        self._chunks: list[_StreamChunk] = []
        self._artifact_chunks: list[_ArtifactChunk] = []
        self._artifact_seq: defaultdict[str, int] = defaultdict(int)
        self._artifact_proxy = _EventEmittingArtifactStoreProxy(
            store=planner._artifact_store,
            emit_event=planner._emit_event,
            time_source=planner._time_source,
            trajectory=trajectory,
            registry=planner._artifact_registry,
        )
        self._meta_warned = False

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
                "ctx.meta is deprecated; use ctx.llm_context and ctx.tool_context instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._meta_warned = True
        return ChainMap(self._tool_context, self._llm_context)

    @property
    def artifacts(self) -> ArtifactStore:
        """Binary/large-text artifact storage.

        Use this to store binary content (PDFs, images) or large text
        out-of-band, keeping only compact ArtifactRef in LLM context.

        Note: This returns an event-emitting proxy that notifies frontends
        when artifacts are stored (e.g., for real-time UI updates).
        """
        return self._artifact_proxy

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
            ts=self._planner._time_source(),
        )
        self._chunks.append(chunk)

        self._planner._emit_event(
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
        chunk: Any,
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
            ts=self._planner._time_source(),
        )
        self._artifact_chunks.append(record)

        self._planner._emit_event(
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

    def _collect_chunks(self) -> dict[str, list[dict[str, Any]]]:
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
        return await self._planner._pause_from_context(
            reason,
            dict(payload or {}),
            self._trajectory,
        )
