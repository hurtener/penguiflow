"""Tests for penguiflow.planner.artifact_handling -- scope propagation."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from penguiflow.artifacts import (
    ArtifactScope,
    InMemoryArtifactStore,
    NoOpArtifactStore,
)
from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.models import PlannerEvent
from penguiflow.planner.trajectory import Trajectory


def _noop_emit(event: PlannerEvent) -> None:
    pass


def _make_proxy(tool_context: dict[str, Any]) -> _EventEmittingArtifactStoreProxy:
    traj = MagicMock(spec=Trajectory)
    traj.tool_context = tool_context
    registry = MagicMock(spec=ArtifactRegistry)
    return _EventEmittingArtifactStoreProxy(
        store=NoOpArtifactStore(),
        emit_event=_noop_emit,
        time_source=time.monotonic,
        trajectory=traj,
        registry=registry,
    )


def test_resolve_scope_includes_all_fields() -> None:
    """When tool_context has all four fields, _resolve_scope returns a full ArtifactScope."""
    proxy = _make_proxy({
        "session_id": "sess-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "trace_id": "trace-1",
    })
    scope = proxy._resolve_scope(None)
    assert scope is not None
    assert scope.session_id == "sess-1"
    assert scope.tenant_id == "tenant-1"
    assert scope.user_id == "user-1"
    assert scope.trace_id == "trace-1"


def test_resolve_scope_returns_none_without_session_id() -> None:
    """When tool_context has no session_id, _resolve_scope returns None."""
    proxy = _make_proxy({"tenant_id": "tenant-1"})
    scope = proxy._resolve_scope(None)
    assert scope is None


def test_resolve_scope_passes_through_explicit_scope() -> None:
    """When a non-None scope is passed, it is returned unchanged."""
    proxy = _make_proxy({"session_id": "sess-1", "tenant_id": "tenant-1"})
    explicit = ArtifactScope(session_id="explicit-sess")
    result = proxy._resolve_scope(explicit)
    assert result is explicit
    assert result.session_id == "explicit-sess"


# ---------------------------------------------------------------------------
# emit / register independence (Phase 001)
# ---------------------------------------------------------------------------


def _make_real_proxy() -> tuple[
    _EventEmittingArtifactStoreProxy,
    ArtifactRegistry,
    list[PlannerEvent],
]:
    """Build a proxy backed by real store/registry/trajectory plus an event sink."""
    events: list[PlannerEvent] = []
    traj = Trajectory(query="q", tool_context={"session_id": "sess-1"})
    registry = ArtifactRegistry()
    proxy = _EventEmittingArtifactStoreProxy(
        store=InMemoryArtifactStore(),
        emit_event=events.append,
        time_source=time.monotonic,
        trajectory=traj,
        namespace="test_ns",
        registry=registry,
    )
    return proxy, registry, events


async def test_put_bytes_defaults_register_and_emit() -> None:
    """Default flags (emit=True, register=True) keep today's behavior: index + event."""
    proxy, registry, events = _make_real_proxy()
    ref = await proxy.put_bytes(b"hello", mime_type="application/pdf")

    # Binary index record registered (matches today's unconditional behavior).
    assert ref.id in registry._binary_index
    assert len(registry._records) == 1
    # artifact_stored event fired.
    assert len(events) == 1
    assert events[0].event_type == "artifact_stored"
    assert events[0].extra["artifact_id"] == ref.id


async def test_put_text_defaults_register_and_emit() -> None:
    """put_text default flags also register and emit (parity with put_bytes)."""
    proxy, registry, events = _make_real_proxy()
    ref = await proxy.put_text("some text", mime_type="application/json")

    assert ref.id in registry._binary_index
    assert len(registry._records) == 1
    assert len(events) == 1
    assert events[0].event_type == "artifact_stored"


async def test_register_false_skips_index_but_still_emits() -> None:
    """register=False writes NO binary index record; the event still fires when emit=True."""
    proxy, registry, events = _make_real_proxy()
    ref = await proxy.put_text(
        "ui payload",
        mime_type="application/json",
        namespace="penguiflow_ui_component",
        register=False,
        emit=True,
    )

    # No phantom binary index record created.
    assert registry._binary_index == {}
    assert registry._records == []
    # Event still fires, carrying the namespace in extra["source"].
    assert len(events) == 1
    assert events[0].extra["artifact_id"] == ref.id
    assert events[0].extra["source"]["namespace"] == "penguiflow_ui_component"


async def test_emit_false_skips_event_but_still_registers() -> None:
    """emit=False emits NO event; the binary record is still created when register=True."""
    proxy, registry, events = _make_real_proxy()
    ref = await proxy.put_bytes(b"silent", mime_type="application/pdf", emit=False)

    # Index bookkeeping still happens.
    assert ref.id in registry._binary_index
    assert len(registry._records) == 1
    # No event emitted.
    assert events == []


async def test_register_false_emit_false_only_stores() -> None:
    """With both flags False, nothing is indexed and no event fires (pure store write)."""
    proxy, registry, events = _make_real_proxy()
    ref = await proxy.put_text(
        "quiet",
        namespace="penguiflow_ui_component",
        register=False,
        emit=False,
    )

    assert registry._binary_index == {}
    assert registry._records == []
    assert events == []
    # The store write itself still succeeded.
    assert ref.id
