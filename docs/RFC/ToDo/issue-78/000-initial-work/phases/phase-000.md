# Phase 000: Planner Core -- Add Event Buffer Field and Initialize It

## Objective
Add the `_event_buffer: list[PlannerEvent]` field to `ReactPlanner` (type annotation + initialization) and wire `_emit_event()` to append every event to the buffer. This is the foundation for all subsequent persistence work -- the buffer collects events during execution so they can be flushed to the state store later.

## Tasks
1. Add `_event_buffer` type annotation to `ReactPlanner` class body in `react.py`.
2. Append to `self._event_buffer` at the top of `_emit_event()` in `react.py`.
3. Initialize `planner._event_buffer = []` in `init_react_planner()` in `react_init.py`.

## Detailed Steps

### Step 1: Add type annotation in `react.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react.py`.
- Locate the class-level type annotation block. Find the line `_event_callback: PlannerEventCallback | None` (currently at line 349).
- Insert `_event_buffer: list[PlannerEvent]` on a new line immediately after `_event_callback: PlannerEventCallback | None` and before `_hop_budget: int | None` (currently at line 350).

### Step 2: Append to buffer in `_emit_event()` in `react.py`
- In the same file, locate the `_emit_event` method (currently at line 1195).
- Add `self._event_buffer.append(event)` as the **very first line** of the method body, before the comment `# Log the event (strip reserved logging keys to avoid collisions)` and before `payload = event.to_payload()`.
- This ensures the event is buffered even if the callback later raises an exception.

### Step 3: Initialize buffer in `react_init.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react_init.py`.
- Locate the line `planner._event_callback = event_callback` (currently at line 450) inside the `init_react_planner()` function.
- Add `planner._event_buffer = []` on the line immediately after it, before `planner._absolute_max_parallel = absolute_max_parallel` (currently at line 451).

## Required Code

```python
# Target file: penguiflow/planner/react.py
# --- In the class-level annotation block, insert between _event_callback and _hop_budget ---
    _event_callback: PlannerEventCallback | None
    _event_buffer: list[PlannerEvent]              # NEW
    _hop_budget: int | None
```

```python
# Target file: penguiflow/planner/react.py
# --- At the top of _emit_event() method body ---
    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        self._event_buffer.append(event)           # NEW -- buffer for persistence

        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        for reserved in ("args", "msg", "levelname", "levelno", "exc_info"):
            payload.pop(reserved, None)
        log_fn = logger.debug if event.event_type == "llm_stream_chunk" else logger.info
        log_fn(event.event_type, extra=payload)

        # Invoke callback if provided
        if self._event_callback is not None:
            try:
                self._event_callback(event)
            except Exception:
                logger.exception(
                    "event_callback_error",
                    extra={
                        "event_type": event.event_type,
                        "step": event.trajectory_step,
                    },
                )
        self._last_event = event
```

```python
# Target file: penguiflow/planner/react_init.py
# --- In init_react_planner(), after _event_callback assignment ---
    planner._event_callback = event_callback         # existing line 450
    planner._event_buffer = []                        # NEW
    planner._absolute_max_parallel = absolute_max_parallel  # existing line 451
```

## Exit Criteria (Success)
- [ ] `penguiflow/planner/react.py` contains `_event_buffer: list[PlannerEvent]` in the class-level annotation block between `_event_callback` and `_hop_budget`.
- [ ] `_emit_event()` in `react.py` has `self._event_buffer.append(event)` as its first statement before the logging block.
- [ ] `penguiflow/planner/react_init.py` contains `planner._event_buffer = []` immediately after `planner._event_callback = event_callback`.
- [ ] `uv run ruff check penguiflow/planner/react.py penguiflow/planner/react_init.py` reports zero errors.
- [ ] `uv run mypy` reports zero new type errors.
- [ ] `uv run pytest tests/test_react_planner.py -x -q` passes (existing planner tests still work with the new buffer field).

## Implementation Notes
- `PlannerEvent` is already imported in `react.py` (used in the `_emit_event` signature and type annotations). No new import needed.
- Session-forked planners go through `__init__()` -> `_init_react_planner()`, so each fork gets its own independent `_event_buffer = []`. No additional changes are needed for the forking path.
- The buffer is append-only at this point. Flushing/clearing is added in Phase 1.
- Use the Edit tool's `old_string` parameter for precise edits. Line numbers in this document are for locating context only -- they may shift if the file has been modified.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/planner/react.py penguiflow/planner/react_init.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_react_planner.py -x -q
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- **`penguiflow/planner/react.py`**: Added `_event_buffer: list[PlannerEvent]` type annotation in the class-level annotation block of `ReactPlanner`, positioned between `_event_callback` and `_hop_budget` (line 350).
- **`penguiflow/planner/react.py`**: Added `self._event_buffer.append(event)` as the first statement in the `_emit_event()` method body (line 1198), before the logging block.
- **`penguiflow/planner/react_init.py`**: Added `planner._event_buffer = []` initialization in `init_react_planner()` (line 451), immediately after `planner._event_callback = event_callback`.

### Key Considerations
- All three changes are minimal and surgical, touching only the exact lines specified in the plan.
- The `self._event_buffer.append(event)` call is placed before both logging and callback invocation, ensuring that every event is captured in the buffer regardless of whether the callback raises an exception. This matches the plan's stated intent.
- `PlannerEvent` was already imported in `react.py` (used in the `_emit_event` method signature and other type annotations), so no new imports were needed.

### Assumptions
- The `_event_buffer` field does not need a default value in the class body annotation block (it follows the same pattern as other fields like `_event_callback`, `_hop_budget`, etc., which are annotated in the class body but initialized in `init_react_planner()`).
- Session-forked planners that go through `init_react_planner()` will each get their own independent empty list, so no shallow-copy concerns exist.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The buffer grows without bound during a planner run. The phase file notes that flushing/clearing is deferred to Phase 1. If a planner run emits a very large number of events before Phase 1 is implemented, memory usage could increase. This is acknowledged in the plan and is by design.
- Any code path that creates a `ReactPlanner` without going through `init_react_planner()` would lack `_event_buffer` initialization and would raise `AttributeError` on the first `_emit_event()` call. The type annotation in the class body will help catch this statically, and the plan notes that all creation paths go through `init_react_planner()`.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react.py` (modified)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/react_init.py` (modified)
