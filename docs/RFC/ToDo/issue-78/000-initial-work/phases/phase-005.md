# Phase 005: New Planner Persistence Tests

## Objective
Create a new test file `tests/planner/test_persistence.py` with integration tests that verify the planner-level trajectory and event persistence added in Phases 0-1. These tests exercise the fire-and-forget background task pattern using real `ReactPlanner` instances with scripted LLM clients and `InMemoryStateStore`.

## Tasks
1. Create the `tests/planner/` directory and `__init__.py`.
2. Create `tests/planner/test_persistence.py` with 9 test cases.

## Detailed Steps

### Step 1: Create directory and `__init__.py`
- The `tests/planner/` directory does not currently exist.
- Create `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/` directory.
- Create `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/__init__.py` as an empty file (zero bytes, no content).

### Step 2: Create `test_persistence.py`
- Create `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/test_persistence.py` with the following test cases:

**Helper: `_drain_persistence_tasks()`**
Since persistence is fire-and-forget (`asyncio.create_task`), tests must drain the event loop after `run()`/`resume()` to let background tasks complete before asserting. Do NOT use `await asyncio.sleep(0)` -- a single yield is insufficient because background tasks have multiple await points (e.g., `InMemoryStateStore` methods use `async with self._lock:`). Instead, gather the named tasks explicitly.

**Test scaffolding -- basic planner setup:**
Use the `ReactPlanner` constructor directly with a scripted `llm_client`. Import `InMemoryStateStore` from `penguiflow.state.in_memory` (the canonical location), NOT from `penguiflow.cli.playground_state`.

**Test cases (9 total):**

1. **`test_run_persists_trajectory`**: Planner with `InMemoryStateStore` and `tool_context={"session_id": "s1", "trace_id": "t1"}` -- after `run()` + drain, `store.get_trajectory("t1", "s1")` returns a trajectory.

2. **`test_run_persists_events`**: Same setup -- after `run()` + drain, `store.list_planner_events("t1")` returns events (at least 1).

3. **`test_pause_persists_trajectory`**: Planner with `pause_enabled=True` using the pause scaffolding (a tool that calls `ctx.pause(...)` and a scripted client that routes to it) -- after `run()` returns `PlannerPause` + drain, `store.get_trajectory("t1", "s1")` returns a trajectory.

4. **`test_no_state_store_no_error`**: Planner without a state store -- `run()` completes normally with no error and no background tasks spawned.

5. **`test_missing_methods_silently_skipped`**: Planner with a state store object that lacks `save_trajectory` / `save_planner_event` -- `run()` completes normally, no error.

6. **`test_resume_persists_trajectory_and_events`**: Full pause->resume flow. Run -> PlannerPause -> drain -> assert trajectory. Resume with `resume_token` -> PlannerFinish -> drain -> assert trajectory updated (more steps) and events include a "resume" event.

7. **`test_trajectory_persistence_failure_logged`**: State store whose `save_trajectory` raises -- planner returns normally, warning is logged. Use `caplog` fixture.

8. **`test_event_persistence_failure_logged`**: State store whose `save_planner_event` raises -- planner returns normally, warning is logged. Use `caplog` fixture.

9. **`test_events_persisted_on_run_loop_error`**: Monkeypatch `run_loop` at `penguiflow.planner.react_runtime.run_loop`. The stub calls `planner._emit_event()` before raising. Catch the exception, drain tasks, then assert `store.list_planner_events("t1")` contains the emitted event(s).

## Required Code

```python
# Target file: tests/planner/__init__.py
# (empty file -- zero bytes)
```

```python
# Target file: tests/planner/test_persistence.py
"""Tests for planner-level trajectory and event persistence (fire-and-forget)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause, ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.state.in_memory import InMemoryStateStore
import penguiflow.planner.react_runtime as rt_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _drain_persistence_tasks() -> None:
    """Wait for all fire-and-forget persistence tasks to complete."""
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("penguiflow-persist")]
    if tasks:
        await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Tool definitions for test scaffolding
# ---------------------------------------------------------------------------

class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    answer: str


@tool(desc="echo")
async def echo(args: EchoArgs, ctx: Any) -> dict[str, str]:
    return {"answer": args.text}


class PauseArgs(BaseModel):
    prompt: str


class PauseOut(BaseModel):
    ok: bool


@tool(desc="Trigger a pause")
async def pause_tool(args: PauseArgs, ctx: Any) -> PauseOut:
    await ctx.pause("await_input", {"prompt": args.prompt})
    return PauseOut(ok=True)


# ---------------------------------------------------------------------------
# Scripted LLM clients
# ---------------------------------------------------------------------------

class ScriptedClient:
    async def complete(self, *, messages: Any, **_: Any) -> str:
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


class PauseClient:
    """Scripted LLM client that calls pause_tool on the first call, then finishes."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, messages: Any, **_: Any) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"thought":"pause","next_node":"pause_tool","args":{"prompt":"Confirm?"}}'
        return '{"thought":"done","next_node":null,"args":{"raw_answer":"ok"}}'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_echo_planner(store: Any) -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    return ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
        state_store=store,
    )


def _make_pause_planner(store: Any) -> tuple[ReactPlanner, PauseClient]:
    registry = ModelRegistry()
    registry.register("pause_tool", PauseArgs, PauseOut)
    catalog = build_catalog([Node(pause_tool, name="pause_tool")], registry)
    client = PauseClient()
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=3,
        state_store=store,
        pause_enabled=True,
    )
    return planner, client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_run_persists_trajectory() -> None:
    store = InMemoryStateStore()
    planner = _make_echo_planner(store)
    result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None
    assert traj.query == "hi"


async def test_run_persists_events() -> None:
    store = InMemoryStateStore()
    planner = _make_echo_planner(store)
    await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
    await _drain_persistence_tasks()

    events = await store.list_planner_events("t1")
    assert len(events) >= 1


async def test_pause_persists_trajectory() -> None:
    store = InMemoryStateStore()
    planner, _ = _make_pause_planner(store)
    result = await planner.run("pause please", tool_context={"session_id": "s1", "trace_id": "t1"})
    assert isinstance(result, PlannerPause)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None


async def test_no_state_store_no_error() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
    )
    result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()
    # No assertions on store -- just verifying no error occurred


async def test_missing_methods_silently_skipped() -> None:
    class BareStore:
        """A store that has neither save_trajectory nor save_planner_event."""
        pass

    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        max_iters=2,
        state_store=BareStore(),
    )
    result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
    assert isinstance(result, PlannerFinish)
    await _drain_persistence_tasks()


async def test_resume_persists_trajectory_and_events() -> None:
    store = InMemoryStateStore()
    planner, _ = _make_pause_planner(store)

    # Phase 1: Run -> PlannerPause
    pause_result = await planner.run(
        "pause please", tool_context={"session_id": "s1", "trace_id": "t1"}
    )
    assert isinstance(pause_result, PlannerPause)
    await _drain_persistence_tasks()

    traj = await store.get_trajectory("t1", "s1")
    assert traj is not None

    # Phase 2: Resume -> PlannerFinish
    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="confirmed",
        tool_context={"session_id": "s1", "trace_id": "t1"},
    )
    assert isinstance(resume_result, PlannerFinish)
    await _drain_persistence_tasks()

    # Trajectory updated after resume (more steps)
    traj_after = await store.get_trajectory("t1", "s1")
    assert traj_after is not None
    assert len(traj_after.steps) > len(traj.steps)

    # Events persisted for both phases
    events = await store.list_planner_events("t1")
    assert len(events) >= 2
    assert any(e.event_type == "resume" for e in events)


async def test_trajectory_persistence_failure_logged(caplog: pytest.LogCaptureFixture) -> None:
    class FailingStore:
        async def save_trajectory(self, *_: Any) -> None:
            raise RuntimeError("disk full")

        async def save_planner_event(self, *_: Any) -> None:
            pass

    store = FailingStore()
    planner = _make_echo_planner(store)

    with caplog.at_level(logging.WARNING, logger="penguiflow.planner"):
        result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
        assert isinstance(result, PlannerFinish)
        await _drain_persistence_tasks()

    assert any("Background trajectory persistence failed" in r.message for r in caplog.records)


async def test_event_persistence_failure_logged(caplog: pytest.LogCaptureFixture) -> None:
    class FailingEventStore:
        async def save_trajectory(self, *_: Any) -> None:
            pass

        async def save_planner_event(self, *_: Any) -> None:
            raise RuntimeError("disk full")

    store = FailingEventStore()
    planner = _make_echo_planner(store)

    with caplog.at_level(logging.WARNING, logger="penguiflow.planner"):
        result = await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})
        assert isinstance(result, PlannerFinish)
        await _drain_persistence_tasks()

    assert any("Background planner-event persistence failed" in r.message for r in caplog.records)


async def test_events_persisted_on_run_loop_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_run_loop(planner: Any, trajectory: Any, **_: Any) -> None:
        """Stub that buffers an event, then raises."""
        planner._emit_event(
            PlannerEvent(event_type="step_start", ts=1.0, trajectory_step=0)
        )
        raise RuntimeError("Simulated run_loop failure")

    store = InMemoryStateStore()
    planner = _make_echo_planner(store)

    monkeypatch.setattr(rt_mod, "run_loop", _failing_run_loop)

    with pytest.raises(RuntimeError, match="Simulated"):
        await planner.run("hi", tool_context={"session_id": "s1", "trace_id": "t1"})

    await _drain_persistence_tasks()
    events = await store.list_planner_events("t1")
    assert len(events) >= 1
    assert events[0].event_type == "step_start"
```

## Exit Criteria (Success)
- [ ] `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/__init__.py` exists (empty file).
- [ ] `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/test_persistence.py` exists with 9 test functions.
- [ ] `uv run ruff check tests/planner/test_persistence.py` reports zero errors.
- [ ] `uv run pytest tests/planner/test_persistence.py -v` -- all 9 tests pass.

## Implementation Notes
- Import `InMemoryStateStore` from `penguiflow.state.in_memory` (the canonical location), NOT from `penguiflow.cli.playground_state`.
- `pytest-asyncio` with `asyncio_mode = "auto"` means async test functions run automatically without `@pytest.mark.asyncio`.
- `InMemoryStateStore.save_trajectory` uses `self._trajectories[trace_id] = ...` (dict keyed by `trace_id`), so calling it twice with the same `(trace_id, session_id)` overwrites. The `len(traj_after.steps) > len(traj.steps)` assertion in the resume test is valid because the resumed trajectory accumulates steps from both phases.
- For the "missing methods" test, use a bare class with neither `save_trajectory` nor `save_planner_event` -- the duck-typing (`getattr`) in the helpers will return `None` and skip silently.
- For the "error" test, monkeypatch `run_loop` at `penguiflow.planner.react_runtime.run_loop`. The stub must call `planner._emit_event()` before raising so the event buffer is populated.
- Depends on Phases 0-1 (the planner persistence infrastructure must exist).
- These tests verify the planner's behavior independent of the playground wrapper.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check tests/planner/test_persistence.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/planner/test_persistence.py -v
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- Created `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/` directory
- Created `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/__init__.py` as an empty file
- Created `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/test_persistence.py` with all 9 test cases specified in the plan
- All 9 tests pass, ruff reports zero errors

### Key Considerations
- **Import ordering**: The phase plan placed `import penguiflow.planner.react_runtime as rt_mod` after the `from penguiflow...` imports. Ruff's isort rule (I001) requires bare `import` statements to precede `from ... import` statements within the same first-party group. Moved the bare import before the `from` imports. Additionally, ruff required only a single blank line between the import block and the first code section (the plan had two blank lines). Reduced to one blank line.
- **`ReactPlanner` constructor**: Confirmed that `ReactPlanner.__init__` accepts `state_store` directly as a keyword argument. No need for `init_react_planner` or manual `planner._state_store` assignment.
- **`_drain_persistence_tasks()`**: Implemented exactly as specified -- gathers all `asyncio.Task` objects whose names start with `"penguiflow-persist"`, matching the named tasks (`penguiflow-persist-trajectory` and `penguiflow-persist-events`) created in the react_runtime `_fire_persistence_tasks` function.

### Assumptions
- The persistence infrastructure from Phases 0-1 is already in place and functioning (confirmed by inspecting `penguiflow/planner/react_runtime.py` which contains `_persist_trajectory`, `_persist_events`, and `_fire_persistence_tasks` with the expected named task pattern).
- The warning log messages "Background trajectory persistence failed" and "Background planner-event persistence failed" are emitted by the existing `_persist_trajectory` and `_persist_events` helper functions when exceptions occur. This was confirmed in the codebase.

### Deviations from Plan
1. **`PauseClient` scripted responses**: The plan's `PauseClient` returned a finish action (`next_node: null`) on call 2 (after resume). However, a finish action does NOT append a `TrajectoryStep` to the trajectory -- it returns a `PlannerFinish` directly. This meant `len(traj_after.steps) > len(traj.steps)` would fail (both equal 1). To fix this, `PauseClient` was modified to return an `echo` tool call on call 2 (which adds a step to the trajectory), and then finish on call 3. Correspondingly:
   - The `_make_pause_planner` function now also registers the `echo` tool (both in the registry and catalog) so the planner can route to it.
   - `max_iters` was increased from 3 to 4 to accommodate the additional tool call after resume.
   - The `PauseClient` docstring was updated to reflect the new 3-call sequence.
2. **Import order and blank lines**: Adjusted to satisfy ruff's isort rule (see Key Considerations above). This is a formatting-only deviation from the literal code in the plan.

### Potential Risks & Reviewer Attention Points
- The `_drain_persistence_tasks` helper relies on the naming convention of background tasks (`"penguiflow-persist*"`). If this naming convention changes in the runtime, these tests would silently stop draining and could produce flaky failures.
- The `test_resume_persists_trajectory_and_events` test depends on the `echo` tool call after resume producing a new `TrajectoryStep`. If the planner's step-counting semantics change (e.g., finish actions start creating steps), the test would still pass but the deviation from the plan's original design would become unnecessary.
- The tests do not verify the exact number of events or trajectory steps beyond minimum bounds, which makes them resilient to changes in event emission patterns but less strict as regression tests.

### Files Modified
- **Created**: `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/__init__.py` (empty)
- **Created**: `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/planner/test_persistence.py` (9 test cases)
- **Modified**: `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-78/000-initial-work/phases/phase-005.md` (appended implementation notes)
