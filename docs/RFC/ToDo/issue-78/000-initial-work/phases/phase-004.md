# Phase 004: Documentation Updates

## Objective
Update five documentation files to reflect that trajectory and event persistence has moved from the playground wrapper into the planner core (`react_runtime.py`). Add guidance about orchestrator `trace_id` propagation for custom orchestrators.

## Tasks
1. Update `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md` sections 10 and 11.
2. Update `docs/tools/statestore.md` with automatic persistence notes.
3. Update `docs/architecture/planning_orchestration/reactplanner_core.md` with persistence details.
4. Update `docs/PLAYGROUND_BACKEND_CONTRACTS.md` with planner persistence notes.
5. Update `REACT_PLANNER_INTEGRATION_GUIDE.md` with auto-persistence and orchestrator guidance.

## Detailed Steps

### Step 1: Update `STATESTORE_IMPLEMENTATION_SPEC.md`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`.
- Find section "10. Trajectory Persistence" (around line 807). Locate the line `**Location (integration):**` and change the path from `penguiflow/cli/playground.py` (or similar playground reference) to `penguiflow/planner/react_runtime.py`.
- Add a note that the planner now persists trajectories directly after `run()` / `resume()` returns (`PlannerFinish` or `PlannerPause`), as fire-and-forget background tasks.
- Find section "11. Planner Event Persistence". Apply the same location update -- events are now persisted by the planner (flushed in `finally`, best-effort even on errors/cancellation), not the playground.

### Step 2: Update `statestore.md`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/statestore.md`.
- Find the section about "Planner event storage" (around line 60-72).
- Add a note that planner events are now automatically persisted by the `ReactPlanner` when a StateStore with `save_planner_event` capability is provided. Events are buffered during execution and flushed as a fire-and-forget background task on every exit path (finish, pause, error, cancel).
- Find the section about "Sessions/tasks/steering/trajectories".
- Add a note that trajectories are now automatically persisted by the `ReactPlanner` when a StateStore with `save_trajectory` capability is provided. Persistence happens on both `PlannerFinish` and `PlannerPause`.

### Step 3: Update `reactplanner_core.md`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/architecture/planning_orchestration/reactplanner_core.md`.
- In section "2. Trajectory Management" (around line 21-29), add a bullet point about automatic persistence:
  - The `ReactPlanner` automatically persists the live `Trajectory` object to the StateStore (if available) on `PlannerFinish` and `PlannerPause`. This is fire-and-forget -- persistence failures are logged but never propagate to the caller.
- In section "1. Planning Loop Flow" diagram (around line 299-324), update the flow to show that after `run_loop()` returns (in the `finally` block), trajectory and event persistence tasks are spawned as background tasks. This happens before `_maybe_record_memory_turn`.

### Step 4: Update `PLAYGROUND_BACKEND_CONTRACTS.md`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/PLAYGROUND_BACKEND_CONTRACTS.md`.
- Find the section discussing `session_id` and `trace_id` in `tool_context` (around line 112-114).
- Add a note that these IDs are now used by the `ReactPlanner` for automatic trajectory and event persistence -- the playground wrapper no longer saves these itself.
- Add a note that orchestrators must propagate this `tool_context` into their internal `ReactPlanner` calls so that persistence uses the frontend `run_id` (`trace_id_hint`) as the source of truth.

### Step 5: Update `REACT_PLANNER_INTEGRATION_GUIDE.md`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md`.
- Find the section referencing trajectory persistence via the Playground state store (around line 939) -- text like "If you persist trajectories (e.g., via the Playground state store)".
- Update it to reflect that the planner now auto-persists trajectories and events when a StateStore is provided. The caller only needs to ensure `trace_id` and `session_id` are present in `tool_context`.
- Add a new section or note explaining the orchestrator `trace_id` propagation requirement:
  - Orchestrators must propagate the caller's `trace_id` from `tool_context` into the planner's `run()` / `resume()` call.
  - Recommended pattern: `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
  - Orchestrators that generate their own `trace_id` unconditionally will break persistence alignment with the frontend.
  - Projects generated from older templates need manual update of the `trace_id = secrets.token_hex(8)` line in their orchestrator.

## Exit Criteria (Success)
- [ ] `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md` sections 10 and 11 reference `penguiflow/planner/react_runtime.py` as the persistence location.
- [ ] `docs/tools/statestore.md` mentions automatic planner-level persistence for both events and trajectories.
- [ ] `docs/architecture/planning_orchestration/reactplanner_core.md` section 2 mentions automatic trajectory persistence on `PlannerFinish` and `PlannerPause`.
- [ ] `docs/PLAYGROUND_BACKEND_CONTRACTS.md` notes that `trace_id` and `session_id` in `tool_context` drive planner-level persistence.
- [ ] `REACT_PLANNER_INTEGRATION_GUIDE.md` documents the orchestrator `trace_id` propagation pattern.
- [ ] `uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict` passes with no warnings.

## Implementation Notes
- This phase has no code changes -- only documentation updates.
- The documentation should be factual and concise. Do not over-explain.
- Preserve existing document structure and formatting conventions.
- This phase is independent of test phases (5 and 6) and can be done in any order relative to them.
- Depends conceptually on Phases 0-3 (documents the changes made there), but the files are independent so no code dependency.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict
```
