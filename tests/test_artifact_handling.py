"""Tests for penguiflow.planner.artifact_handling -- scope propagation."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from penguiflow.artifacts import ArtifactScope, NoOpArtifactStore
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
