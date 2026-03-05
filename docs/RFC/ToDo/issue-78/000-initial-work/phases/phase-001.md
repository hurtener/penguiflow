# Phase 001: Planner Core -- Add Persistence Helpers and Wire into run()/resume()

## Objective
Add three persistence helper functions to `react_runtime.py` (`_persist_trajectory`, `_persist_events`, `_fire_persistence_tasks`) and modify the `run()` and `resume()` functions to call `_fire_persistence_tasks` from their `finally` blocks. After this phase, the planner automatically persists trajectories and events as fire-and-forget background tasks on every exit path (finish, pause, error, cancel).

## Tasks
1. Add `import asyncio` to `react_runtime.py`.
2. Add the three persistence helper functions before the `run()` function definition.
3. Modify `run()` to define `result = None` before the `try` and call `_fire_persistence_tasks` in `finally`.
4. Modify `resume()` with the same pattern.

## Detailed Steps

### Step 1: Add `import asyncio`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react_runtime.py`.
- The file currently does NOT import `asyncio`. Add `import asyncio` **before** `import hashlib` (currently line 5) to maintain alphabetical ordering required by ruff/isort.
- The file starts with `from __future__ import annotations` (line 3), then a blank line, then `import hashlib` (line 5). Insert `import asyncio` between the blank line and `import hashlib`.

### Step 2: Add persistence helper functions
- The file already defines `logger = logging.getLogger("penguiflow.planner")` at line 45. Do NOT add another logger definition -- the helpers use the existing module-level `logger`.
- The helpers use `PlannerEvent` (already imported at line 29), `PlannerFinish` (line 30), `PlannerPause` (line 31), `Trajectory` (line 42), and `Any` from `typing` (line 13). All are already present. No additional imports needed beyond `asyncio`.
- Place the three helper functions immediately **before** the `run()` function definition (currently at line 876). There is a blank line after the `return True` statement of the preceding function -- insert the helpers there.

### Step 3: Modify `run()` function
- In the `run()` function (currently starting at line 876), locate the `try/finally` block around `run_loop()` (currently lines 922-944).
- **Before** the `try` statement (after `_init_tool_activation_state` at line 921), add: `result: PlannerFinish | PlannerPause | None = None`
- Inside the `try` block, find `result = await run_loop(...)` (currently line 942). This line stays unchanged.
- In the existing `finally` block, **after** `planner._active_tool_names = previous_active` (currently line 944), add: `_fire_persistence_tasks(planner, trajectory, result)`
- **After** the `finally` block, add: `assert result is not None` (if `run_loop()` raised, the exception already propagated before reaching this line -- standard Python behavior).
- The existing line `await planner._maybe_record_memory_turn(query, result, trajectory, resolved_key)` (currently line 945) and `return result` (line 946) stay unchanged after the assert.

### Step 4: Modify `resume()` function
- Same pattern as `run()`. In the `resume()` function (currently starting at line 949), locate the `try/finally` block around `run_loop()` (currently lines 1008-1030).
- **Before** the `try` statement (after `_init_tool_activation_state` at line 1007), add: `result: PlannerFinish | PlannerPause | None = None`
- Inside the `try` block, `result = await run_loop(...)` (currently line 1028) stays unchanged.
- In the existing `finally` block, **after** `planner._active_tool_names = previous_active` (currently line 1030), add: `_fire_persistence_tasks(planner, trajectory, result)`
- **After** the `finally` block, add: `assert result is not None`
- The existing line `await planner._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)` (currently line 1031) and `return result` (line 1032) stay unchanged after the assert.

## Required Code

```python
# Target file: penguiflow/planner/react_runtime.py
# --- New import at top of file, before hashlib ---
import asyncio      # NEW
import hashlib
import json
```

```python
# Target file: penguiflow/planner/react_runtime.py
# --- Three new helper functions, placed immediately before the run() function definition ---

async def _persist_trajectory(planner: Any, trajectory: Trajectory) -> None:
    """Save trajectory to StateStore if available. Designed to run as a background task."""
    try:
        store = getattr(planner, "_state_store", None)
        if store is None:
            return
        saver = getattr(store, "save_trajectory", None)
        if saver is None:
            return
        ctx = trajectory.tool_context or {}
        trace_id = ctx.get("trace_id")
        session_id = ctx.get("session_id")
        if not trace_id or not session_id:
            return
        await saver(trace_id, session_id, trajectory)
    except Exception:
        logger.warning("Background trajectory persistence failed", exc_info=True)


async def _persist_events(events: list[PlannerEvent], planner: Any, trace_id: str) -> None:
    """Flush planner events to StateStore. Designed to run as a background task.

    Receives an already-snapshot list of events (buffer was cleared by the caller
    before spawning this task) so there is no shared-state concern.
    """
    try:
        store = getattr(planner, "_state_store", None)
        if store is None:
            return
        saver = getattr(store, "save_planner_event", None)
        if saver is None:
            return
        for event in events:
            await saver(trace_id, event)
    except Exception:
        logger.warning("Background planner-event persistence failed", exc_info=True)


def _fire_persistence_tasks(planner: Any, trajectory: Trajectory, result: Any | None) -> None:
    """Spawn fire-and-forget background tasks for trajectory + event persistence.

    This is synchronous and returns immediately -- no awaiting.
    """
    loop = asyncio.get_running_loop()
    ctx = trajectory.tool_context or {}
    trace_id = ctx.get("trace_id")
    session_id = ctx.get("session_id")

    # Trajectory -- on PlannerFinish and PlannerPause
    if isinstance(result, (PlannerFinish, PlannerPause)) and trace_id and session_id:
        loop.create_task(
            _persist_trajectory(planner, trajectory),
            name="penguiflow-persist-trajectory",
        )

    # Events -- on any exit path (finish/pause/error/cancel)
    buf = getattr(planner, "_event_buffer", None)
    if buf and trace_id:
        events = list(buf)  # snapshot
        buf.clear()          # clear immediately so planner can reuse
        loop.create_task(
            _persist_events(events, planner, trace_id),
            name="penguiflow-persist-events",
        )
    elif buf:
        buf.clear()  # no trace_id -> discard, but still clear to avoid unbounded growth
```

```python
# Target file: penguiflow/planner/react_runtime.py
# --- Modified run() function structure (showing the try/finally area) ---
    # ... (lines before try unchanged) ...
    _init_tool_activation_state(planner, trajectory, normalised_tool_context or {}, resume=False)
    result: PlannerFinish | PlannerPause | None = None   # NEW
    try:
        base_specs: Sequence[NodeSpec] = list(
            getattr(planner, "_execution_specs", None) or getattr(planner, "_specs", None) or []
        )
        allowed_names = _allowed_names_from_policy(base_specs, tool_visibility, normalised_tool_context or {})
        all_tool_names = {spec.name for spec in base_specs}
        await _prepare_tool_discovery_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
        )
        await _prepare_skills_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
            all_tool_names=all_tool_names,
        )
        with _tool_visibility_scope(
            planner, tool_visibility=tool_visibility, tool_context=normalised_tool_context or {}
        ):
            result = await run_loop(planner, trajectory, tracker=None, error_recovery_config=error_recovery_cfg)
    finally:
        planner._active_tool_names = previous_active
        _fire_persistence_tasks(planner, trajectory, result)   # NEW
    assert result is not None
    await planner._maybe_record_memory_turn(query, result, trajectory, resolved_key)
    return result
```

```python
# Target file: penguiflow/planner/react_runtime.py
# --- Modified resume() function structure (showing the try/finally area) ---
    # ... (lines before try unchanged) ...
    _init_tool_activation_state(planner, trajectory, trajectory.tool_context or {}, resume=True)
    result: PlannerFinish | PlannerPause | None = None   # NEW
    try:
        base_specs: Sequence[NodeSpec] = list(
            getattr(planner, "_execution_specs", None) or getattr(planner, "_specs", None) or []
        )
        allowed_names = _allowed_names_from_policy(base_specs, tool_visibility, trajectory.tool_context or {})
        all_tool_names = {spec.name for spec in base_specs}
        await _prepare_tool_discovery_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
        )
        await _prepare_skills_context(
            planner,
            trajectory,
            allowed_names=allowed_names,
            all_tool_names=all_tool_names,
        )
        with _tool_visibility_scope(
            planner, tool_visibility=tool_visibility, tool_context=trajectory.tool_context or {}
        ):
            result = await run_loop(planner, trajectory, tracker=tracker, error_recovery_config=error_recovery_cfg)
    finally:
        planner._active_tool_names = previous_active
        _fire_persistence_tasks(planner, trajectory, result)   # NEW
    assert result is not None
    await planner._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)
    return result
```

## Exit Criteria (Success)
- [ ] `react_runtime.py` imports `asyncio` at the top of the file (before `hashlib`).
- [ ] `_persist_trajectory`, `_persist_events`, and `_fire_persistence_tasks` exist as module-level functions in `react_runtime.py`, placed before `run()`.
- [ ] `run()` has `result: PlannerFinish | PlannerPause | None = None` before the `try`, and `_fire_persistence_tasks(planner, trajectory, result)` in its `finally` block, followed by `assert result is not None`.
- [ ] `resume()` has the same pattern: `result = None` before `try`, `_fire_persistence_tasks` in `finally`, `assert result is not None` after.
- [ ] `uv run ruff check penguiflow/planner/react_runtime.py` reports zero errors.
- [ ] `uv run mypy` reports zero new type errors.
- [ ] `uv run pytest tests/test_react_planner.py -x -q` passes (existing planner tests still work).

## Implementation Notes
- **Fire-and-forget via `loop.create_task()`** -- persistence never blocks the planner's return path.
- **Errors logged, not propagated** -- both async helpers wrap everything in `try/except` and log at `WARNING` level.
- **Buffer snapshot-and-clear is synchronous** -- `list(buf)` + `buf.clear()` happens before the task is spawned, so there is no race condition.
- **`assert result is not None` after `finally`**: If `run_loop()` raises, the exception propagates before the assert is reached -- standard Python behavior. No comment needed.
- Uses duck-typing (`getattr`) consistent with existing StateStore patterns (see `pause_management.py` lines 70-80).
- Named tasks (`name="penguiflow-persist-*"`) for easier debugging in `asyncio.all_tasks()` output.
- `_persist_trajectory` runs on `PlannerFinish` and `PlannerPause` (but NOT on `run_loop()` exceptions).
- `_persist_events` runs on ALL exit paths (finish/pause/error/cancel) -- best-effort.
- The planner itself must NEVER generate a `trace_id` or `session_id`. These are always passed in via `tool_context`. If missing, persistence is silently skipped.
- Depends on Phase 0 (the `_event_buffer` field must exist and be initialized).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/planner/react_runtime.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_react_planner.py -x -q
```
