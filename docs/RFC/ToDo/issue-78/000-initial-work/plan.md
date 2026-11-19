# Plan: Move trajectory & planner event persistence into the planner

## Context

Currently, the ReactPlanner maintains trajectories in memory during execution and returns them via `PlannerFinish.metadata`. Two types of persistence only happen in the caller's HTTP layer, meaning agents used without it never persist them:

1. **Trajectories** — `save_trajectory()` is called by the HTTP layer after `run()` / `resume()` completes.
2. **Planner events** — The HTTP layer buffers events via a sync callback, then flushes them via `save_planner_event()` after the run.

This change moves both into the planner itself so that any caller benefits from persistence automatically.

**Critical constraint: fire-and-forget.** Trajectory and event persistence exist solely for traceability/observability. They must **never** slow down the planner's hot path. Both persistence operations are launched as background `asyncio.Task`s — the planner does not `await` them. Failures are logged and swallowed; they must not propagate exceptions to the caller.

**Explicit decisions (confirmed):**
- **Orchestrator trace IDs:** In orchestrator mode, the caller's trace id (for example a UI client's `run_id`) is the source of truth for persistence. The caller must inject `trace_id` + `session_id` into the orchestrator `tool_context` **before** calling `execute()` / `resume()`, and orchestrators must propagate that `tool_context` into the internal `ReactPlanner` calls.
- **Missing IDs:** If `tool_context` is missing required IDs (`trace_id` for events; `trace_id` + `session_id` for trajectories), persistence is **silently skipped** (no ID generation).
- **Error/cancel:** Buffered planner events are persisted even when `run_loop()` raises or the caller cancels (best-effort, still fire-and-forget).
- **Pause:** Trajectories are persisted for both `PlannerFinish` **and** `PlannerPause`.
- **Event completeness:** Persist **all** planner events (no event-type filtering).

## Implementation order

Implement sections in order: **1 → 2 → 3 → 5 → 6**. Sections 1–3 are independent (planner core changes), Section 5 is docs (independent), and Section 6 (tests) depends on 1–3. (There is no Section 4: it covered HTTP-layer and project-template code that is no longer part of the library.)

Phase files: `phase-000` and `phase-001` (Sections 1–3), `phase-004` (Section 5), `phase-005` (Section 6). Phases 002, 003 and 006 were dropped with Section 4.

## Implementation notes (verified)

The following decisions were confirmed during plan verification on 2026-03-04 and re-verified on 2026-03-05:

- **`InMemoryStateStore` import path in new tests** (Section 6): New planner-level tests in `tests/planner/test_persistence.py` should import from `penguiflow.state.in_memory` (the canonical location).

- **`assert result is not None` after `finally`** (Section 3): No comment needed. If `run_loop()` raises, the exception propagates before the assert is reached — standard Python behavior.
- **`InMemoryStateStore.save_trajectory` overwrites** (Section 6): Verified — `save_trajectory` uses `self._trajectories[trace_id] = ...` (dict keyed by `trace_id`), so calling it twice with the same `(trace_id, session_id)` overwrites. The `len(traj_after.steps) > len(traj.steps)` assertion in the resume test is valid because the resumed trajectory accumulates steps from both phases.

## Approach

### A. Trajectory persistence

Add a helper `_persist_trajectory` in `react_runtime.py` and **fire it as a background task** from `run()` and `resume()` after `run_loop()` returns (`PlannerFinish` **or** `PlannerPause`). The planner proceeds immediately without waiting for the save to complete.

### B. Planner event persistence

The challenge: `_emit_event()` is **sync** but `save_planner_event()` is **async**. We use a buffering pattern: buffer events in-memory during execution, then flush them to the state store on exit — but the flush itself is **fire-and-forget** (background task). This is implemented as:

1. Add an `_event_buffer: list[PlannerEvent]` field on `ReactPlanner`.
2. In `_emit_event()`, append to the buffer (sync, zero overhead).
3. Add `_persist_events()` async helper in `react_runtime.py` — flushes the buffer to `save_planner_event()`.
4. **Always flush the buffer in a `finally` block** in `run()` and `resume()`, so events are persisted on `PlannerFinish`, `PlannerPause`, and also on `run_loop()` errors/cancellation. The flush itself is fire-and-forget; the buffer is snapshot-and-cleared synchronously before the task is spawned, so the planner can immediately continue.

Note: Callers that stream events in real time (for example over SSE to a UI client) keep using the planner `event_callback`; it is independent of the persistence buffer.

## Files to modify

### 1. `penguiflow/planner/react.py` — Add event buffer field

Add `_event_buffer: list[PlannerEvent]` to the class-level type annotation declarations. Insert it between `_event_callback` (line 349) and `_hop_budget` (line 350):

```python
    _event_callback: PlannerEventCallback | None
    _event_buffer: list[PlannerEvent]              # NEW
    _hop_budget: int | None
```

In `_emit_event()` (line 1195), append the event to `self._event_buffer` **at the very beginning of the method body, before the logging block (before line 1198)**. This ensures the event is buffered even if the callback later raises an exception:

```python
    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        self._event_buffer.append(event)           # NEW — buffer for persistence

        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        ...  # rest of method unchanged
```

### 2. `penguiflow/planner/react_init.py` — Initialize buffer

**Initialization path note:** `ReactPlanner.__init__()` (react.py line 529) delegates to `_init_react_planner()` internally. Adding the buffer here is sufficient — any planner created via `ReactPlanner(...)` or via direct `init_react_planner()` will have `_event_buffer` initialized. The test scaffolding's `ReactPlanner(llm_client=..., catalog=..., state_store=store)` works because `__init__` accepts `state_store` (line 429) and forwards it.

**Session-forked planners:** `ReactPlanner.__init__` has a session-dispatch mechanism that forks planners per-session using `_init_kwargs`. Forked planners go through `__init__()` → `_init_react_planner()`, so each fork gets its own independent `_event_buffer = []`. This is correct — each forked planner buffers and persists events independently. No additional changes are needed for the forking path.

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

**No additional imports needed:** The new helpers use `PlannerEvent` (already imported at line 29), `PlannerFinish` (line 30), `PlannerPause` (line 31), `Trajectory` (line 42), and `Any` from `typing` (line 13). All are already present in `react_runtime.py`.

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


def _fire_persistence_tasks(planner: Any, trajectory: Trajectory, result: Any | None) -> None:
    """Spawn fire-and-forget background tasks for trajectory + event persistence.

    This is synchronous and returns immediately — no awaiting.
    """
    loop = asyncio.get_running_loop()
    ctx = trajectory.tool_context or {}
    trace_id = ctx.get("trace_id")
    session_id = ctx.get("session_id")

    # Trajectory — on PlannerFinish and PlannerPause
    if isinstance(result, (PlannerFinish, PlannerPause)) and trace_id and session_id:
        loop.create_task(
            _persist_trajectory(planner, trajectory),
            name="penguiflow-persist-trajectory",
        )

    # Events — on any exit path (finish/pause/error/cancel)
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

**Call from `run()`** — modify the `try` block around `run_loop()` (lines 922-944). Keep the existing `finally` block (do NOT duplicate or move it), but:
- Define `result: PlannerFinish | PlannerPause | None = None` before the `try` so it is available in `finally`.
- In the existing `finally` block, call `_fire_persistence_tasks(...)` **after** restoring `_active_tool_names`. This ensures events are flushed on *all* exit paths (finish/pause/error/cancel).
Then `assert result is not None` and continue to `_maybe_record_memory_turn`. The full structure becomes:

```python
    result: PlannerFinish | PlannerPause | None = None
    try:
        ...
            result = await run_loop(...)          # line 942
    finally:
        planner._active_tool_names = previous_active  # line 944
        _fire_persistence_tasks(planner, trajectory, result)   # NEW — fire-and-forget (always flushes events)
    assert result is not None
    await planner._maybe_record_memory_turn(query, result, trajectory, resolved_key)  # line 945
    return result
```

**Call from `resume()`** — same pattern as `run()`: define `result = None` before the `try`, and call `_fire_persistence_tasks(...)` in the `finally` block after `_active_tool_names` is restored. Modify the `try` block around `run_loop()` (lines 1008-1030):

```python
    result: PlannerFinish | PlannerPause | None = None
    try:
        ...
            result = await run_loop(...)          # line 1028
    finally:
        planner._active_tool_names = previous_active  # line 1030
        _fire_persistence_tasks(planner, trajectory, result)   # NEW — fire-and-forget (always flushes events)
    assert result is not None
    await planner._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)  # line 1031
    return result
```

Note:
- `_persist_events` runs on `PlannerFinish`, `PlannerPause`, and also on `run_loop()` errors/cancellation (best-effort).
- `_persist_trajectory` runs on both `PlannerFinish` and `PlannerPause` (but **not** on `run_loop()` exceptions).

Key design decisions:
- **Fire-and-forget via `loop.create_task()`** — persistence never blocks the planner's return path. The event loop runs the tasks in the background whenever it gets a chance.
- **Errors logged, not propagated** — both helpers wrap everything in `try/except` and log at `WARNING` level. A flaky state store cannot crash or slow down the planner.
- **Buffer snapshot-and-clear is synchronous** — `list(buf)` + `buf.clear()` happens before the task is spawned, so there is no race between the planner reusing the buffer and the background task reading it.
- **Buffer flushed/cleared in `finally`** — `_fire_persistence_tasks` always snapshots+clears the buffer in a `finally` block, so stale events cannot leak into subsequent runs, and error/cancel paths still get event persistence.
- Uses duck-typing (`getattr`) consistent with existing StateStore patterns (see `pause_management.py` lines 70-80)
- Requires `trace_id` (and for trajectories also `session_id`) in `tool_context` — silently skips if missing
- Named tasks (`name="penguiflow-persist-*"`) for easier debugging in `asyncio.all_tasks()` output

### 5. Documentation updates

**`docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`** (line ~807):
- Section "10. Trajectory Persistence": Update `**Location (integration):**` to `penguiflow/planner/react_runtime.py`. Note that the planner now persists trajectories directly after `run()` / `resume()` returns (`PlannerFinish` or `PlannerPause`).
- Section "11. Planner Event Persistence": Same location update — events are now persisted by the planner (flushed in `finally`, best-effort even on errors/cancellation), not the caller's HTTP layer.

**`docs/tools/statestore.md`** (line ~60-72):
- Under "Planner event storage" and "Sessions/tasks/steering/trajectories": Add notes that both are now handled automatically by the planner when a StateStore with the respective capabilities is provided (including trajectory persistence on `PlannerPause`).

**`docs/architecture/planning_orchestration/reactplanner_core.md`**:
- Section "2. Trajectory Management" (line ~21-29): Add a bullet about automatic persistence to StateStore on `PlannerFinish` and `PlannerPause`.
- Section "1. Planning Loop Flow" diagram (line ~299-324): Update the flow to show trajectory + event persistence after the result is produced.

**`REACT_PLANNER_INTEGRATION_GUIDE.md`** (at repo root, NOT in `docs/`) (line ~939):
- Update the "If you persist trajectories" note to reflect that the planner now auto-persists trajectories and events when a StateStore is provided.
- Add a section or note explaining that **orchestrators must propagate the caller's `trace_id` from `tool_context`** into the planner's `run()` / `resume()` call. The recommended pattern: `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`. Orchestrators that generate their own `trace_id` unconditionally will break persistence alignment with the caller.

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
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.state.in_memory import InMemoryStateStore

# 1. Define a trivial tool node
class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    answer: str


@tool(desc="echo")
async def echo(args: EchoArgs, ctx):
    return {"answer": args.text}

# 2. Build catalog + registry
registry = ModelRegistry()
registry.register("echo", EchoArgs, EchoOut)
catalog = build_catalog([Node(echo, name="echo")], registry)

# 3. Create a scripted LLM client that returns a finish action
# See tests/test_tool_background_mode.py:107 (ScriptedClient) for the full
# complete() signature accepted by the planner: (*, messages, response_format=None,
# stream=False, on_stream_chunk=None). The **_ shorthand below works equally well.
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

**Test scaffolding — how to create a planner that triggers PlannerPause:**

`pause_enabled=True` must be passed to the `ReactPlanner` constructor. Define a tool that calls `await ctx.pause(...)`, and a scripted client that routes to it:

```python
from typing import Any

from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerPause, ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.state.in_memory import InMemoryStateStore


class PauseArgs(BaseModel):
    prompt: str


class PauseOut(BaseModel):
    ok: bool


@tool(desc="Trigger a pause")
async def pause_tool(args: PauseArgs, ctx: Any):
    await ctx.pause("await_input", {"prompt": args.prompt})
    return PauseOut(ok=True)


class PauseClient:
    """Scripted LLM client that calls pause_tool on the first call, then finishes."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, messages, **_):
        self.calls += 1
        if self.calls == 1:
            return '{"thought":"pause","next_node":"pause_tool","args":{"prompt":"Confirm?"}}'
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


# Setup:
registry = ModelRegistry()
registry.register("pause_tool", PauseArgs, PauseOut)
catalog = build_catalog([Node(pause_tool, name="pause_tool")], registry)
store = InMemoryStateStore()
planner = ReactPlanner(
    llm_client=PauseClient(),
    catalog=catalog,
    max_iters=3,
    state_store=store,
    pause_enabled=True,  # REQUIRED for pause support
)

# Run — expect PlannerPause:
result = await planner.run("pause please", tool_context={"session_id": "s1", "trace_id": "t1"})
assert isinstance(result, PlannerPause)
await _drain_persistence_tasks()

# Assert trajectory was persisted (pause also persists trajectories):
traj = await store.get_trajectory("t1", "s1")
assert traj is not None
```

Valid pause reasons (from `PlannerPauseReason`): `"approval_required"`, `"await_input"`, `"external_event"`, `"constraints_conflict"`. See `tests/test_react_planner.py` line 1705+ for an existing pause/resume integration test pattern.

**Test scaffolding — how to test `resume()` persistence (full pause→resume flow):**

After `PlannerPause`, call `planner.resume()` with the `resume_token` from `PlannerPause.resume_token`. The `PauseClient` above returns a finish action on the second call, so resume completes with `PlannerFinish`. Then drain and assert:

```python
from penguiflow.planner import PlannerFinish, PlannerPause

# --- After the pause scaffolding above (planner, store, PauseClient already set up) ---

# Phase 1: Run → PlannerPause
pause_result = await planner.run("pause please", tool_context={"session_id": "s1", "trace_id": "t1"})
assert isinstance(pause_result, PlannerPause)
await _drain_persistence_tasks()

# Assert trajectory persisted on pause
traj = await store.get_trajectory("t1", "s1")
assert traj is not None

# Phase 2: Resume → PlannerFinish
# resume() takes the resume_token from PlannerPause, plus optional user_input.
# tool_context can be overridden or omitted (resume restores the paused tool_context).
resume_result = await planner.resume(
    pause_result.resume_token,
    user_input="confirmed",
    tool_context={"session_id": "s1", "trace_id": "t1"},  # re-inject IDs for persistence
)
assert isinstance(resume_result, PlannerFinish)
await _drain_persistence_tasks()

# Assert trajectory updated after resume (live trajectory now includes post-resume steps)
traj_after = await store.get_trajectory("t1", "s1")
assert traj_after is not None
assert len(traj_after.steps) > len(traj.steps)  # resume added more steps

# Assert events persisted for both phases
events = await store.list_planner_events("t1")
assert len(events) >= 2  # at least: step_start from run + resume event from resume
assert any(e.event_type == "resume" for e in events)
```

**Key detail:** `planner.resume()` accepts `tool_context` as an optional override. If omitted, the paused trajectory's `tool_context` is restored (which already contains `trace_id` and `session_id` from the initial `run()` call). However, explicitly re-passing `tool_context` with the same IDs is safer and mirrors what an HTTP layer would do. The resume signature is:

```python
async def resume(
    self,
    token: str,
    user_input: str | None = None,
    *,
    tool_context: Mapping[str, Any] | None = None,
    memory_key: MemoryKey | None = None,
    steering: SteeringInbox | None = None,
    tool_visibility: ToolVisibilityPolicy | None = None,
) -> PlannerFinish | PlannerPause:
```

**Test scaffolding — how to test event persistence on `run_loop()` errors:**

Monkeypatch `run_loop` at `penguiflow.planner.react_runtime.run_loop`. The stub must call `planner._emit_event()` before raising so the event buffer is populated:

```python
import asyncio

from penguiflow.planner import PlannerEvent
import penguiflow.planner.react_runtime as rt_mod


async def _failing_run_loop(planner, trajectory, **_):
    """Stub that buffers an event, then raises."""
    planner._emit_event(
        PlannerEvent(event_type="step_start", ts=1.0, trajectory_step=0)
    )
    raise RuntimeError("Simulated run_loop failure")


# In your test:
async def test_events_persisted_on_run_loop_error(monkeypatch):
    # ... set up planner with InMemoryStateStore as above ...

    monkeypatch.setattr(rt_mod, "run_loop", _failing_run_loop)

    with pytest.raises(RuntimeError, match="Simulated"):
        await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})

    await _drain_persistence_tasks()
    events = await store.list_planner_events("t1")
    assert len(events) >= 1
    assert events[0].event_type == "step_start"
```

**Test cases:**

- Test: planner with `InMemoryStateStore` and `tool_context={"session_id": "s1", "trace_id": "t1"}` → after `run()` + loop drain, `store.get_trajectory("t1", "s1")` returns the trajectory.
- Test: planner with `InMemoryStateStore` and `tool_context={"session_id": "s1", "trace_id": "t1"}` → after `run()` + loop drain, `store.list_planner_events("t1")` returns events.
- Test: planner with `InMemoryStateStore` that returns `PlannerPause` (using the pause scaffolding above) and `tool_context={"session_id": "s1", "trace_id": "t1"}` → after `run()` + loop drain, `store.get_trajectory("t1", "s1")` returns the (paused) trajectory.
- Test: planner without a state store → no error, no background tasks spawned.
- Test: planner with a state store lacking `save_trajectory` / `save_planner_event` → silently skipped, no error.
- Test: planner `resume()` also persists trajectory + events (same drain pattern).
- Test: state store `save_trajectory` raises → planner returns normally, error is logged (not propagated). Verify via `caplog` or mock logger.
- Test: state store `save_planner_event` raises → same: planner unaffected, warning logged.
- Test: `run()` persists buffered events when `run_loop()` raises (using the error scaffolding above) → monkeypatch `penguiflow.planner.react_runtime.run_loop`, catch the exception, drain tasks, then assert `store.list_planner_events("t1")` contains the emitted event(s).

## Exit criteria

**Baseline (verified 2026-02-27):** 2411 tests pass, 0 fail, 7 skipped. `ruff check .` clean. `mypy` clean (214 source files, 0 issues).

All of the following must pass before the implementation is considered complete:

1. **No regressions:** `uv run pytest` — the full test suite passes. The baseline is **2411 passed, 0 failed**. There are **no pre-existing test failures** — zero tests are allowed to fail after this migration. Every test that passed before must continue to pass (modulo the tests explicitly added by this plan in Section 6).
2. **Lint clean:** `uv run ruff check .` — zero lint errors in the `penguiflow/` directory and tests.
3. **Type check clean:** `uv run mypy` — zero type errors.
4. **Targeted tests pass:** `uv run pytest tests/planner/test_persistence.py tests/test_planner*.py -v` — all new and modified tests pass.
5. **Coverage threshold met:** `uv run pytest --cov=penguiflow --cov-report=term --cov-fail-under=84.5` — full suite with coverage at or above 84.5%.
6. **Docs build clean:** `uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict` — docs build passes with no warnings.
7. **ReactPlanner never generates trace_id:** The planner itself must never create, generate, or synthesise a `trace_id` or `session_id`. These are always passed in via `tool_context` by the caller. If `tool_context` is missing `trace_id` (or `session_id` for trajectories), persistence is **silently skipped** — no fallback generation (e.g., no `secrets.token_hex()`, no `uuid4()`). This is verified by the "planner without trace_id skips persistence" test case in `tests/planner/test_persistence.py`.
