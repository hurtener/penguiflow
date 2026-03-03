# Phase 000: Upstream scope fix -- artifact_handling and payload_builders

## Objective
Fix scope propagation in two planner-internal modules so that artifacts stored via the `_EventEmittingArtifactStoreProxy` and the `_clamp_observation` guardrail include the full scope (`session_id`, `tenant_id`, `user_id`, `trace_id`) rather than only `session_id` or no scope at all. This is prerequisite to the hydration feature: `artifact_store.list(scope=...)` only returns results when the stored artifacts have matching scope fields.

## Tasks
1. Modify `_resolve_scope` in `artifact_handling.py` to inject all four scope fields from `tool_context` (plan section 0a)
2. Add `scope` parameter to `_clamp_observation` in `payload_builders.py` and pass it to `put_text` (plan section 0c)
3. Update `_clamp_observation` wrapper in `react.py` to build the full scope from the active trajectory and pass it through (plan section 0c)
4. Create `tests/test_artifact_handling.py` with scope propagation tests
5. Create `tests/test_payload_builders.py` with scope propagation tests

## Detailed Steps

### Step 1: Modify `_resolve_scope` in `artifact_handling.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/artifact_handling.py`
- Find the `_resolve_scope` method at lines 47-57 of the `_EventEmittingArtifactStoreProxy` class
- Replace the method body so it returns an `ArtifactScope` with all four fields (`session_id`, `tenant_id`, `user_id`, `trace_id`) instead of only `session_id`
- Update the docstring from `"Inject session_id from trajectory if scope is missing."` to `"Inject scope fields from trajectory if scope is missing."`
- No import changes needed -- `ArtifactScope` is already imported at line 13

### Step 2: Add `scope` parameter to `_clamp_observation` in `payload_builders.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/payload_builders.py`
- Add `from ..artifacts import ArtifactScope` import -- the existing import at line 12 is `from ..artifacts import ArtifactStore`, so **change line 12** to `from ..artifacts import ArtifactScope, ArtifactStore`
- Find the `_clamp_observation` function signature at line 119-130
- Add `scope: ArtifactScope | None = None,` as the last keyword parameter (before the return type annotation)
- Find the `artifact_store.put_text(` call at line 152-155 and add `scope=scope,` to its keyword arguments

### Step 3: Update `_clamp_observation` wrapper in `react.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react.py`
- Find the import at line 13: `from ..artifacts import ArtifactStore` -- change to `from ..artifacts import ArtifactScope, ArtifactStore`
- Find the `_clamp_observation` wrapper method at line 1090-1106
- Replace the method body to: (a) build an `ArtifactScope` from `self._active_trajectory.tool_context` with all four fields, (b) pass `scope=scope` to the `_clamp_observation_impl` call

### Step 4: Create `tests/test_artifact_handling.py`
- Create new file `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_artifact_handling.py`
- Use the scaffold from the plan (imports, `_noop_emit`, `_make_proxy` helper)
- Write test: `test_resolve_scope_includes_all_fields` -- when `tool_context` has `session_id`, `tenant_id`, `user_id`, and `trace_id`, `_resolve_scope(None)` returns an `ArtifactScope` with all four fields populated
- Write test: `test_resolve_scope_returns_none_without_session_id` -- when `tool_context` has no `session_id`, returns `None`
- Write test: `test_resolve_scope_passes_through_explicit_scope` -- when a non-None scope is passed, it is returned unchanged

### Step 5: Create `tests/test_payload_builders.py`
- Create new file `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_payload_builders.py`
- Use the scaffold from the plan (imports, `_make_config`, `_noop_emit`, `_make_registry`)
- Write test: `test_clamp_observation_stores_artifact_with_full_scope` -- call `_clamp_observation` with `InMemoryArtifactStore`, a large observation exceeding `auto_artifact_threshold`, and a scope with all four fields; verify via `artifact_store.list()` that the stored artifact has correct scope
- Write test: `test_clamp_observation_stores_artifact_with_none_scope` -- call with `scope=None` and verify no crash (backwards-compatible)

## Required Code

```python
# Target file: penguiflow/planner/artifact_handling.py
# Replace the _resolve_scope method (lines 47-57) with:

    def _resolve_scope(self, scope: ArtifactScope | None) -> ArtifactScope | None:
        """Inject scope fields from trajectory if scope is missing."""
        if scope is not None:
            return scope
        tool_ctx = self._trajectory.tool_context
        if tool_ctx and isinstance(tool_ctx, dict):
            session_id = tool_ctx.get("session_id")
            if session_id:
                return ArtifactScope(
                    session_id=str(session_id),
                    tenant_id=tool_ctx.get("tenant_id"),
                    user_id=tool_ctx.get("user_id"),
                    trace_id=tool_ctx.get("trace_id"),
                )
        return None
```

```python
# Target file: penguiflow/planner/payload_builders.py
# Change line 12 from:
#   from ..artifacts import ArtifactStore
# To:
from ..artifacts import ArtifactScope, ArtifactStore
```

```python
# Target file: penguiflow/planner/payload_builders.py
# Replace the _clamp_observation signature (lines 119-130) with:

async def _clamp_observation(
    *,
    observation: dict[str, Any],
    spec_name: str,
    trajectory_step: int,
    config: ObservationGuardrailConfig,
    artifact_store: ArtifactStore,
    artifact_registry: ArtifactRegistry,
    active_trajectory: Trajectory | None,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    scope: ArtifactScope | None = None,
) -> tuple[dict[str, Any], bool]:
```

```python
# Target file: penguiflow/planner/payload_builders.py
# Replace the put_text call (lines 152-155) with:

            ref = await artifact_store.put_text(
                serialized,
                namespace=f"observation.{spec_name}",
                scope=scope,
            )
```

```python
# Target file: penguiflow/planner/react.py
# Change line 13 from:
#   from ..artifacts import ArtifactStore
# To:
from ..artifacts import ArtifactScope, ArtifactStore
```

```python
# Target file: penguiflow/planner/react.py
# Replace the _clamp_observation wrapper method (lines 1090-1106) with:

    async def _clamp_observation(
        self,
        observation: dict[str, Any],
        spec_name: str,
        trajectory_step: int,
    ) -> tuple[dict[str, Any], bool]:
        scope: ArtifactScope | None = None
        traj = self._active_trajectory
        if traj is not None:
            tool_ctx = traj.tool_context
            if tool_ctx and isinstance(tool_ctx, dict):
                session_id = tool_ctx.get("session_id")
                if session_id:
                    scope = ArtifactScope(
                        session_id=str(session_id),
                        tenant_id=tool_ctx.get("tenant_id"),
                        user_id=tool_ctx.get("user_id"),
                        trace_id=tool_ctx.get("trace_id"),
                    )
        return await _clamp_observation_impl(
            observation=observation,
            spec_name=spec_name,
            trajectory_step=trajectory_step,
            config=self._observation_guardrail,
            artifact_store=self._artifact_store,
            artifact_registry=self._artifact_registry,
            active_trajectory=self._active_trajectory,
            emit_event=self._emit_event,
            time_source=self._time_source,
            scope=scope,
        )
```

```python
# Target file: tests/test_artifact_handling.py
# Create new file with:

"""Tests for penguiflow.planner.artifact_handling -- scope propagation."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

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
```

```python
# Target file: tests/test_payload_builders.py
# Create new file with:

"""Tests for penguiflow.planner.payload_builders."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.models import ObservationGuardrailConfig, PlannerEvent
from penguiflow.planner.payload_builders import _clamp_observation


def _make_config(*, auto_artifact_threshold: int = 100) -> ObservationGuardrailConfig:
    """Create an ObservationGuardrailConfig with a low threshold for testing."""
    return ObservationGuardrailConfig(auto_artifact_threshold=auto_artifact_threshold)


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
    # Create an observation large enough to exceed the threshold
    large_obs = {"data": "x" * 200}
    config = _make_config(auto_artifact_threshold=100)

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
    large_obs = {"data": "x" * 200}
    config = _make_config(auto_artifact_threshold=100)

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
```

## Exit Criteria (Success)
- [ ] `penguiflow/planner/artifact_handling.py` `_resolve_scope` method returns `ArtifactScope` with `session_id`, `tenant_id`, `user_id`, and `trace_id` when `tool_context` provides them
- [ ] `penguiflow/planner/payload_builders.py` imports `ArtifactScope` and `_clamp_observation` accepts a `scope` keyword parameter
- [ ] `penguiflow/planner/payload_builders.py` `put_text` call passes `scope=scope`
- [ ] `penguiflow/planner/react.py` imports `ArtifactScope` and the wrapper method builds and passes the full scope
- [ ] `tests/test_artifact_handling.py` exists with passing tests
- [ ] `tests/test_payload_builders.py` exists with passing tests
- [ ] No ruff lint errors in modified files
- [ ] No mypy type errors in modified files

## Implementation Notes
- The `_resolve_scope` method is on the `_EventEmittingArtifactStoreProxy` class (line 22 of `artifact_handling.py`). This proxy wraps the raw `ArtifactStore` and is used by all `ToolNode` call sites and `ResourceCache`. Fixing `_resolve_scope` propagates scope to all 8 `ctx._artifacts` call sites in `tools/node.py` and the 2 in `tools/resources.py`.
- In `payload_builders.py`, the `_clamp_observation` function is imported in `react.py` with the alias `_clamp_observation_impl` (see line 1096 of `react.py`). The wrapper method in `ReactPlanner` delegates to it. The function name `_clamp_observation` is used in `payload_builders.py`, while `react.py` calls it as `_clamp_observation_impl`.
- The `scope` parameter in `_clamp_observation` defaults to `None` for backwards compatibility.
- `trace_id` is included in the planner-internal scope (it comes from `tool_context`) but is intentionally omitted from HTTP endpoints in later phases.
- `InMemoryArtifactStore` from `penguiflow.artifacts` is the appropriate store for testing -- it stores artifacts in memory and supports `list()` with scope filtering.
- `ObservationGuardrailConfig` controls thresholds; set `auto_artifact_threshold` low (e.g., 100) in tests so small observations trigger artifact storage.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_artifact_handling.py -k "resolve_scope" -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_payload_builders.py -k "clamp_observation" -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/planner/artifact_handling.py penguiflow/planner/payload_builders.py penguiflow/planner/react.py tests/test_artifact_handling.py tests/test_payload_builders.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy penguiflow/planner/artifact_handling.py penguiflow/planner/payload_builders.py penguiflow/planner/react.py
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-03

### Summary of Changes
- **`penguiflow/planner/artifact_handling.py`**: Updated `_resolve_scope` method on `_EventEmittingArtifactStoreProxy` to return an `ArtifactScope` with all four fields (`session_id`, `tenant_id`, `user_id`, `trace_id`) from `tool_context`, instead of only `session_id`. Updated the docstring accordingly.
- **`penguiflow/planner/payload_builders.py`**: Added `ArtifactScope` to the import from `..artifacts`. Added `scope: ArtifactScope | None = None` parameter to `_clamp_observation` function signature. Passed `scope=scope` to the `artifact_store.put_text()` call inside the artifact storage branch.
- **`penguiflow/planner/react.py`**: Added `ArtifactScope` to the import from `..artifacts`. Updated the `_clamp_observation` wrapper method in `ReactPlanner` to build a full `ArtifactScope` from `self._active_trajectory.tool_context` and pass it as `scope=scope` to `_clamp_observation_impl`.
- **`tests/test_artifact_handling.py`**: Created new test file with three tests: `test_resolve_scope_includes_all_fields`, `test_resolve_scope_returns_none_without_session_id`, `test_resolve_scope_passes_through_explicit_scope`.
- **`tests/test_payload_builders.py`**: Created new test file with two tests: `test_clamp_observation_stores_artifact_with_full_scope`, `test_clamp_observation_stores_artifact_with_none_scope`.

### Key Considerations
- The `_resolve_scope` change is minimal and surgical -- only the `ArtifactScope` constructor call was expanded. The control flow (check `scope is not None`, check `tool_ctx` is dict, check `session_id` is truthy) remains identical to the original.
- The `scope` parameter in `_clamp_observation` defaults to `None` for full backwards compatibility. All existing callers that do not pass `scope` continue to work unchanged.
- The `react.py` wrapper method builds the scope from `self._active_trajectory.tool_context` using the same pattern as `_resolve_scope` in `artifact_handling.py`. This is intentional duplication per the plan -- the two code paths serve different artifact storage entry points (proxy store vs. observation clamping).

### Assumptions
- The plan's test scaffold specified `{"data": "x" * 200}` as the large observation, but this only produces ~215 chars of JSON, well under the `max_observation_chars` minimum of 1000 (enforced by Pydantic `ge=1000`). The observation would hit the fast-path return and never reach the artifact storage code. I adjusted the tests to use `"x" * 2000` and explicitly set `max_observation_chars=1000` so the observation exceeds the limit and triggers artifact storage.
- The unused `pytest` and `typing.Any` imports in the plan's test scaffolds were removed to satisfy ruff F401 lint rules.

### Deviations from Plan
- **Test observation size**: Changed from `"x" * 200` to `"x" * 2000` and added `max_observation_chars=1000` to `_make_config`. Without this change, the observation would never exceed `max_observation_chars` (minimum 1000, default 50,000) and the tests would always fail because `_clamp_observation` returns `(observation, False)` on the fast path.
- **Removed unused imports in tests**: Removed `import pytest` from `test_artifact_handling.py` and removed `from typing import Any` and `import pytest` from `test_payload_builders.py` to pass ruff F401 checks.

### Potential Risks & Reviewer Attention Points
- The `_resolve_scope` method now reads `tenant_id`, `user_id`, and `trace_id` from `tool_context` via `.get()` which returns `None` if the key is missing. These `None` values are passed to `ArtifactScope` where all four fields default to `None`. This means partial scope is possible (e.g., `session_id` + `tenant_id` but no `user_id`). This is consistent with the `ArtifactScope` model design.
- The `react.py` wrapper duplicates the scope-building logic from `_resolve_scope`. If the logic needs to change in the future, both places must be updated. The plan explicitly calls for this pattern.
- The `InMemoryArtifactStore.list(scope=...)` filtering behavior is relied upon in the payload_builders test. If the in-memory store's scope matching semantics change, the test may need updating.

### Files Modified
- `penguiflow/planner/artifact_handling.py` (modified)
- `penguiflow/planner/payload_builders.py` (modified)
- `penguiflow/planner/react.py` (modified)
- `tests/test_artifact_handling.py` (created)
- `tests/test_payload_builders.py` (created)
