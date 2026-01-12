"""Tool-backed background job execution.

This is the "job" execution mode described in RFC_AGENT_BACKGROUND_TASKS:
- A single tool call runs asynchronously in a background task.
- No planner loop is started.

Notes:
- Pause/resume is intentionally not supported in job mode. Tools that require
  pause (OAuth, confirmations) should be executed in "subagent" mode instead.
"""

from __future__ import annotations

import json
from collections import ChainMap
from collections.abc import Mapping, MutableMapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore
from penguiflow.catalog import NodeSpec
from penguiflow.planner.context import PlannerPauseReason, ToolContext

from .models import ContextPatch, TaskContextSnapshot, TaskType, UpdateType
from .session import TaskPipeline, TaskResult, TaskRuntime


class ToolJobContext(ToolContext):
    def __init__(
        self,
        *,
        llm_context: Mapping[str, Any],
        tool_context: dict[str, Any],
        artifacts: ArtifactStore | None,
    ) -> None:
        self._llm_context = dict(llm_context)
        self._tool_context = dict(tool_context)
        self._artifacts = artifacts or NoOpArtifactStore()

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return MappingProxyType(self._llm_context)

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> MutableMapping[str, Any]:
        # Deprecated; keep minimal but mutable for ToolContext compatibility.
        return ChainMap(self._tool_context, self._llm_context)

    @property
    def artifacts(self) -> ArtifactStore:
        return self._artifacts

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        _ = reason, payload
        raise RuntimeError("pause_not_supported_in_tool_job")

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        _ = stream_id, seq, text, done, meta
        return None

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        _ = stream_id, chunk, done, artifact_type, meta
        return None


def _extract_digest(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload[:5000]]
    if isinstance(payload, Mapping):
        for key in ("raw_answer", "answer", "text", "content", "message"):
            if key in payload and payload[key] is not None:
                return [str(payload[key])[:5000]]
    return [str(payload)[:5000]]


def _infer_artifact_type(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        component = payload.get("component")
        if isinstance(component, str) and component:
            return component
        payload_type = payload.get("type")
        if isinstance(payload_type, str) and payload_type:
            return payload_type
    return None


def _extract_compact_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    allowed_keys = {
        "type",
        "chart_type",
        "chart_id",
        "analysis_id",
        "title",
        "metric",
        "unit",
    }
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            compact[key] = value
    return compact


async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifact_store: ArtifactStore,
    session_id: str | None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for field_name, field_info in out_model.model_fields.items():
        extra = field_info.json_schema_extra
        if not isinstance(extra, Mapping) or not extra.get("artifact"):
            continue
        if field_name not in observation:
            continue
        value = observation.get(field_name)
        if value is None:
            continue

        async def _store(item: Any, *, item_index: int | None) -> None:
            serialized = json.dumps(item, ensure_ascii=False)
            scope = ArtifactScope(session_id=session_id) if session_id else None
            ref = await artifact_store.put_text(
                serialized,
                mime_type="application/json",
                filename=f"{node_name}.{field_name}.json",
                namespace=f"tool_artifact.{node_name}.{field_name}",
                scope=scope,
                meta={
                    "node": node_name,
                    "field": field_name,
                    "item_index": item_index,
                },
            )
            artifact_type = _infer_artifact_type(item)
            compact_meta = _extract_compact_metadata(item)
            stub: dict[str, Any] = {
                "artifact": ref.model_dump(mode="json"),
                **compact_meta,
            }
            if artifact_type:
                stub["type"] = artifact_type
            entry: dict[str, Any] = {
                "node": node_name,
                "field": field_name,
                "artifact": stub,
            }
            if item_index is not None:
                entry["item_index"] = item_index
            artifacts.append(entry)

        if isinstance(value, list):
            for idx, item in enumerate(value):
                if item is None:
                    continue
                await _store(item, item_index=idx)
        else:
            await _store(value, item_index=None)
    return artifacts


def build_tool_job_pipeline(
    *,
    spec: NodeSpec,
    args_payload: dict[str, Any],
    artifacts: ArtifactStore | None = None,
) -> TaskPipeline:
    """Build a TaskPipeline that runs a single tool call."""

    async def _pipeline(runtime: TaskRuntime) -> TaskResult:
        runtime.emit_update(
            UpdateType.TOOL_CALL,
            {
                "tool_name": spec.name,
                "args": json.loads(json.dumps(args_payload, ensure_ascii=False)),
                "mode": "background_job",
            },
        )
        snapshot: TaskContextSnapshot = runtime.context_snapshot
        ctx = ToolJobContext(
            llm_context=snapshot.llm_context or {},
            tool_context={**(snapshot.tool_context or {}), "task_id": runtime.state.task_id},
            artifacts=artifacts,
        )
        parsed_args = spec.args_model.model_validate(args_payload)
        result = await spec.node.func(parsed_args, ctx)
        observation: BaseModel = spec.out_model.model_validate(result)
        payload = observation.model_dump(mode="json")
        session_id = snapshot.session_id if isinstance(snapshot.session_id, str) and snapshot.session_id else None
        extracted_artifacts = []
        if isinstance(payload, Mapping):
            extracted_artifacts = await _extract_artifacts_from_observation(
                node_name=spec.name,
                out_model=spec.out_model,
                observation=payload,
                artifact_store=ctx.artifacts,
                session_id=session_id,
            )
        patch = ContextPatch(
            task_id=runtime.state.task_id,
            spawned_from_event_id=runtime.context_snapshot.spawned_from_event_id,
            source_context_version=runtime.context_snapshot.context_version,
            source_context_hash=runtime.context_snapshot.context_hash,
            digest=_extract_digest(payload),
            artifacts=extracted_artifacts,
        )
        return TaskResult(
            payload=payload,
            context_patch=patch if runtime.state.task_type == TaskType.BACKGROUND else None,
            digest=patch.digest,
            artifacts=extracted_artifacts,
            sources=[],
            notification=None,
        )

    return _pipeline


__all__ = ["ToolJobContext", "build_tool_job_pipeline"]
