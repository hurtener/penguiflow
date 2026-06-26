from __future__ import annotations

import json
from typing import Any

import pytest

from penguiflow.artifacts import ArtifactRef
from penguiflow.planner.artifact_registry import (
    ArtifactRegistry,
    resolve_artifact_refs_async,
)


def test_registry_registers_tool_artifact_and_resolves() -> None:
    registry = ArtifactRegistry()
    payload = {
        "type": "echarts",
        "config": {"title": {"text": "Revenue"}, "series": [{"data": [1, 2, 3]}]},
        "title": "Revenue Trend",
        "chart_type": "line",
    }

    record = registry.register_tool_artifact(
        "gather_data_from_genie",
        "chart_artifacts",
        payload,
        step_index=1,
    )

    assert record.kind == "ui_component"
    assert record.component == "echarts"
    summaries = registry.list_records()
    assert summaries[0]["component"] == "echarts"

    resolved = registry.resolve_ref(record.ref, trajectory=None, session_id=None)
    assert resolved is not None
    assert resolved["component"] == "echarts"
    assert resolved["props"]["option"]["title"]["text"] == "Revenue"


def test_registry_registers_binary_artifact_and_resolves() -> None:
    registry = ArtifactRegistry()
    ref = ArtifactRef(
        id="tableau_abc123",
        mime_type="image/png",
        size_bytes=2048,
        filename="sales.png",
    )

    record = registry.register_binary_artifact(ref, source_tool="tableau", step_index=2)
    summaries = registry.list_records(kind="binary")
    assert summaries[0]["artifact_id"] == "tableau_abc123"

    resolved = registry.resolve_ref(record.ref, trajectory=None, session_id="sess-1")
    assert resolved is not None
    assert resolved["component"] == "image"
    assert resolved["props"]["src"] == "/artifacts/tableau_abc123?session_id=sess-1"


class _ScopedStoreStub:
    """Minimal stand-in for ScopedArtifacts: exposes only ``download`` (no ``get``)."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs
        self.downloaded: list[str] = []

    async def download(self, artifact_id: str) -> bytes | None:
        # Mirrors ScopedArtifacts.download: returns the stored bytes after a scope check.
        self.downloaded.append(artifact_id)
        return self._blobs.get(artifact_id)


async def test_resolve_ref_async_rehydrates_ui_component_from_store_after_snapshot() -> None:
    # Production shape (Phase 002): the full tool-payload dict is what gets stored as bytes.
    payload = {
        "id": "comp-1",
        "component": "echarts",
        "props": {"option": {"title": {"text": "Revenue"}}},
        "title": "Revenue Trend",
        "summary": "echarts artifact",
        "metadata": {},
    }
    artifact_id = "ui_component_xyz"
    store = _ScopedStoreStub({artifact_id: json.dumps(payload, default=str).encode("utf-8")})

    # Build the in-run registry, register the component, and stamp artifact_id (as Phase 002 does
    # post-store-write) BEFORE snapshotting so the id survives the snapshot/restore cycle.
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "render_component",
        "ui",
        payload,
        step_index=0,
    )
    record.artifact_id = artifact_id
    snapshot = registry.snapshot()

    # Guard Phase 002: the persisted snapshot MUST carry artifact_id, else resume rehydration
    # silently fails (no branch to take in resolve_ref_async).
    snapshot_record = snapshot["records"][0]
    assert snapshot_record["artifact_id"] == artifact_id

    # Resume: a fresh registry from the snapshot has the record but NOT the in-run _payloads cache.
    restored = ArtifactRegistry.from_snapshot(snapshot)
    ref = snapshot_record["ref"]
    assert ref not in restored._payloads

    resolved = await restored.resolve_ref_async(
        ref,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    assert resolved is not None
    assert resolved["component"] == "echarts"
    assert resolved["props"]["option"]["title"]["text"] == "Revenue"
    # Hydration ran through the scoped store's download (scope-checked path).
    assert store.downloaded == [artifact_id]
    # Hydrated payload is cached back into _payloads for subsequent resolves.
    assert restored._payloads[ref] is not None


async def test_resolve_ref_async_inline_mode_never_hits_store() -> None:
    # inline mode: artifact_id stays None, so the rehydration branch is never taken.
    payload = {
        "component": "echarts",
        "props": {"option": {"title": {"text": "Revenue"}}},
    }
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "render_component",
        "ui",
        payload,
        step_index=0,
    )
    assert record.artifact_id is None
    snapshot = registry.snapshot()

    # Resume without the in-run payload cache and without a usable trajectory.
    restored = ArtifactRegistry.from_snapshot(snapshot)
    ref = snapshot["records"][0]["ref"]
    store = _ScopedStoreStub({})

    resolved = await restored.resolve_ref_async(
        ref,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    # No artifact_id -> branch not taken -> early return None, store untouched.
    assert resolved is None
    assert store.downloaded == []


# ---------------------------------------------------------------------------
# Phase 005: cross-run (record-ABSENT) store-only resolution.
#
# These tests exercise resolve_ref_async's record-ABSENT branch
# (artifact_registry.py:382-394): the ref the LLM was handed by list_artifacts
# in a *separate* planner.run() is the opaque store id, and NO ArtifactRecord
# exists in the fresh registry. Resolution must fall back to the store, gated to
# the ``penguiflow_ui_component`` namespace via a scope-checked ``get_metadata``.
#
# A separate, get_metadata-AWARE stub is used here on purpose: the Phase 004
# ``_ScopedStoreStub`` above is intentionally get_metadata-less and is left
# untouched so its record-present tests keep their contract.
# ---------------------------------------------------------------------------

UI_COMPONENT_NAMESPACE = "penguiflow_ui_component"


class _MetadataStoreStub:
    """ScopedArtifacts-like stub exposing both ``get_metadata`` and ``download``.

    Mirrors ``ScopedArtifacts``: ``get_metadata`` returns a scope-checked
    ``ArtifactRef`` (or ``None`` for out-of-scope / unknown ids), and
    ``download`` returns the stored bytes. Both calls are recorded (in order via
    ``calls``) so tests can assert the gate runs *before* any byte fetch.
    """

    def __init__(
        self,
        *,
        blobs: dict[str, bytes] | None = None,
        metas: dict[str, ArtifactRef] | None = None,
    ) -> None:
        self._blobs = blobs or {}
        self._metas = metas or {}
        self.metadata_calls: list[str] = []
        self.downloaded: list[str] = []
        self.calls: list[str] = []

    async def get_metadata(self, artifact_id: str) -> ArtifactRef | None:
        # Mirrors ScopedArtifacts.get_metadata: returns the scope-checked ref or
        # None when the id is out of scope / unknown.
        self.metadata_calls.append(artifact_id)
        self.calls.append(f"get_metadata:{artifact_id}")
        return self._metas.get(artifact_id)

    async def download(self, artifact_id: str) -> bytes | None:
        self.downloaded.append(artifact_id)
        self.calls.append(f"download:{artifact_id}")
        return self._blobs.get(artifact_id)


def _ui_component_payload() -> dict[str, Any]:
    """Full Phase 002 tool-payload dict (what gets stored as JSON bytes)."""
    return {
        "id": "comp-cross-run",
        "component": "echarts",
        "props": {"option": {"title": {"text": "Cross-run Revenue"}}},
        "title": "Cross-run Revenue Trend",
        "summary": "echarts artifact",
        "metadata": {},
    }


def _meta_ref(artifact_id: str, *, namespace: str | None) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        mime_type="application/json",
        namespace=namespace,
    )


async def test_resolve_ref_async_cross_run_hydrates_from_store() -> None:
    """Issue 1: FRESH registry, no record -> store fallback hydrates the component.

    Genuinely enters the record-ABSENT branch: the id is absent from
    ``_records_by_ref``, ``get_metadata`` is consulted first, the namespace gate
    passes, then ``download`` fetches the bytes.
    """
    artifact_id = "penguiflow_ui_component_abc123"
    payload = _ui_component_payload()
    store = _MetadataStoreStub(
        blobs={artifact_id: json.dumps(payload, default=str).encode("utf-8")},
        metas={artifact_id: _meta_ref(artifact_id, namespace=UI_COMPONENT_NAMESPACE)},
    )

    # Fresh registry from a separate planner.run(): no record, no payload cache.
    registry = ArtifactRegistry()
    assert artifact_id not in registry._records_by_ref

    resolved = await registry.resolve_ref_async(
        artifact_id,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    # Hydrated to a real component payload (not None).
    assert resolved is not None
    assert resolved["component"] == "echarts"
    assert resolved["props"]["option"]["title"]["text"] == "Cross-run Revenue"
    # The branch was actually taken: get_metadata ran BEFORE download.
    assert store.metadata_calls == [artifact_id]
    assert store.downloaded == [artifact_id]
    assert store.calls == [f"get_metadata:{artifact_id}", f"download:{artifact_id}"]
    # Record-ABSENT path: nothing was synthesized into the record cache.
    assert artifact_id not in registry._records_by_ref


async def test_resolve_ref_async_cross_run_namespace_gate_refuses() -> None:
    """Issue 2: component-shaped bytes under a DIFFERENT namespace are refused.

    The namespace gate must short-circuit to None *before* any byte download.
    """
    artifact_id = "some_other_namespace_def456"
    payload = _ui_component_payload()  # parses as a component, but wrong namespace
    store = _MetadataStoreStub(
        blobs={artifact_id: json.dumps(payload, default=str).encode("utf-8")},
        metas={artifact_id: _meta_ref(artifact_id, namespace="some_other_namespace")},
    )

    registry = ArtifactRegistry()
    assert artifact_id not in registry._records_by_ref

    resolved = await registry.resolve_ref_async(
        artifact_id,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    # Wrong namespace -> None, and the gate fired before any byte fetch.
    assert resolved is None
    assert store.metadata_calls == [artifact_id]
    assert store.downloaded == []


async def test_resolve_ref_async_cross_run_out_of_scope_refuses() -> None:
    """Issue 3: out-of-scope id -> get_metadata returns None -> None, no download.

    Mirrors ScopedArtifacts.get_metadata returning None for an out-of-scope ref.
    """
    artifact_id = "penguiflow_ui_component_ghi789"
    payload = _ui_component_payload()
    store = _MetadataStoreStub(
        blobs={artifact_id: json.dumps(payload, default=str).encode("utf-8")},
        metas={},  # get_metadata returns None (out of scope)
    )

    registry = ArtifactRegistry()
    assert artifact_id not in registry._records_by_ref

    resolved = await registry.resolve_ref_async(
        artifact_id,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    assert resolved is None
    assert store.metadata_calls == [artifact_id]
    assert store.downloaded == []


async def test_resolve_artifact_refs_async_bogus_id_raises() -> None:
    """Issue 4: a bogus id (no record, get_metadata -> None) raises loudly.

    The store-backed record-ABSENT miss must surface as
    RuntimeError("Unknown artifact_ref ...") via resolve_artifact_refs_async,
    never a silent wrong answer.
    """
    store = _MetadataStoreStub(metas={})  # get_metadata returns None for anything

    registry = ArtifactRegistry()
    assert "bogus_id" not in registry._records_by_ref

    with pytest.raises(RuntimeError, match="Unknown artifact_ref"):
        await resolve_artifact_refs_async(
            {"artifact_ref": "bogus_id"},
            registry=registry,
            trajectory=None,
            session_id="sess-1",
            artifact_store=store,
        )

    # The store was consulted (genuinely entered the record-ABSENT branch),
    # but nothing was downloaded.
    assert store.metadata_calls == ["bogus_id"]
    assert store.downloaded == []


async def test_resolve_ref_async_cross_run_inline_noop_store_is_inert() -> None:
    """Issue 5: record-ABSENT branch with a store lacking get_metadata is inert.

    With a store that exposes only ``download`` (no ``get_metadata``), the
    record-ABSENT branch sets ``meta_ref = None`` and returns None without
    downloading. Uses the Phase 004 get_metadata-less stub on purpose.
    """
    artifact_id = "penguiflow_ui_component_jkl012"
    payload = _ui_component_payload()
    store = _ScopedStoreStub({artifact_id: json.dumps(payload, default=str).encode("utf-8")})

    registry = ArtifactRegistry()
    assert artifact_id not in registry._records_by_ref
    # Confirm the store really has no get_metadata (so callable(get_meta) is False).
    assert getattr(store, "get_metadata", None) is None

    resolved = await registry.resolve_ref_async(
        artifact_id,
        trajectory=None,
        session_id="sess-1",
        artifact_store=store,
    )

    assert resolved is None
    assert store.downloaded == []
