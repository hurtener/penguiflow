"""Artifact registry for tool-generated UI and binary artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard, cast

from pydantic import BaseModel

from penguiflow.artifacts import ArtifactRef

ArtifactKind = Literal["ui_component", "binary", "tool_artifact"]


@dataclass(slots=True)
class ArtifactRecord:
    ref: str
    kind: ArtifactKind
    source_tool: str | None = None
    step_index: int | None = None
    field_name: str | None = None
    item_index: int | None = None
    component: str | None = None
    title: str | None = None
    summary: str | None = None
    artifact_id: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    scope_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "source_tool": self.source_tool,
            "component": self.component,
            "title": self.title,
            "summary": self.summary,
            "artifact_id": self.artifact_id,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_step": self.step_index,
            "renderable": bool(self.component or self.kind == "binary"),
            "metadata": dict(self.metadata),
        }

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "source_tool": self.source_tool,
            "step_index": self.step_index,
            "field_name": self.field_name,
            "item_index": self.item_index,
            "component": self.component,
            "title": self.title,
            "summary": self.summary,
            "artifact_id": self.artifact_id,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "scope_session_id": self.scope_session_id,
            "metadata": dict(self.metadata),
        }


class ArtifactRegistry:
    """Track artifacts produced during planner execution."""

    def __init__(self) -> None:
        self._records: list[ArtifactRecord] = []
        self._records_by_ref: dict[str, ArtifactRecord] = {}
        self._payloads: dict[str, Any] = {}
        self._counter = 0
        self._binary_index: dict[str, str] = {}
        self._external_keys: set[str] = set()

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any] | None) -> ArtifactRegistry:
        registry = cls()
        if not snapshot:
            return registry
        if not isinstance(snapshot, Mapping):
            return registry
        registry._counter = int(snapshot.get("counter", 0) or 0)
        for item in snapshot.get("records", []) or []:
            if not isinstance(item, Mapping):
                continue
            kind_value = item.get("kind")
            if kind_value not in _ALLOWED_KINDS:
                kind_value = "tool_artifact"
            record = ArtifactRecord(
                ref=str(item.get("ref", "")),
                kind=cast(ArtifactKind, kind_value),
                source_tool=item.get("source_tool"),
                step_index=item.get("step_index"),
                field_name=item.get("field_name"),
                item_index=item.get("item_index"),
                component=item.get("component"),
                title=item.get("title"),
                summary=item.get("summary"),
                artifact_id=item.get("artifact_id"),
                mime_type=item.get("mime_type"),
                size_bytes=item.get("size_bytes"),
                scope_session_id=item.get("scope_session_id"),
                metadata=dict(item.get("metadata") or {}),
            )
            registry._records.append(record)
            if record.ref:
                registry._records_by_ref[record.ref] = record
            external_key = record.metadata.get("_external_key")
            if isinstance(external_key, str) and external_key:
                registry._external_keys.add(external_key)
            if record.artifact_id:
                registry._binary_index[record.artifact_id] = record.ref
        return registry

    def snapshot(self) -> dict[str, Any]:
        return {
            "counter": self._counter,
            "records": [record.to_snapshot() for record in self._records],
        }

    def write_snapshot(self, metadata: dict[str, Any]) -> None:
        metadata["artifact_registry"] = self.snapshot()

    def list_records(
        self,
        *,
        kind: str | None = None,
        source_tool: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        records = list(self._records)
        if kind and kind != "all":
            records = [record for record in records if record.kind == kind]
        if source_tool:
            records = [record for record in records if record.source_tool == source_tool]
        if limit is not None and limit > 0:
            records = records[-limit:]
        return [record.to_public() for record in records]

    def register_tool_artifacts(
        self,
        tool_name: str,
        out_model: type[BaseModel],
        observation: Mapping[str, Any],
        *,
        step_index: int,
        metadata_extra: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(observation, Mapping):
            return
        for field_name, field_info in out_model.model_fields.items():
            extra = field_info.json_schema_extra
            if not isinstance(extra, Mapping) or not extra.get("artifact"):
                continue
            if field_name not in observation:
                continue
            value = observation[field_name]
            if isinstance(value, list):
                for idx, item in enumerate(value):
                    self.register_tool_artifact(
                        tool_name,
                        field_name,
                        item,
                        step_index=step_index,
                        item_index=idx,
                        metadata_extra=metadata_extra,
                    )
            else:
                self.register_tool_artifact(
                    tool_name,
                    field_name,
                    value,
                    step_index=step_index,
                    metadata_extra=metadata_extra,
                )

    def register_tool_artifact(
        self,
        tool_name: str,
        field_name: str,
        payload: Any,
        *,
        step_index: int,
        item_index: int | None = None,
        metadata_extra: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        ref = self._next_ref()
        component = _infer_component_name(payload)
        title = _extract_title(payload)
        summary = _extract_summary(payload, component)
        metadata = _extract_metadata(payload)
        if metadata_extra:
            metadata = {**metadata, **metadata_extra}
        kind: ArtifactKind = "ui_component" if component else "tool_artifact"
        record = ArtifactRecord(
            ref=ref,
            kind=kind,
            source_tool=tool_name,
            step_index=step_index,
            field_name=field_name,
            item_index=item_index,
            component=component,
            title=title,
            summary=summary,
            metadata=metadata,
        )
        self._records.append(record)
        self._records_by_ref[ref] = record
        self._payloads[ref] = payload
        return record

    def ingest_llm_context(self, llm_context: Any, *, step_index: int | None = None) -> None:
        """Ingest artifacts embedded in llm_context ContextPatch payloads.

        Background task results are merged into session llm_context as serialized
        ContextPatch dicts (typically under `background_result(s)`), including an
        `artifacts` list. Those need to be registered into the in-run
        ArtifactRegistry so they can be referenced via `artifact_ref` and later
        resolved into UI component payloads.

        This is best-effort and idempotent for a given task/artifact key.
        """
        if not isinstance(llm_context, Mapping):
            return
        target_step = 0 if step_index is None else int(step_index)

        patches: list[Mapping[str, Any]] = []
        single = llm_context.get("background_result")
        if isinstance(single, Mapping):
            patches.append(single)
        many = llm_context.get("background_results")
        if isinstance(many, list):
            for item in many:
                if isinstance(item, Mapping):
                    patches.append(item)

        for patch in patches:
            task_id = patch.get("task_id")
            task_id_str = str(task_id) if task_id is not None else ""
            artifacts = patch.get("artifacts")
            if not isinstance(artifacts, list):
                continue
            for entry in artifacts:
                if not isinstance(entry, Mapping):
                    continue
                node = entry.get("node")
                if not isinstance(node, str) or not node:
                    continue
                field = entry.get("field")
                if not isinstance(field, str) or not field:
                    field = "artifact"
                item_index = entry.get("item_index")
                if item_index is None:
                    item_index = entry.get("index")
                if not isinstance(item_index, int):
                    item_index = None
                payload = entry.get("artifact")
                if payload is None:
                    continue

                external_key = f"bg:{task_id_str}:{node}:{field}:{item_index}"
                if external_key in self._external_keys:
                    continue
                self._external_keys.add(external_key)

                self.register_tool_artifact(
                    node,
                    field,
                    payload,
                    step_index=target_step,
                    item_index=item_index,
                    metadata_extra={
                        "_external_key": external_key,
                        "background_task_id": task_id_str,
                    },
                )

    def register_binary_artifact(
        self,
        ref: ArtifactRef,
        *,
        source_tool: str | None,
        step_index: int | None,
    ) -> ArtifactRecord:
        if ref.id in self._binary_index:
            existing_ref = self._binary_index[ref.id]
            return self._records_by_ref[existing_ref]
        record = ArtifactRecord(
            ref=ref.id,
            kind="binary",
            source_tool=source_tool,
            step_index=step_index,
            artifact_id=ref.id,
            mime_type=ref.mime_type,
            size_bytes=ref.size_bytes,
            scope_session_id=getattr(ref.scope, "session_id", None) if ref.scope else None,
            title=ref.filename,
            summary=_binary_summary(ref),
            component=_binary_component_name(ref.mime_type),
            metadata=_compact_metadata(ref.source),
        )
        self._records.append(record)
        self._records_by_ref[record.ref] = record
        self._binary_index[ref.id] = record.ref
        return record

    def resolve_ref(
        self,
        ref: str,
        *,
        trajectory: Any | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        record = self._records_by_ref.get(ref)
        if record is None:
            return None
        if record.kind == "binary":
            return _binary_component_payload(record, session_id=session_id)
        payload = self._payloads.get(ref)
        if payload is None and trajectory is not None:
            payload = _payload_from_trajectory(trajectory, record)
        if payload is None:
            return None
        component_payload = _component_payload_from_tool_payload(payload)
        if component_payload is None:
            return None
        return component_payload

    async def resolve_ref_async(
        self,
        ref: str,
        *,
        trajectory: Any | None = None,
        session_id: str | None = None,
        artifact_store: Any | None = None,
    ) -> dict[str, Any] | None:
        """Async variant of resolve_ref that can hydrate stored tool artifacts.

        Tool artifacts coming from background jobs may be stored out-of-band in an
        ArtifactStore (as JSON), with only a compact ArtifactRef stub present in
        the artifact payload.
        """
        record = self._records_by_ref.get(ref)
        if record is None:
            return None
        if record.kind == "binary":
            return _binary_component_payload(record, session_id=session_id)

        payload = self._payloads.get(ref)
        if payload is None and trajectory is not None:
            payload = _payload_from_trajectory(trajectory, record)
        if payload is None:
            return None

        hydrated = await _maybe_hydrate_stored_payload(payload, artifact_store=artifact_store)
        if hydrated is not None:
            payload = hydrated
            self._payloads[ref] = payload

        component_payload = _component_payload_from_tool_payload(payload)
        if component_payload is None:
            return None
        return component_payload

    def _next_ref(self) -> str:
        ref = f"artifact_{self._counter}"
        self._counter += 1
        return ref


def get_artifact_registry(ctx: Any) -> ArtifactRegistry | None:
    planner = getattr(ctx, "_planner", None)
    registry = getattr(planner, "_artifact_registry", None)
    if isinstance(registry, ArtifactRegistry):
        return registry
    return None


def resolve_artifact_refs(
    value: Any,
    *,
    registry: ArtifactRegistry,
    trajectory: Any | None,
    session_id: str | None,
) -> Any:
    if isinstance(value, list):
        return [
            resolve_artifact_refs(item, registry=registry, trajectory=trajectory, session_id=session_id)
            for item in value
        ]
    if isinstance(value, dict):
        if "artifact_ref" in value:
            ref = value.get("artifact_ref")
            if isinstance(ref, str):
                resolved = registry.resolve_ref(ref, trajectory=trajectory, session_id=session_id)
                if resolved is None:
                    raise RuntimeError(f"Unknown artifact_ref '{ref}'")
                merged = dict(resolved)
                for key, sub_value in value.items():
                    if key == "artifact_ref":
                        continue
                    merged[key] = resolve_artifact_refs(
                        sub_value, registry=registry, trajectory=trajectory, session_id=session_id
                    )
                return merged
        return {
            key: resolve_artifact_refs(sub_value, registry=registry, trajectory=trajectory, session_id=session_id)
            for key, sub_value in value.items()
        }
    return value


async def resolve_artifact_refs_async(
    value: Any,
    *,
    registry: ArtifactRegistry,
    trajectory: Any | None,
    session_id: str | None,
    artifact_store: Any | None,
) -> Any:
    if isinstance(value, list):
        return [
            await resolve_artifact_refs_async(
                item,
                registry=registry,
                trajectory=trajectory,
                session_id=session_id,
                artifact_store=artifact_store,
            )
            for item in value
        ]
    if isinstance(value, dict):
        if "artifact_ref" in value:
            ref = value.get("artifact_ref")
            if isinstance(ref, str):
                resolved = await registry.resolve_ref_async(
                    ref,
                    trajectory=trajectory,
                    session_id=session_id,
                    artifact_store=artifact_store,
                )
                if resolved is None:
                    raise RuntimeError(f"Unknown artifact_ref '{ref}'")
                merged = dict(resolved)
                for key, sub_value in value.items():
                    if key == "artifact_ref":
                        continue
                    merged[key] = await resolve_artifact_refs_async(
                        sub_value,
                        registry=registry,
                        trajectory=trajectory,
                        session_id=session_id,
                        artifact_store=artifact_store,
                    )
                return merged
        return {
            key: await resolve_artifact_refs_async(
                sub_value,
                registry=registry,
                trajectory=trajectory,
                session_id=session_id,
                artifact_store=artifact_store,
            )
            for key, sub_value in value.items()
        }
    return value


def has_artifact_refs(value: Any) -> bool:
    if isinstance(value, list):
        return any(has_artifact_refs(item) for item in value)
    if isinstance(value, dict):
        if "artifact_ref" in value:
            return True
        return any(has_artifact_refs(item) for item in value.values())
    return False


def _is_artifact_ref_dict(value: Any) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping) and isinstance(value.get("id"), str)


async def _maybe_hydrate_stored_payload(payload: Any, *, artifact_store: Any | None) -> Any | None:
    """Hydrate payload stored as JSON in ArtifactStore via a compact stub.

    Expected stub shape:
    - {"artifact": {"id": "...", ...}, ...}
    """
    if artifact_store is None:
        return None
    if not isinstance(payload, Mapping):
        return None
    ref_dict = payload.get("artifact")
    if not _is_artifact_ref_dict(ref_dict):
        return None
    get_fn = getattr(artifact_store, "get", None)
    if not callable(get_fn):
        return None
    raw = await get_fn(str(ref_dict["id"]))
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
    except Exception:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _component_payload_from_tool_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, Mapping):
        component = payload.get("component")
        props = payload.get("props")
        if isinstance(component, str) and isinstance(props, Mapping):
            return {"component": component, "props": dict(props)}
        component_type = payload.get("type")
        if isinstance(component_type, str):
            if component_type == "echarts":
                config = payload.get("config") or payload.get("option")
                if isinstance(config, Mapping):
                    return {"component": "echarts", "props": {"option": dict(config)}}
            if component_type == "plotly":
                data = payload.get("data")
                layout = payload.get("layout")
                config = payload.get("config")
                if isinstance(data, list) or isinstance(layout, Mapping) or isinstance(config, Mapping):
                    return {
                        "component": "plotly",
                        "props": {
                            "data": list(data) if isinstance(data, list) else [],
                            "layout": dict(layout) if isinstance(layout, Mapping) else {},
                            "config": dict(config) if isinstance(config, Mapping) else {},
                        },
                    }
            if component_type == "mermaid":
                code = payload.get("code") or payload.get("diagram")
                if isinstance(code, str):
                    return {"component": "mermaid", "props": {"code": code}}
    return None


def _binary_component_payload(record: ArtifactRecord, session_id: str | None) -> dict[str, Any]:
    artifact_id = record.artifact_id or record.ref
    url = _artifact_url(artifact_id, session_id or record.scope_session_id)
    mime_type = record.mime_type or ""
    if mime_type.startswith("image/"):
        return {
            "component": "image",
            "props": {
                "src": url,
                "alt": record.title or "artifact image",
                "caption": record.title,
                "border": True,
            },
        }
    if mime_type == "application/pdf":
        return {
            "component": "embed",
            "props": {
                "url": url,
                "title": record.title or "PDF document",
                "height": "600px",
            },
        }
    return {
        "component": "markdown",
        "props": {"content": f"[Download artifact]({url})"},
    }


def _artifact_url(artifact_id: str, session_id: str | None) -> str:
    if session_id:
        return f"/artifacts/{artifact_id}?session_id={session_id}"
    return f"/artifacts/{artifact_id}"


def _binary_component_name(mime_type: str | None) -> str | None:
    if not mime_type:
        return None
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf":
        return "embed"
    return "markdown"


def _binary_summary(ref: ArtifactRef) -> str:
    if ref.filename:
        return f"Stored {ref.filename} ({ref.mime_type or 'binary'})"
    if ref.mime_type:
        return f"Stored {ref.mime_type} artifact"
    return "Stored binary artifact"


def _payload_from_trajectory(trajectory: Any, record: ArtifactRecord) -> Any | None:
    if record.step_index is None or record.field_name is None:
        return None
    steps = getattr(trajectory, "steps", None)
    if not isinstance(steps, list):
        return None
    if record.step_index < 0 or record.step_index >= len(steps):
        return None
    step = steps[record.step_index]
    observation = getattr(step, "observation", None)
    if not isinstance(observation, Mapping):
        return None
    if record.field_name not in observation:
        return None
    value = observation[record.field_name]
    if record.item_index is not None and isinstance(value, list):
        if 0 <= record.item_index < len(value):
            return value[record.item_index]
    return value


def _infer_component_name(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        component = payload.get("component")
        if isinstance(component, str):
            return component
        component_type = payload.get("type")
        if isinstance(component_type, str):
            return component_type
    return None


def _extract_title(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        for key in ("title", "name", "label", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _compact_text(value)
    return None


def _extract_summary(payload: Any, component: str | None) -> str | None:
    if isinstance(payload, Mapping):
        summary = payload.get("summary") or payload.get("description")
        if isinstance(summary, str) and summary.strip():
            return _compact_text(summary)
    if component:
        return f"{component} artifact"
    return None


def _extract_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    allowed_keys = {
        "type",
        "chart_type",
        "chart_id",
        "analysis_id",
        "title",
        "metric",
        "unit",
    }
    for key in allowed_keys:
        if key in payload and _is_scalar(payload[key]):
            metadata[key] = payload[key]
    nested = payload.get("metadata")
    if isinstance(nested, Mapping):
        metadata.update(_compact_metadata(nested))
    return metadata


def _compact_metadata(meta: Mapping[str, Any], *, max_items: int = 8) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in meta.items():
        if len(compact) >= max_items:
            break
        if _is_scalar(value):
            compact[key] = _compact_text(value) if isinstance(value, str) else value
    return compact


def _compact_text(value: str, max_len: int = 120) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


__all__ = [
    "ArtifactRegistry",
    "ArtifactRecord",
    "ArtifactKind",
    "get_artifact_registry",
    "resolve_artifact_refs",
    "resolve_artifact_refs_async",
    "has_artifact_refs",
]

_ALLOWED_KINDS = {"ui_component", "binary", "tool_artifact"}
