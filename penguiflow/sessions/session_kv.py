"""Durable session key-value facade for tools and nodes.

This is implemented on top of StateStore's optional short-term memory persistence
surface: `save_memory_state(key, state)` / `load_memory_state(key)`.

Design notes:
- Default scope is session-scoped with no TTL.
- Task scope is opt-in and uses a fixed TTL of 3600 seconds (not configurable).
- Concurrency is best-effort multi-writer (no CAS); we serialize per key within
  a single process to reduce local races.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from penguiflow.artifacts import ArtifactScope, ArtifactStore

KVScope = Literal["session", "task"]

_KV_PREFIX = "kv:v1"
_TASK_TTL_S = 3600
_DEFAULT_MAX_INLINE_BYTES = 8192
_REDACTED = "<redacted>"
_DEFAULT_REDACT_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "password",
        "secret",
        "client_secret",
    }
)

_LOCKS: dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_expired(state: Mapping[str, Any]) -> bool:
    expires_at = state.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return _utc_now() >= dt


def _normalise_jsonable(value: Any) -> Any:
    # Support Pydantic v2 BaseModel-like objects without importing pydantic here.
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    return value


def _redact_value(value: Any, *, redact_keys: frozenset[str]) -> Any:
    value = _normalise_jsonable(value)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if key_str.lower() in redact_keys:
                redacted[key_str] = _REDACTED
            else:
                redacted[key_str] = _redact_value(v, redact_keys=redact_keys)
        return redacted
    if isinstance(value, list):
        return [_redact_value(v, redact_keys=redact_keys) for v in value]
    if isinstance(value, tuple):
        return [_redact_value(v, redact_keys=redact_keys) for v in value]
    return value


def _require_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"session_kv_missing_required_tool_context:{key}")
    return value.strip()


async def _lock_for(key: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


@dataclass(frozen=True, slots=True)
class SessionKVConfig:
    max_inline_bytes: int = _DEFAULT_MAX_INLINE_BYTES
    redact_keys: frozenset[str] = _DEFAULT_REDACT_KEYS


class SessionKVFacade:
    """Session KV facade bound to a specific tool execution context."""

    def __init__(
        self,
        *,
        state_store: object | None,
        artifacts: ArtifactStore,
        tool_context: MutableMapping[str, Any],
        emit_planner_event: Callable[[str, Mapping[str, Any]], None] | None = None,
        emit_checkpoint_update: Callable[[Mapping[str, Any]], None] | None = None,
        config: SessionKVConfig | None = None,
    ) -> None:
        self._state_store = state_store
        self._artifacts = artifacts
        self._tool_context = tool_context
        self._emit_planner_event = emit_planner_event
        self._emit_checkpoint_update = emit_checkpoint_update
        self._config = config or SessionKVConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str, *, scope: KVScope = "session", namespace: str | None = None) -> Any | None:
        entry = await self._load_entry(key, scope=scope, namespace=namespace)
        if entry is None:
            return None
        return await self._hydrate_value(entry)

    async def get_or_init(
        self,
        key: str,
        default: Any,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any:
        composite_key = self._composite_key(key, scope=scope, namespace=namespace)
        lock = await _lock_for(composite_key)
        async with lock:
            prev_state = await self._load_state(composite_key)
            if prev_state is not None and not self._is_deleted_or_expired(prev_state, scope=scope):
                return await self._hydrate_value(prev_state)

            prev_payload = self._payload_for_update(prev_state)
            next_state = await self._build_state(default, scope=scope)
            await self._save_state(composite_key, next_state)
            next_payload = self._payload_for_update(next_state)

            if emit_update:
                self._emit(
                    event_type="kv_init",
                    content=self._checkpoint_content(
                        kind="kv_init",
                        key=key,
                        scope=scope,
                        namespace=self._resolve_namespace(namespace),
                        composite_key=composite_key,
                        prev=prev_payload,
                        next=next_payload,
                        prev_etag_seen=prev_state.get("etag") if isinstance(prev_state, Mapping) else None,
                        next_etag_written=str(next_state.get("etag") or ""),
                        expires_at=next_state.get("expires_at"),
                    ),
                )
            return await self._hydrate_value(next_state)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any:
        composite_key = self._composite_key(key, scope=scope, namespace=namespace)
        lock = await _lock_for(composite_key)
        async with lock:
            prev_state = await self._load_state(composite_key)
            if prev_state is not None and self._is_deleted_or_expired(prev_state, scope=scope):
                prev_state = None

            prev_payload = self._payload_for_update(prev_state)
            next_state = await self._build_state(value, scope=scope)
            await self._save_state(composite_key, next_state)
            next_payload = self._payload_for_update(next_state)

            if emit_update:
                self._emit(
                    event_type="kv_set",
                    content=self._checkpoint_content(
                        kind="kv_set",
                        key=key,
                        scope=scope,
                        namespace=self._resolve_namespace(namespace),
                        composite_key=composite_key,
                        prev=prev_payload,
                        next=next_payload,
                        prev_etag_seen=prev_state.get("etag") if isinstance(prev_state, Mapping) else None,
                        next_etag_written=str(next_state.get("etag") or ""),
                        expires_at=next_state.get("expires_at"),
                    ),
                )
            return await self._hydrate_value(next_state)

    async def patch(
        self,
        key: str,
        patch: Mapping[str, Any],
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> Any:
        composite_key = self._composite_key(key, scope=scope, namespace=namespace)
        lock = await _lock_for(composite_key)
        async with lock:
            prev_state = await self._load_state(composite_key)
            base: Any = None
            if prev_state is not None and not self._is_deleted_or_expired(prev_state, scope=scope):
                base = await self._hydrate_value(prev_state)
            if base is None:
                base_obj: dict[str, Any] = {}
            elif isinstance(base, Mapping):
                base_obj = dict(base)
            else:
                raise RuntimeError("session_kv_patch_requires_mapping")

            patch_redacted = _redact_value(dict(patch), redact_keys=self._config.redact_keys)
            merged = dict(base_obj)
            merged.update(patch_redacted)

            prev_payload = self._payload_for_update(prev_state)
            next_state = await self._build_state(merged, scope=scope)
            await self._save_state(composite_key, next_state)
            next_payload = self._payload_for_update(next_state)

            if emit_update:
                self._emit(
                    event_type="kv_patch",
                    content=self._checkpoint_content(
                        kind="kv_patch",
                        key=key,
                        scope=scope,
                        namespace=self._resolve_namespace(namespace),
                        composite_key=composite_key,
                        prev=prev_payload,
                        next=next_payload,
                        prev_etag_seen=prev_state.get("etag") if isinstance(prev_state, Mapping) else None,
                        next_etag_written=str(next_state.get("etag") or ""),
                        expires_at=next_state.get("expires_at"),
                    ),
                )
            return await self._hydrate_value(next_state)

    async def delete(
        self,
        key: str,
        *,
        scope: KVScope = "session",
        namespace: str | None = None,
        emit_update: bool = True,
    ) -> bool:
        composite_key = self._composite_key(key, scope=scope, namespace=namespace)
        lock = await _lock_for(composite_key)
        async with lock:
            prev_state = await self._load_state(composite_key)
            if prev_state is None or self._is_deleted_or_expired(prev_state, scope=scope):
                return False

            prev_payload = self._payload_for_update(prev_state)
            next_state: dict[str, Any] = {
                "schema_version": 1,
                "etag": uuid4().hex,
                "updated_at": _to_iso(_utc_now()),
                "deleted": True,
                "value": None,
                "redaction": {"applied": True, "rules": sorted(self._config.redact_keys)},
            }
            if scope == "task":
                next_state["expires_at"] = _to_iso(_utc_now() + timedelta(seconds=_TASK_TTL_S))
            await self._save_state(composite_key, next_state)
            next_payload = self._payload_for_update(next_state)

            if emit_update:
                self._emit(
                    event_type="kv_delete",
                    content=self._checkpoint_content(
                        kind="kv_delete",
                        key=key,
                        scope=scope,
                        namespace=self._resolve_namespace(namespace),
                        composite_key=composite_key,
                        prev=prev_payload,
                        next=next_payload,
                        prev_etag_seen=prev_state.get("etag") if isinstance(prev_state, Mapping) else None,
                        next_etag_written=str(next_state.get("etag") or ""),
                        expires_at=next_state.get("expires_at"),
                    ),
                )
            return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emit(self, *, event_type: str, content: Mapping[str, Any]) -> None:
        if self._emit_planner_event is not None:
            self._emit_planner_event(event_type, content)
        if self._emit_checkpoint_update is not None:
            self._emit_checkpoint_update(content)

    def _resolve_namespace(self, namespace: str | None) -> str:
        if namespace and namespace.strip():
            return namespace.strip()
        tool_name = self._tool_context.get("_current_tool_name")
        if isinstance(tool_name, str) and tool_name.strip():
            return f"tool:{tool_name.strip()}"
        return "unknown"

    def _composite_key(self, key: str, *, scope: KVScope, namespace: str | None) -> str:
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("session_kv_key_must_be_non_empty_string")
        tenant_id = _require_str(self._tool_context, "tenant_id")
        user_id = _require_str(self._tool_context, "user_id")
        session_id = _require_str(self._tool_context, "session_id")
        # Required for actor metadata and consistent scoping across tool/node/task execution.
        _require_str(self._tool_context, "task_id")
        _require_str(self._tool_context, "trace_id")
        ns = self._resolve_namespace(namespace)
        if scope == "session":
            return f"{_KV_PREFIX}:{tenant_id}:{user_id}:{session_id}:session:{ns}:{key.strip()}"
        task_id = _require_str(self._tool_context, "task_id")
        return f"{_KV_PREFIX}:{tenant_id}:{user_id}:{session_id}:task:{task_id}:{ns}:{key.strip()}"

    async def _load_entry(self, key: str, *, scope: KVScope, namespace: str | None) -> Mapping[str, Any] | None:
        composite_key = self._composite_key(key, scope=scope, namespace=namespace)
        state = await self._load_state(composite_key)
        if state is None:
            return None
        if self._is_deleted_or_expired(state, scope=scope):
            return None
        return state

    def _is_deleted_or_expired(self, state: Mapping[str, Any], *, scope: KVScope) -> bool:
        if bool(state.get("deleted")):
            return True
        if scope == "task" and _is_expired(state):
            return True
        return False

    async def _load_state(self, composite_key: str) -> dict[str, Any] | None:
        store = self._state_store
        if store is None or not hasattr(store, "load_memory_state"):
            raise RuntimeError("session_kv_requires_state_store_memory_methods")
        state = await store.load_memory_state(composite_key)
        return dict(state) if isinstance(state, Mapping) else None

    async def _save_state(self, composite_key: str, state: Mapping[str, Any]) -> None:
        store = self._state_store
        if store is None or not hasattr(store, "save_memory_state"):
            raise RuntimeError("session_kv_requires_state_store_memory_methods")
        await store.save_memory_state(composite_key, dict(state))

    async def _build_state(self, value: Any, *, scope: KVScope) -> dict[str, Any]:
        redacted = _redact_value(value, redact_keys=self._config.redact_keys)
        redacted = _normalise_jsonable(redacted)
        try:
            payload_json = _canonical_json(redacted)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("session_kv_value_not_json_serialisable") from exc

        payload_hash = _sha256_text(payload_json)
        payload_bytes = payload_json.encode("utf-8")
        inline: Any | None = None
        artifact_ref: dict[str, Any] | None = None

        if len(payload_bytes) <= max(0, int(self._config.max_inline_bytes)):
            inline = redacted
        else:
            scope_obj = ArtifactScope(
                tenant_id=self._tool_context.get("tenant_id"),
                user_id=self._tool_context.get("user_id"),
                session_id=self._tool_context.get("session_id"),
                trace_id=self._tool_context.get("trace_id"),
            )
            ref = await self._artifacts.put_text(
                payload_json,
                mime_type="application/json",
                filename="session_kv.json",
                namespace="session_kv",
                scope=scope_obj,
                meta={"kv_hash": payload_hash},
            )
            artifact_ref = ref.model_dump(mode="json")

        now = _utc_now()
        state: dict[str, Any] = {
            "schema_version": 1,
            "etag": uuid4().hex,
            "updated_at": _to_iso(now),
            "deleted": False,
            "value": {
                "inline": inline,
                "hash": payload_hash,
                "artifact_ref": artifact_ref,
                "mime_type": "application/json",
            },
            "redaction": {"applied": True, "rules": sorted(self._config.redact_keys)},
        }
        if scope == "task":
            state["expires_at"] = _to_iso(now + timedelta(seconds=_TASK_TTL_S))
        return state

    async def _hydrate_value(self, state: Mapping[str, Any]) -> Any | None:
        if bool(state.get("deleted")):
            return None
        value_obj = state.get("value")
        if not isinstance(value_obj, Mapping):
            return None
        inline = value_obj.get("inline")
        if inline is not None:
            return inline
        ref_raw = value_obj.get("artifact_ref")
        if not isinstance(ref_raw, Mapping):
            return None
        artifact_id = ref_raw.get("id")
        if not isinstance(artifact_id, str) or not artifact_id:
            return None
        data = await self._artifacts.get(artifact_id)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return None

    def _payload_for_update(self, state: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if state is None:
            return None
        value_obj = state.get("value")
        if not isinstance(value_obj, Mapping):
            return None
        payload: dict[str, Any] = {"hash": value_obj.get("hash")}
        inline = value_obj.get("inline")
        if inline is not None:
            payload["inline"] = inline
        artifact_ref = value_obj.get("artifact_ref")
        if artifact_ref is not None:
            payload["artifact_ref"] = artifact_ref
        return payload

    def _checkpoint_content(
        self,
        *,
        kind: str,
        key: str,
        scope: KVScope,
        namespace: str,
        composite_key: str,
        prev: dict[str, Any] | None,
        next: dict[str, Any] | None,
        prev_etag_seen: Any | None,
        next_etag_written: str,
        expires_at: Any | None,
    ) -> dict[str, Any]:
        tenant_id = self._tool_context.get("tenant_id")
        user_id = self._tool_context.get("user_id")
        session_id = self._tool_context.get("session_id")
        task_id = self._tool_context.get("task_id")
        trace_id = self._tool_context.get("trace_id")
        tool_name = self._tool_context.get("_current_tool_name")
        tool_call_id = self._tool_context.get("_current_tool_call_id")
        context_version = self._tool_context.get("context_version")
        context_hash = self._tool_context.get("context_hash")
        content: dict[str, Any] = {
            "kind": kind,
            "scope": scope,
            "namespace": namespace,
            "key": key,
            "composite_key": composite_key,
            "prev": prev,
            "next": next,
            "prev_etag_seen": prev_etag_seen if isinstance(prev_etag_seen, str) else None,
            "next_etag_written": next_etag_written,
            "expires_at": expires_at if isinstance(expires_at, str) else None,
            "actor": {
                "tenant_id": tenant_id if isinstance(tenant_id, str) else None,
                "user_id": user_id if isinstance(user_id, str) else None,
                "session_id": session_id if isinstance(session_id, str) else None,
                "task_id": task_id if isinstance(task_id, str) else None,
                "trace_id": trace_id if isinstance(trace_id, str) else None,
                "tool_name": tool_name if isinstance(tool_name, str) else None,
                "tool_call_id": tool_call_id if isinstance(tool_call_id, str) else None,
            },
            "context": {
                "context_version": context_version if isinstance(context_version, int) else None,
                "context_hash": context_hash if isinstance(context_hash, str) else None,
            },
        }
        return content


__all__ = ["KVScope", "SessionKVConfig", "SessionKVFacade"]
