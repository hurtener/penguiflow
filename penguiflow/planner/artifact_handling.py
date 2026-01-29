"""Artifact and source handling helpers for the React planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ValidationError

from ..artifacts import ArtifactRef, ArtifactScope, ArtifactStore
from .artifact_registry import ArtifactRegistry
from .llm import _unwrap_model
from .models import PlannerEvent, Source
from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")


class _EventEmittingArtifactStoreProxy:
    """Proxy that wraps an ArtifactStore and emits artifact_stored events.

    This enables real-time notification to frontends when binary artifacts
    are stored (e.g., PDFs from MCP tools).
    """

    __slots__ = ("_store", "_emit_event", "_time_source", "_trajectory", "_namespace", "_registry")

    def __init__(
        self,
        store: ArtifactStore,
        emit_event: Callable[[PlannerEvent], None],
        time_source: Callable[[], float],
        trajectory: Trajectory,
        namespace: str | None = None,
        registry: ArtifactRegistry | None = None,
    ) -> None:
        self._store = store
        self._emit_event = emit_event
        self._time_source = time_source
        self._trajectory = trajectory
        self._namespace = namespace
        self._registry = registry

    def _resolve_scope(self, scope: ArtifactScope | None) -> ArtifactScope | None:
        """Inject session_id from trajectory if scope is missing."""
        if scope is not None:
            return scope
        # Get session_id from trajectory's tool_context for proper session scoping
        tool_ctx = self._trajectory.tool_context
        if tool_ctx and isinstance(tool_ctx, dict):
            session_id = tool_ctx.get("session_id")
            if session_id:
                return ArtifactScope(session_id=str(session_id))
        return None

    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store binary data and emit artifact_stored event."""
        resolved_scope = self._resolve_scope(scope)
        ref = await self._store.put_bytes(
            data,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=resolved_scope,
            meta=meta,
        )
        self._emit_artifact_stored_event(ref, len(data), namespace)
        return ref

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store large text and emit artifact_stored event."""
        resolved_scope = self._resolve_scope(scope)
        ref = await self._store.put_text(
            text,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=resolved_scope,
            meta=meta,
        )
        self._emit_artifact_stored_event(ref, len(text.encode("utf-8")), namespace)
        return ref

    def _emit_artifact_stored_event(
        self,
        ref: ArtifactRef,
        size_bytes: int,
        namespace: str | None,
    ) -> None:
        """Emit artifact_stored event for real-time UI updates."""
        if self._registry is not None:
            source_tool = namespace or self._namespace
            self._registry.register_binary_artifact(
                ref,
                source_tool=source_tool,
                step_index=len(self._trajectory.steps),
            )
            if isinstance(self._trajectory.metadata, MutableMapping):
                self._registry.write_snapshot(self._trajectory.metadata)
        self._emit_event(
            PlannerEvent(
                event_type="artifact_stored",
                ts=self._time_source(),
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "artifact_id": ref.id,
                    "mime_type": ref.mime_type,
                    "size_bytes": size_bytes,
                    "artifact_filename": ref.filename,  # Use artifact_filename to avoid LogRecord conflict
                    "source": {"namespace": namespace or self._namespace},
                },
            )
        )

    # Delegate all other methods to the underlying store
    async def get(self, artifact_id: str) -> bytes | None:
        return await self._store.get(artifact_id)

    async def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        return await self._store.get_ref(artifact_id)

    async def delete(self, artifact_id: str) -> bool:
        return await self._store.delete(artifact_id)

    async def exists(self, artifact_id: str) -> bool:
        return await self._store.exists(artifact_id)


class _ArtifactCollector:
    """Collect artifact-marked fields during planner execution."""

    def __init__(self, existing: Mapping[str, Any] | None = None) -> None:
        self._artifacts: dict[str, Any] = dict(existing or {})

    def collect(
        self,
        node_name: str,
        out_model: type[BaseModel],
        observation: Mapping[str, Any],
    ) -> None:
        if not isinstance(observation, Mapping):
            return

        collected: dict[str, Any] = {}
        for field_name, field_info in out_model.model_fields.items():
            extra = field_info.json_schema_extra
            if not isinstance(extra, Mapping):
                extra = {}
            if extra.get("artifact") and field_name in observation:
                collected[field_name] = observation[field_name]

        if not collected:
            return

        existing = self._artifacts.get(node_name, {})
        merged = dict(existing)
        merged.update(collected)
        self._artifacts[node_name] = merged

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._artifacts)


def _model_json_schema_extra(model: type[BaseModel]) -> Mapping[str, Any]:
    """Return json_schema_extra from model config (ConfigDict or legacy Config)."""

    config_extra: Mapping[str, Any] | None = None
    config = getattr(model, "model_config", None)
    if isinstance(config, Mapping):
        raw_extra = config.get("json_schema_extra")
        if isinstance(raw_extra, Mapping):
            config_extra = raw_extra

    legacy_config = getattr(model, "Config", None)
    if legacy_config is not None:
        legacy_extra = getattr(legacy_config, "json_schema_extra", None)
        if isinstance(legacy_extra, Mapping):
            config_extra = {**(config_extra or {}), **legacy_extra}

    return config_extra or {}


def _produces_sources(model: type[BaseModel]) -> bool:
    """Check whether the model declares that it produces sources."""

    extra = _model_json_schema_extra(model)
    return bool(extra.get("produces_sources"))


def _source_field_map(model: type[BaseModel]) -> dict[str, str]:
    """Build mapping of model field names to Source fields."""

    mapping: dict[str, str] = {}
    for field_name, field_info in model.model_fields.items():
        extra = field_info.json_schema_extra
        if not isinstance(extra, Mapping):
            extra = {}
        target = extra.get("source_field")
        if target is None and field_name in Source.model_fields:
            target = field_name
        if target:
            mapping[field_name] = str(target)
    return mapping


def _extract_source_payloads(
    out_model: type[BaseModel],
    observation: Any,
) -> list[Mapping[str, Any]]:
    """Extract potential Source payloads from an observation."""

    if observation is None:
        return []
    if isinstance(observation, BaseModel):
        observation = observation.model_dump(mode="json")
    if not isinstance(observation, Mapping):
        return []

    payloads: list[Mapping[str, Any]] = []

    if _produces_sources(out_model):
        mapping = _source_field_map(out_model)
        if mapping:
            payload = {
                target: observation.get(field_name)
                for field_name, target in mapping.items()
                if field_name in observation
            }
            if payload:
                payloads.append(payload)

    for field_name, field_info in out_model.model_fields.items():
        nested_model = _unwrap_model(field_info.annotation)
        if nested_model is None:
            continue
        nested_value = observation.get(field_name)
        if nested_value is None:
            continue

        if isinstance(nested_value, Sequence) and not isinstance(nested_value, (str, bytes, bytearray, Mapping)):
            for item in nested_value:
                payloads.extend(_extract_source_payloads(nested_model, item))
        elif isinstance(nested_value, Mapping) or isinstance(nested_value, BaseModel):
            payloads.extend(_extract_source_payloads(nested_model, nested_value))

    return payloads


class _SourceCollector:
    """Collect Source objects emitted by tools during execution."""

    def __init__(self, existing: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._sources: list[Source] = []
        self._seen: set[tuple[str, str | None, str | None]] = set()
        for src in existing or []:
            self._add(src)

    def _add(self, payload: Mapping[str, Any] | Source) -> None:
        try:
            model = payload if isinstance(payload, Source) else Source.model_validate(payload)
        except ValidationError as exc:
            logger.debug("source_validation_failed", extra={"error": str(exc)})
            return

        key = (model.title, model.url, model.snippet)
        if key in self._seen:
            return
        self._seen.add(key)
        self._sources.append(model)

    def collect(self, out_model: type[BaseModel], observation: Mapping[str, Any]) -> None:
        if not isinstance(observation, Mapping):
            return
        for payload in _extract_source_payloads(out_model, observation):
            self._add(payload)

    def snapshot(self) -> list[Mapping[str, Any]]:
        return [src.model_dump(mode="json") for src in self._sources]


def _normalise_artifact_value(value: Any) -> Any:
    """Best-effort conversion of artifact chunks to JSON-serialisable payloads."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        try:
            return json.loads(json.dumps(value, default=str, ensure_ascii=False))
        except Exception:
            return repr(value)
