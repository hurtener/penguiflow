# Phase 002: Playground Wrapper Cleanup -- Remove Trajectory Saves, Simplify EventRecorder, Remove state_store

## Objective
Remove redundant trajectory persistence and event persistence from the playground wrapper now that the planner handles both internally (Phases 0-1). This involves deleting `_build_trajectory()`, simplifying `_EventRecorder` to forward-only, removing `persist()` calls, removing the `state_store` parameter from both wrapper constructors, and updating the two caller sites in `playground.py`.

## Tasks
1. Simplify `_EventRecorder` class (remove `__init__`, remove `persist()`, simplify `callback()`).
2. Remove `_build_trajectory()` helper function.
3. Remove trajectory save blocks from `PlannerAgentWrapper.chat()`, `PlannerAgentWrapper.resume()`, and `OrchestratorAgentWrapper.chat()`.
4. Remove `persist()` calls from all four wrapper methods.
5. Remove `state_store` parameter from both wrapper constructors and update `_event_recorder` initialization.
6. Remove `has_store` from log statements.
7. Remove unused imports (`PlaygroundStateStore`, `Trajectory`, `dataclass`).
8. Update caller sites in `playground.py`.

## Detailed Steps

### Step 1: Simplify `_EventRecorder` class in `playground_wrapper.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_wrapper.py`.
- Replace the entire `_EventRecorder` class (currently lines 72-105) with the simplified version. The new class has:
  - No `__init__` method (no `self._state_store`, no `self._buffer`).
  - A simplified `callback()` that returns `None` when `event_consumer is None` (regardless of state_store), and only forwards events to `event_consumer` when provided.
  - No `persist()` method.
- **Behavioral change:** The old `callback()` returned a closure even when `event_consumer` was `None` if `state_store` was set. The new one returns `None` when `event_consumer is None`. This is correct because the planner now handles buffering and persistence internally.

### Step 2: Remove `_build_trajectory()` helper
- Delete the entire `_build_trajectory()` function (currently lines 174-211). Verified: it is only called within `playground_wrapper.py` (lines 308, 406, 586) and its test file. No other callers exist.

### Step 3: Remove trajectory save blocks from three locations
- **`PlannerAgentWrapper.chat()`** (currently lines 308-313): Remove the `_build_trajectory()` call and the `if trajectory is not None and self._state_store is not None:` save block and the `elif trajectory is None:` warning log. Keep everything else (answer extraction, ChatResult construction).
- **`PlannerAgentWrapper.resume()`** (currently lines 405-411): Same removal pattern. Remove the `# Save trajectory (same as chat method)` comment and the `_build_trajectory()` + save block.
- **`OrchestratorAgentWrapper.chat()`** (currently lines 586-591): Same removal pattern.
- Note: `OrchestratorAgentWrapper.resume()` does NOT currently save trajectories -- nothing to remove there.

### Step 4: Remove `persist()` calls from four locations
- **`PlannerAgentWrapper.chat()`** (currently line 282): Remove `await self._event_recorder.persist(trace_id)`.
- **`PlannerAgentWrapper.resume()`** (currently line 378): Same.
- **`OrchestratorAgentWrapper.chat()`** (currently line 576): Same.
- **`OrchestratorAgentWrapper.resume()`** (currently line 687): Same.

### Step 5: Remove `state_store` from wrapper constructors
- **`PlannerAgentWrapper.__init__`** (currently line 221): Remove `state_store: PlaygroundStateStore | None = None` parameter and `self._state_store = state_store` assignment (line 225). Change `self._event_recorder = _EventRecorder(state_store)` to `self._event_recorder = _EventRecorder()`.
- **`OrchestratorAgentWrapper.__init__`** (currently line 432): Remove `state_store: PlaygroundStateStore | None = None` parameter and `self._state_store = state_store` assignment (line 438). Change `self._event_recorder = _EventRecorder(state_store)` to `self._event_recorder = _EventRecorder()`.

### Step 6: Remove `has_store` from log statements
- **`PlannerAgentWrapper.chat()`** log (currently lines ~301-306): Remove `, has_store=%s` from the format string and remove the corresponding `self._state_store is not None` argument. The log still keeps trace_id, session_id, metadata_keys, has_steps.
- **`PlannerAgentWrapper.resume()`** log (currently lines ~397-402): Same removal.
- **`OrchestratorAgentWrapper.chat()`** log (currently lines ~579-584, uses `_LOGGER.debug` not `info`): Same removal.

### Step 7: Remove unused imports
- Remove `Trajectory` from the `from penguiflow.planner import (...)` block (currently line 20). After deleting `_build_trajectory()`, `Trajectory` is unused. The remaining imports (`PlannerEvent`, `PlannerEventCallback`, `PlannerFinish`, `PlannerPause`, `ReactPlanner`) are still used.
- Remove `from .playground_state import PlaygroundStateStore` (currently line 24). After removing `state_store` from constructors and `_EventRecorder.__init__`, `PlaygroundStateStore` is unused.
- Remove `from dataclasses import dataclass` (currently line 11). After removing `_EventRecorder.__init__` (which was NOT a dataclass, but this import was used by something else -- **actually check**: the `@dataclass` decorator on `ChatResult` at line 27). **WAIT -- `ChatResult` at line 28 uses `@dataclass`. Do NOT remove the `dataclass` import.** Only remove it if `ChatResult` is NOT a dataclass. Verify before removing.

### Step 8: Update caller sites in `playground.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground.py`.
- Currently line 786: `OrchestratorAgentWrapper(orchestrator, state_store=state_store,)` -- remove `state_store=state_store,`.
- Currently line 793: `PlannerAgentWrapper(planner, state_store=state_store,)` -- remove `state_store=state_store,`.

## Required Code

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- Simplified _EventRecorder class (replaces entire old class) ---
class _EventRecorder:
    """Creates a planner event callback that forwards events to an optional consumer."""

    def callback(
        self,
        *,
        trace_id_supplier: Callable[[], str | None] | None = None,
        event_consumer: Callable[[PlannerEvent, str | None], None] | None = None,
    ) -> PlannerEventCallback | None:
        if event_consumer is None:
            return None

        def _record(event: PlannerEvent) -> None:
            trace_id = trace_id_supplier() if trace_id_supplier else None
            event_consumer(event, trace_id)

        return _record
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- PlannerAgentWrapper.__init__ after cleanup ---
    def __init__(
        self,
        planner: ReactPlanner,
        *,
        tool_context_defaults: Mapping[str, Any] | None = None,
    ) -> None:
        self._planner = planner
        self._event_recorder = _EventRecorder()
        self._tool_context_defaults = dict(tool_context_defaults or {})
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.__init__ after cleanup ---
    def __init__(
        self,
        orchestrator: Any,
        *,
        tenant_id: str = "playground-tenant",
        user_id: str = "playground-user",
        tool_context_defaults: Mapping[str, Any] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._tool_context_defaults = dict(tool_context_defaults or {})
        self._event_recorder = _EventRecorder()
        self._initialized = False
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- PlannerAgentWrapper.chat() log statement after cleanup (remove has_store) ---
        _LOGGER.info(
            "chat complete: trace_id=%s, session_id=%s, metadata_keys=%s, has_steps=%s",
            trace_id, session_id,
            list(metadata.keys()) if metadata else None,
            bool(metadata and isinstance(metadata.get("steps"), list)),
        )
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- PlannerAgentWrapper.resume() log statement after cleanup ---
        _LOGGER.info(
            "resume complete: trace_id=%s, session_id=%s, metadata_keys=%s, has_steps=%s",
            trace_id, session_id,
            list(metadata.keys()) if metadata else None,
            bool(metadata and isinstance(metadata.get("steps"), list)),
        )
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.chat() log statement after cleanup ---
        _LOGGER.debug(
            "orchestrator chat complete: trace_id=%s, session_id=%s, metadata_keys=%s, has_steps=%s",
            trace_id, session_id,
            list(metadata.keys()) if metadata else None,
            bool(metadata and isinstance(metadata.get("steps"), list)),
        )
```

```python
# Target file: penguiflow/cli/playground.py
# --- Wrapper construction without state_store ---
        wrapper: AgentWrapper = OrchestratorAgentWrapper(
            orchestrator,
        )
    # ...
        wrapper = PlannerAgentWrapper(
            planner,
        )
```

## Exit Criteria (Success)
- [ ] `_EventRecorder` in `playground_wrapper.py` has no `__init__` method, no `_buffer` attribute, no `persist()` method. Only has `callback()` that returns `None` when `event_consumer is None`.
- [ ] `_build_trajectory()` function is completely removed from `playground_wrapper.py`.
- [ ] No `save_trajectory` calls remain in `playground_wrapper.py` wrapper methods.
- [ ] No `await self._event_recorder.persist(...)` calls remain in any wrapper method.
- [ ] Neither `PlannerAgentWrapper` nor `OrchestratorAgentWrapper` constructors accept a `state_store` parameter.
- [ ] Neither wrapper class has a `self._state_store` attribute.
- [ ] `Trajectory` is no longer imported in `playground_wrapper.py`.
- [ ] `PlaygroundStateStore` is no longer imported in `playground_wrapper.py`.
- [ ] `playground.py` wrapper construction calls do not pass `state_store`.
- [ ] Log statements no longer reference `has_store` or `self._state_store`.
- [ ] `uv run ruff check penguiflow/cli/playground_wrapper.py penguiflow/cli/playground.py` reports zero errors.
- [ ] `uv run mypy` reports zero new type errors.

## Implementation Notes
- **Retained helpers -- DO NOT remove:** `_combine_callbacks`, `_normalise_answer`, `_normalise_metadata`, `_extract_from_dict`, `_get_attr`, `_planner_trace_id`. These are all still used by wrapper methods.
- **Edit order:** Use string matching (the Edit tool's `old_string` parameter) rather than line numbers, since removals shift subsequent line numbers. Line numbers in this document are for locating context only.
- **`dataclass` import:** `ChatResult` at line 28 is decorated with `@dataclass`. Do NOT remove the `from dataclasses import dataclass` import -- it is still used.
- **`OrchestratorAgentWrapper.resume()` does NOT currently save trajectories** -- there is nothing to remove there for trajectory saves (only the `persist()` call).
- The `PlannerAgentWrapper` already injects `trace_id` + `session_id` into `merged_tool_context` before calling `planner.run()` / `planner.resume()` -- no changes needed there.
- `playground.py` lines 782 and 809 also pass `state_store=state_store` to `_call_orchestrator_builder` and `_call_builder` respectively -- these are NOT wrapper constructors and must NOT be changed.
- Depends on Phase 1 (planner must already handle persistence before wrapper stops doing it).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/cli/playground_wrapper.py penguiflow/cli/playground.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy
# Note: some tests may fail at this point because test files still pass state_store= to wrapper constructors.
# Those are fixed in Phase 6. Run targeted tests that don't use state_store on wrappers:
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/cli/test_playground_wrapper_helpers.py::test_combine_callbacks tests/cli/test_playground_wrapper_helpers.py::test_normalise_metadata tests/cli/test_playground_wrapper_helpers.py::test_normalise_answer tests/cli/test_playground_wrapper_helpers.py::test_extract_from_dict -x -q
```
