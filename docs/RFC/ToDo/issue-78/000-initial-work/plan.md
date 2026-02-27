# Plan: Move trajectory & planner event persistence from playground into the planner

## Context

Currently, the ReactPlanner maintains trajectories in memory during execution and returns them via `PlannerFinish.metadata`. Two types of persistence only happen in the playground wrapper (`playground_wrapper.py`), meaning agents used outside the playground never persist them:

1. **Trajectories** — `save_trajectory()` is called by the wrapper after `run()` / `resume()` completes.
2. **Planner events** — The wrapper's `_EventRecorder` buffers events via a sync callback, then flushes them via `save_planner_event()` after the run.

This change moves both into the planner itself so that any caller benefits from persistence automatically.

**Critical constraint: fire-and-forget.** Trajectory and event persistence exist solely for traceability/observability. They must **never** slow down the planner's hot path. Both persistence operations are launched as background `asyncio.Task`s — the planner does not `await` them. Failures are logged and swallowed; they must not propagate exceptions to the caller.

## Approach

### A. Trajectory persistence

Add a helper `_persist_trajectory` in `react_runtime.py` and **fire it as a background task** from `run()` and `resume()` after `run_loop()` returns `PlannerFinish`. The planner proceeds immediately without waiting for the save to complete. Remove the redundant saves from the playground wrapper.

### B. Planner event persistence

The challenge: `_emit_event()` is **sync** but `save_planner_event()` is **async**. We follow the same buffering pattern the playground uses: buffer events in-memory during execution, then flush them to the state store after `run_loop()` completes — but the flush itself is **fire-and-forget** (background task). This is implemented as:

1. Add an `_event_buffer: list[PlannerEvent]` field on `ReactPlanner`.
2. In `_emit_event()`, append to the buffer (sync, zero overhead).
3. Add `_persist_events()` async helper in `react_runtime.py` — flushes the buffer to `save_planner_event()`.
4. **Fire `_persist_events()` as a background task** from `run()` and `resume()` after `run_loop()` returns (same location as trajectory persistence). The buffer is snapshot-and-cleared synchronously before the task is spawned, so the planner can immediately continue.
5. Remove `_EventRecorder.persist()` calls and simplify `_EventRecorder` in the playground wrapper (see Section 4 for details).

Note: The playground wrapper's `_EventRecorder` also supports an `event_consumer` callback for real-time SSE streaming to the UI. **Important:** `event_consumer` is wired *through* `_EventRecorder.callback()` — the closure both buffers events and calls `event_consumer`. After this migration, `_EventRecorder` must be simplified to a forward-only class: remove the buffer and `persist()` method, but keep `callback()` so it still produces a closure that calls `event_consumer`. The `event_consumer` path is NOT wired separately from the buffer path — they share the same closure — so this simplification is required to preserve SSE streaming.

## Files to modify

### 1. `penguiflow/planner/react.py` — Add event buffer field

Add `_event_buffer: list[PlannerEvent]` to the class-level type annotation declarations. Insert it between `_event_callback` (line 349) and `_hop_budget` (line 350):

```python
    _event_callback: PlannerEventCallback | None
    _event_buffer: list[PlannerEvent]              # NEW
    _hop_budget: int | None
```

In `_emit_event()` (line 1181), append the event to `self._event_buffer` **at the very beginning of the method body, before the logging block (before line 1184)**. This ensures the event is buffered even if the callback later raises an exception:

```python
    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        self._event_buffer.append(event)           # NEW — buffer for persistence

        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        ...  # rest of method unchanged
```

### 2. `penguiflow/planner/react_init.py` — Initialize buffer

In the `init_react_planner()` function, add `planner._event_buffer = []` right after `planner._event_callback = event_callback` (line 450):

```python
    planner._event_callback = event_callback         # line 450
    planner._event_buffer = []                        # NEW
    planner._absolute_max_parallel = absolute_max_parallel  # line 451
```

### 3. `penguiflow/planner/react_runtime.py` — Add persistence helpers

**New import required:** `react_runtime.py` does not currently import `asyncio`. Add `import asyncio` to the imports at the top of the file **before** `import hashlib` (line 5) to maintain alphabetical ordering required by ruff/isort:

```python
import asyncio      # NEW
import hashlib
import json
```

**Important:** `react_runtime.py` already defines `logger = logging.getLogger("penguiflow.planner")` at line 45. Do NOT add another logger definition — the helpers below use the existing module-level `logger`.

**No additional imports needed:** The new helpers use `PlannerEvent` (already imported at line 29), `PlannerFinish` (line 30), `Trajectory` (line 42), and `Any` from `typing` (line 13). All are already present in `react_runtime.py`.

Add three private helpers — two async coroutines for the actual persistence work, and one sync launcher that fires them as background tasks. **Place them immediately before the `run()` function definition (line 876):**

```python
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


def _fire_persistence_tasks(planner: Any, trajectory: Trajectory, result: Any) -> None:
    """Spawn fire-and-forget background tasks for trajectory + event persistence.

    This is synchronous and returns immediately — no awaiting.
    """
    loop = asyncio.get_running_loop()
    ctx = trajectory.tool_context or {}
    trace_id = ctx.get("trace_id")

    # Trajectory — only on PlannerFinish
    if isinstance(result, PlannerFinish):
        loop.create_task(
            _persist_trajectory(planner, trajectory),
            name="penguiflow-persist-trajectory",
        )

    # Events — on both PlannerFinish and PlannerPause
    buf = getattr(planner, "_event_buffer", None)
    if buf and trace_id:
        events = list(buf)  # snapshot
        buf.clear()          # clear immediately so planner can reuse
        loop.create_task(
            _persist_events(events, planner, trace_id),
            name="penguiflow-persist-events",
        )
    elif buf:
        buf.clear()  # no trace_id → discard, but still clear to avoid unbounded growth
```

**Call from `run()`** — modify the `try` block around `run_loop()` (lines 922-944). Currently the code is `try: ... finally:` (no `except`). Add an `except BaseException` clause **between** the existing `try` body and the existing `finally` clause — do NOT duplicate or move the `finally` block. Then insert the `_fire_persistence_tasks` call **after** the `finally` block but **before** `_maybe_record_memory_turn`. `_fire_persistence_tasks` must run after `finally` (so `_active_tool_names` is restored) but before memory recording. The full structure becomes:

```python
    try:
        ...
            result = await run_loop(...)          # line 942
    except BaseException:
        # Clear event buffer on error to prevent stale events leaking into subsequent runs
        buf = getattr(planner, "_event_buffer", None)
        if buf:
            buf.clear()
        raise
    finally:
        planner._active_tool_names = previous_active  # line 944
    _fire_persistence_tasks(planner, trajectory, result)   # NEW — fire-and-forget
    await planner._maybe_record_memory_turn(query, result, trajectory, resolved_key)  # line 945
    return result
```

**Call from `resume()`** — same pattern as `run()`: add `except BaseException` between the existing `try` body and `finally` clause, then insert `_fire_persistence_tasks` after `finally`. Modify the `try` block around `run_loop()` (lines 1008-1030):

```python
    try:
        ...
            result = await run_loop(...)          # line 1028
    except BaseException:
        # Clear event buffer on error to prevent stale events leaking into subsequent runs
        buf = getattr(planner, "_event_buffer", None)
        if buf:
            buf.clear()
        raise
    finally:
        planner._active_tool_names = previous_active  # line 1030
    _fire_persistence_tasks(planner, trajectory, result)   # NEW — fire-and-forget
    await planner._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)  # line 1031
    return result
```

Note: `_persist_events` runs for both `PlannerFinish` and `PlannerPause` — events emitted during a paused run are still valuable. `_persist_trajectory` only runs on `PlannerFinish` (pauses already snapshot via `SupportsPlannerState`).

Key design decisions:
- **Fire-and-forget via `loop.create_task()`** — persistence never blocks the planner's return path. The event loop runs the tasks in the background whenever it gets a chance.
- **Errors logged, not propagated** — both helpers wrap everything in `try/except` and log at `WARNING` level. A flaky state store cannot crash or slow down the planner.
- **Buffer snapshot-and-clear is synchronous** — `list(buf)` + `buf.clear()` happens before the task is spawned, so there is no race between the planner reusing the buffer and the background task reading it.
- **Buffer cleared on `run_loop()` error** — the `except BaseException` clause clears the event buffer before re-raising. This prevents stale events from a failed run leaking into the next successful run's persistence flush.
- Uses duck-typing (`getattr`) consistent with existing StateStore patterns (see `pause_management.py` lines 70-80)
- Requires `trace_id` (and for trajectories also `session_id`) in `tool_context` — silently skips if missing
- Named tasks (`name="penguiflow-persist-*"`) for easier debugging in `asyncio.all_tasks()` output

### 4. `penguiflow/cli/playground_wrapper.py` — Remove redundant persistence

**Remove trajectory saves from three locations:**
- `PlannerAgentWrapper.chat()` (lines 308-313): Remove `_build_trajectory()` + `save_trajectory` block.
- `PlannerAgentWrapper.resume()` (lines 405-411): Same.
- `OrchestratorAgentWrapper.chat()` (lines 586-591): Same.

Note: `OrchestratorAgentWrapper.resume()` does NOT currently save trajectories — there is nothing to remove there.

**Assumption for `OrchestratorAgentWrapper`:** Removing trajectory saves here assumes the orchestrator's `execute()` method internally uses a `ReactPlanner` that routes through `react_runtime.run()` / `resume()`, where persistence now happens automatically. If an orchestrator uses a different code path, its trajectories will not be persisted. This is acceptable: non-planner orchestrators can add their own persistence if needed.

**Remove `_build_trajectory()` helper** (lines 174-211). Verified: `_build_trajectory` is only called within `playground_wrapper.py` (lines 308, 406, 586) and its test file (`tests/cli/test_playground_wrapper_helpers.py`). No other callers exist — safe to remove.

**Remove unused `Trajectory` import** (line 20): After deleting `_build_trajectory()`, the `Trajectory` import at line 20 becomes unused — it is only referenced at lines 181 and 211 inside `_build_trajectory`. Remove it from the import block to prevent a ruff F401 error. The remaining imports (`PlannerEvent`, `PlannerEventCallback`, `PlannerFinish`, `PlannerPause`, `ReactPlanner`) are still used elsewhere in the file.

**Note on trajectory data fidelity:** The old `_build_trajectory()` *reconstructed* a Trajectory from serialized `PlannerFinish.metadata` (steps, artifacts, sources, summary). The new planner-level persistence saves the **live** `Trajectory` object directly — the authoritative source that metadata was derived from. The live trajectory already contains all fields (artifacts, sources, summary, steps) because they are populated during `run_loop()`. This is an improvement: the live object is more complete and avoids lossy round-tripping through metadata serialization.

**Remove `_EventRecorder.persist()` calls from four locations:**
- `PlannerAgentWrapper.chat()` (line 282): Remove `await self._event_recorder.persist(trace_id)`.
- `PlannerAgentWrapper.resume()` (line 378): Same.
- `OrchestratorAgentWrapper.chat()` (line 576): Same.
- `OrchestratorAgentWrapper.resume()` (line 687): Same.

**Simplify `_EventRecorder` to forward-only:** Remove the `__init__` method entirely (including `self._state_store` and `self._buffer`), remove the `persist()` method, and simplify `callback()` to only call `event_consumer(event, trace_id)` when one is provided. The simplified class should look like:

```python
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

**Behavioral change to note:** The old `callback()` returned a closure even when `event_consumer` was `None`, as long as `state_store` was set (because the closure needed to buffer events for later `persist()`). The new `callback()` returns `None` when `event_consumer is None` regardless of whether a state_store exists. This is correct because the planner now handles event buffering and persistence internally — the `_EventRecorder` only needs to forward events for SSE streaming.

Update the `__init__` methods of `PlannerAgentWrapper` (line 226) and `OrchestratorAgentWrapper` (line 442) to pass no arguments: `self._event_recorder = _EventRecorder()`.

### 5. Documentation updates

**`docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`** (line ~807):
- Section "10. Trajectory Persistence": Update `**Location (integration):**` from `penguiflow/cli/playground.py` to `penguiflow/planner/react_runtime.py`. Note that the planner now persists trajectories directly after `run()` / `resume()` completes.
- Section "11. Planner Event Persistence": Same location update — events are now persisted by the planner, not the playground.

**`docs/tools/statestore.md`** (line ~60-72):
- Under "Planner event storage" and "Sessions/tasks/steering/trajectories": Add notes that both are now handled automatically by the planner when a StateStore with the respective capabilities is provided.

**`docs/architecture/planning_orchestration/reactplanner_core.md`**:
- Section "2. Trajectory Management" (line ~21-29): Add a bullet about automatic persistence to StateStore on completion.
- Section "1. Planning Loop Flow" diagram (line ~299-324): Update the flow to show trajectory + event persistence after the result is produced.

**`docs/PLAYGROUND_BACKEND_CONTRACTS.md`** (line ~112-114):
- Note that `session_id` and `trace_id` in `tool_context` are now used by the planner for automatic trajectory and event persistence — the playground no longer saves these itself.

**`REACT_PLANNER_INTEGRATION_GUIDE.md`** (at repo root, NOT in `docs/`) (line ~939):
- Update "If you persist trajectories (e.g., via the Playground state store)" to reflect that the planner now auto-persists trajectories and events when a StateStore is provided.

### 6. Tests

**Add tests for planner-level persistence in `tests/planner/test_persistence.py` (new file).**

Note: The `tests/planner/` directory does not currently exist — it must be created along with an empty `tests/planner/__init__.py` file (zero bytes, no content).

Since persistence is fire-and-forget (`asyncio.create_task`), tests must drain the event loop after `run()`/`resume()` to let background tasks complete before asserting. **Do NOT use `await asyncio.sleep(0)`** — a single yield is insufficient because background tasks have multiple await points (e.g., `InMemoryStateStore` methods use `async with self._lock:`). Instead, gather the named tasks explicitly:

```python
import asyncio

async def _drain_persistence_tasks() -> None:
    """Wait for all fire-and-forget persistence tasks to complete."""
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("penguiflow-persist")]
    if tasks:
        await asyncio.gather(*tasks)
```

Call `await _drain_persistence_tasks()` after every `run()` / `resume()` call in tests before asserting on store contents. This is reliable regardless of how many await points the background tasks have.

**Test scaffolding — how to create a working planner for integration tests:**

Use the `ReactPlanner` constructor directly with a scripted `llm_client` (no real LLM calls). See `tests/test_tool_background_mode.py` for an existing pattern. The minimal setup:

```python
from penguiflow.node import Node
from penguiflow.catalog import build_catalog
from penguiflow.registry import ModelRegistry
from penguiflow.planner import ReactPlanner
from penguiflow.state.in_memory import InMemoryStateStore

# 1. Define a trivial tool node
@tool(desc="echo")
async def echo(args: EchoArgs, ctx):
    return {"answer": args.text}

# 2. Build catalog + registry
registry = ModelRegistry()
registry.register("echo", EchoArgs, EchoOut)
catalog = build_catalog([Node(echo, name="echo")], registry)

# 3. Create a scripted LLM client that returns a finish action
class ScriptedClient:
    async def complete(self, *, messages, **_):
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'

# 4. Create planner with state_store
store = InMemoryStateStore()
planner = ReactPlanner(
    llm_client=ScriptedClient(),
    catalog=catalog,
    max_iters=2,
    state_store=store,
)

# 5. Run with tool_context containing trace_id + session_id
result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
await _drain_persistence_tasks()
```

The `InMemoryStateStore` is imported from `penguiflow.state.in_memory`. It implements `save_trajectory`, `get_trajectory`, `save_planner_event`, and `list_planner_events` — all needed for these tests. For "state store lacking `save_trajectory`" tests, use a mock/stub object that only has some methods.

**Test cases:**

- Test: planner with `InMemoryStateStore` and `tool_context={"session_id": "s1", "trace_id": "t1"}` → after `run()` + loop drain, `store.get_trajectory("t1", "s1")` returns the trajectory.
- Test: planner with `InMemoryStateStore` → after `run()` + loop drain, `store.list_planner_events("t1")` returns events.
- Test: planner without a state store → no error, no background tasks spawned.
- Test: planner with a state store lacking `save_trajectory` / `save_planner_event` → silently skipped, no error.
- Test: planner `resume()` also persists trajectory + events (same drain pattern).
- Test: state store `save_trajectory` raises → planner returns normally, error is logged (not propagated). Verify via `caplog` or mock logger.
- Test: state store `save_planner_event` raises → same: planner unaffected, warning logged.
- Test: event buffer is cleared when `run_loop()` raises → after error, `planner._event_buffer` is empty.

**Update playground wrapper tests in `tests/cli/test_playground_wrapper_helpers.py`:**

Specific changes required:

- **Remove import** of `_build_trajectory` (line 13). The function is deleted.
- **Remove `test_build_trajectory`** (lines 192-218). The function no longer exists.
- **Update `_EventRecorder` constructor calls** at lines 112, 119, and 140: change `_EventRecorder(None)` and `_EventRecorder(store)` to `_EventRecorder()` (no arguments — the simplified class has no `__init__`).
- **Rewrite `test_event_recorder_buffers_and_persists`** (lines 117-135): The buffer and `persist()` no longer exist. Replace with a test that verifies forward-only behavior:
  - `_EventRecorder().callback(event_consumer=fn)` returns a callable that invokes `fn(event, trace_id)`.
  - `_EventRecorder().callback(trace_id_supplier=supplier, event_consumer=fn)` passes the supplier's return value as `trace_id`.
- **Remove `test_event_recorder_clears_buffer_without_store`** (lines 138-149) entirely. The buffer no longer exists.
- **Update `test_event_recorder_callback_none_when_unused`** (lines 111-113): Change `_EventRecorder(None)` to `_EventRecorder()`. The assertion `recorder.callback() is None` still holds (no `event_consumer` → returns `None`). Add an additional assertion that `_EventRecorder().callback(event_consumer=some_fn)` returns a non-None callback, to verify the positive path works.
- **Remove trajectory assertion in `test_planner_agent_wrapper_pause_and_finish`** (line 241): `store.trajectories[0][0] == "trace-2"` will fail because the wrapper no longer saves trajectories. Remove the `store = DummyStore(...)` setup at line 237, the `state_store=store` kwarg at line 238, and the `store.trajectories` assertion at line 241. After removal, the wrapper constructor call becomes `PlannerAgentWrapper(DummyPlanner(run_result=finish))` (no `state_store`), and the remaining test logic (chat call + `result.answer == "fallback"` assertion at line 240) stays unchanged. The wrapper now only passes through to the planner; trajectory persistence is tested in the new `tests/planner/test_persistence.py`.
- **Remove the `DummyStore` class entirely** (lines 25-34, including the `@dataclass` decorator at line 25). After the changes above, no remaining test references `DummyStore`: `test_event_recorder_buffers_and_persists` is rewritten without it, and the `store = DummyStore(...)` in `test_planner_agent_wrapper_pause_and_finish` is removed. The class is dead code — remove it completely. **Note:** `DummyPlanner` (lines 37-47) and `DummyOrchestrator` (lines 50-66) must be kept — they are still used by other tests.

**Update `tests/cli/test_playground_backend.py`:** Lines 103-104 use `save_trajectory` on a state store but do NOT test the wrapper's trajectory saving — they test the state store directly. These lines are **not affected** by this migration and should remain unchanged.

## Verification

1. `uv run ruff check .` — lint passes
2. `uv run mypy` — type check passes
3. `uv run pytest tests/planner/test_persistence.py tests/test_planner*.py tests/cli/test_playground_wrapper_helpers.py tests/cli/test_playground_backend.py -v` — targeted test run
4. `uv run pytest --cov=penguiflow --cov-report=term --cov-fail-under=84.5` — full suite with coverage threshold
5. `uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict` — docs build passes with no warnings
