"""Tests for penguiflow.planner.payload_builders."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.models import ObservationGuardrailConfig, PlannerEvent
from penguiflow.planner.payload_builders import _clamp_observation


def _make_config(
    *, auto_artifact_threshold: int = 100, max_observation_chars: int = 1000
) -> ObservationGuardrailConfig:
    """Create an ObservationGuardrailConfig with low thresholds for testing."""
    return ObservationGuardrailConfig(
        auto_artifact_threshold=auto_artifact_threshold,
        max_observation_chars=max_observation_chars,
    )


def _noop_emit(event: PlannerEvent) -> None:
    pass


def _make_registry() -> ArtifactRegistry:
    """Create a mock ArtifactRegistry for testing."""
    return MagicMock(spec=ArtifactRegistry)


async def test_clamp_observation_stores_artifact_with_full_scope() -> None:
    """When observation exceeds threshold, stored artifact has the full scope."""
    store = InMemoryArtifactStore()
    scope = ArtifactScope(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
    )
    # Create an observation large enough to exceed max_observation_chars (1000)
    large_obs = {"data": "x" * 2000}
    config = _make_config(auto_artifact_threshold=100, max_observation_chars=1000)

    result, was_clamped = await _clamp_observation(
        observation=large_obs,
        spec_name="test_tool",
        trajectory_step=0,
        config=config,
        artifact_store=store,
        artifact_registry=_make_registry(),
        active_trajectory=None,
        emit_event=_noop_emit,
        time_source=time.monotonic,
        scope=scope,
    )

    assert was_clamped is True
    # Verify stored artifact has the correct scope
    refs = await store.list(scope=scope)
    assert len(refs) >= 1
    ref = refs[0]
    assert ref.scope is not None
    assert ref.scope.session_id == "sess-1"
    assert ref.scope.tenant_id == "tenant-1"
    assert ref.scope.user_id == "user-1"
    assert ref.scope.trace_id == "trace-1"


async def test_clamp_observation_stores_artifact_with_none_scope() -> None:
    """When scope=None (default), _clamp_observation still works (backwards-compatible)."""
    store = InMemoryArtifactStore()
    large_obs = {"data": "x" * 2000}
    config = _make_config(auto_artifact_threshold=100, max_observation_chars=1000)

    result, was_clamped = await _clamp_observation(
        observation=large_obs,
        spec_name="test_tool",
        trajectory_step=0,
        config=config,
        artifact_store=store,
        artifact_registry=_make_registry(),
        active_trajectory=None,
        emit_event=_noop_emit,
        time_source=time.monotonic,
        # scope defaults to None
    )

    assert was_clamped is True
    # Should not crash -- artifact stored without scope
    refs = await store.list()
    assert len(refs) >= 1
