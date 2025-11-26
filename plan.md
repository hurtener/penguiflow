# PenguiFlow v2.4  API Refinement & Production Hardening

## Overview

Version 2.4 focuses on **API clarity**, **developer experience**, and **documentation completeness**. This release addresses accumulated technical debt before broader agent migration, ensuring a stable foundation for production deployments.

### Goals

1. **Clean API Surface**: Eliminate confusion between context types, remove deprecated parameters
2. **Type Safety**: Proper typed context for tools, eliminating `ctx: Any`
3. **Explicit over Implicit**: Remove magic field injection, make parallel join explicit
4. **Maintainable Codebase**: Break up monolithic modules, improve separation of concerns
5. **Documentation Parity**: Every feature documented with examples before release
6. **Example Quality**: All examples use current best practices, no deprecated APIs

### Non-Goals

- No new major features (focus on refinement)
- No breaking changes to core flow execution (only planner APIs)
- No external dependencies added

---

## Current State Assessment

### Issues to Address

| Issue | Severity | Phase |
|-------|----------|-------|
| `_SerializableContext` workaround needed | High | 1 |
| `ctx: Any` in all tools | High | 2 |
| Magic join field injection | Medium | 3 |
| `react.py` monolith (2500+ lines) | Medium | 4 |
| Deprecated `context_meta` in examples | High | 5 |
| Missing documentation for features | High | 5 |
| Confusing context terminology | Medium | 5 |

### Files Requiring Changes

```
penguiflow/
  planner/
    react.py          # Split into modules (Phase 4)
    context.py        # NEW: ToolContext type (Phase 2)
    parallel.py       # NEW: Parallel execution logic (Phase 4)
    trajectory.py     # NEW: Trajectory management (Phase 4)
    join.py           # NEW: Explicit join configuration (Phase 3)
  types.py            # Add ToolContext protocol (Phase 2)

examples/
  planner_enterprise_agent_v2/  # Update all deprecated APIs (Phase 5)
  react_memory_context/          # Update context patterns (Phase 5)
  react_pause_resume/            # Verify current (Phase 5)
  NEW: react_parallel_join/      # Explicit join example (Phase 5)

docs/
  REACT_PLANNER_INTEGRATION_GUIDE.md  # Already updated, verify (Phase 5)
  MIGRATION_V24.md                     # NEW: Migration guide (Phase 5)
```

---

## Phase 1  Context API Separation

**Goal**: Eliminate the `_SerializableContext` workaround by providing first-class API separation.

### Problem

Currently, users must merge LLM-visible and tool-only context, then wrap in `_SerializableContext`:

```python
# Current (confusing)
combined = {**llm_visible, **tool_only}
safe = _SerializableContext(combined)
result = await planner.run(query=query, llm_context=safe)
```

### Solution

Introduce explicit `tool_context` parameter alongside `llm_context`:

```python
# New (clear)
result = await planner.run(
    query=query,
    llm_context={"memories": [...], "preferences": {...}},  # Sent to LLM
    tool_context={"trace_id": "...", "publisher": fn},       # Tools only
)
```

### Deliverables

1. **Add `tool_context` parameter to `ReactPlanner.run()`**
   - `llm_context`: JSON-serializable, included in LLM prompt
   - `tool_context`: Any objects, only accessible via `ctx.tool_context`
   - Validation: `llm_context` must be JSON-serializable (fail fast)

2. **Update `_PlannerContext`**
   ```python
   class _PlannerContext:
       @property
       def llm_context(self) -> dict[str, Any]:
           """Context visible to LLM (read-only view)."""

       @property
       def tool_context(self) -> dict[str, Any]:
           """Tool-only context (callbacks, loggers, etc.)."""

       @property
       def meta(self) -> dict[str, Any]:
           """Combined context (deprecated, for backward compat)."""
   ```

3. **Deprecate `_SerializableContext`**
   - Keep for one version with deprecation warning
   - Document migration path

4. **Update `prompts.build_user_prompt()`**
   - Only receives `llm_context` (never tool_context)
   - Add JSON serialization validation

### Acceptance Criteria

- [ ] `tool_context` parameter accepted by `run()` and `resume()`
- [ ] `ctx.tool_context` accessible in tools
- [ ] `ctx.llm_context` accessible in tools (read-only)
- [ ] `ctx.meta` still works (deprecated warning)
- [ ] Passing non-serializable in `llm_context` raises `TypeError` immediately
- [ ] Tests cover all new paths
- [ ] Backward compatible: old code still works with deprecation warnings

### Migration Example

```python
# Before (v2.3)
context = _SerializableContext({
    "memories": memories,           # LLM sees
    "status_publisher": publisher,  # LLM shouldn't see
})
result = await planner.run(query=q, llm_context=context)

# After (v2.4)
result = await planner.run(
    query=q,
    llm_context={"memories": memories},
    tool_context={"status_publisher": publisher},
)
```

---

## Phase 2  Typed Tool Context

**Goal**: Replace `ctx: Any` with a proper typed protocol for IDE support and type checking.

### Problem

```python
# Current - no type hints, no autocomplete
async def my_tool(args: MyArgs, ctx: Any) -> MyResult:
    meta = ctx.meta  # Hope this exists!
    await ctx.pause(...)  # Is this even a method?
```

### Solution

Introduce `ToolContext` protocol:

```python
# New - full type support
from penguiflow.planner import ToolContext

async def my_tool(args: MyArgs, ctx: ToolContext) -> MyResult:
    trace_id = ctx.tool_context.get("trace_id")  # Autocomplete works!
    await ctx.pause("await_input", {"q": "?"})    # Type checked!
```

### Deliverables

1. **Create `penguiflow/planner/context.py`**
   ```python
   from typing import Protocol, Any, Mapping

   class ToolContext(Protocol):
       """Protocol for tool execution context.

       This is the typed interface available to all planner tools.
       """

       @property
       def llm_context(self) -> Mapping[str, Any]:
           """Context visible to LLM (read-only)."""
           ...

       @property
       def tool_context(self) -> dict[str, Any]:
           """Tool-only context (callbacks, telemetry, etc.)."""
           ...

       @property
       def meta(self) -> dict[str, Any]:
           """Combined context. Deprecated: use llm_context/tool_context."""
           ...

       async def pause(
           self,
           reason: PlannerPauseReason,
           payload: Mapping[str, Any] | None = None,
       ) -> None:
           """Pause execution for human input."""
           ...

       def emit_chunk(
           self,
           text: str,
           *,
           stream_id: str | None = None,
           done: bool = False,
           meta: dict[str, Any] | None = None,
       ) -> None:
           """Emit a streaming chunk."""
           ...
   ```

2. **Export from `penguiflow.planner`**
   ```python
   from penguiflow.planner import ToolContext
   ```

3. **Update `_PlannerContext` to satisfy protocol**

4. **Create helper type for flow context compatibility**
   ```python
   from typing import Union
   from penguiflow.planner import ToolContext
   from penguiflow.core import Context

   # For tools that work in both flow and planner
   AnyContext = Union[ToolContext, Context]
   ```

### Acceptance Criteria

- [ ] `ToolContext` protocol defined and exported
- [ ] `_PlannerContext` implements `ToolContext`
- [ ] mypy passes with `ctx: ToolContext` annotations
- [ ] IDE autocomplete works for all methods
- [ ] Documentation updated with type hints
- [ ] Helper functions updated to use `ToolContext | Any` for backward compat

---

## Phase 3  Explicit Join Configuration

**Goal**: Replace magic field name injection with explicit, discoverable configuration.

### Problem

```python
# Current - magic field names
class JoinArgs(BaseModel):
    results: list[T]  # Magically injected if named "results"
    expect: int       # Magically injected if named "expect"
    # What if I name it "data"? Nothing happens. Surprise!
```

### Solution

Explicit join configuration in the action:

```python
# New - explicit mapping
{
    "plan": [...],
    "join": {
        "node": "merge_results",
        "inject": {
            "branch_results": "$results",     # Explicit: inject results into this field
            "total_branches": "$expect",       # Explicit: inject count into this field
            "all_outcomes": "$branches",       # Explicit: inject all branch data
        },
        "args": {
            "custom_param": "value"            # Additional static args
        }
    }
}
```

### Deliverables

1. **Extend `ParallelJoin` schema**
   ```python
   class JoinInjection(BaseModel):
       """Mapping of target field -> injection source."""
       # Target field name -> source (prefixed with $)
       # Sources: $results, $expect, $branches, $failures, $success_count, $failure_count
       mapping: dict[str, str] = Field(default_factory=dict)

   class ParallelJoin(BaseModel):
       node: str
       args: dict[str, Any] = Field(default_factory=dict)
       inject: JoinInjection | None = None  # NEW: explicit injection config
   ```

2. **Update parallel execution logic**
   - If `inject` is provided, use explicit mapping
   - If `inject` is None, fall back to magic names (backward compat, with deprecation warning)
   - Log warning when using magic injection: "Implicit join injection is deprecated. Use explicit 'inject' mapping."

3. **Update prompts to explain injection**
   - System prompt should document available injection sources
   - Error messages should suggest using `inject`

4. **Create `penguiflow/planner/join.py`**
   - Extract join logic from `react.py`
   - Clean implementation with explicit injection

### Acceptance Criteria

- [ ] `inject` field accepted in `ParallelJoin`
- [ ] Explicit mapping works correctly
- [ ] Magic injection still works with deprecation warning
- [ ] Prompts updated with injection documentation
- [ ] Tests cover explicit and implicit injection
- [ ] Error messages guide users to explicit injection

### Example

```python
# Before (magic)
class MergeArgs(BaseModel):
    results: list[T]  # Must be named exactly "results"

# After (explicit)
class MergeArgs(BaseModel):
    branch_outputs: list[T]  # Can be named anything

# LLM action:
{
    "plan": [...],
    "join": {
        "node": "merge",
        "inject": {"branch_outputs": "$results"}
    }
}
```

---

## Phase 4  Modularize react.py

**Goal**: Break the 2500+ line monolith into focused, maintainable modules.

### Current Structure

```
react.py (2500+ lines)
   PlannerAction, PlannerPause, PlannerFinish (models)
   _PlannerContext (context)
   Trajectory, TrajectoryStep (state)
   _ConstraintTracker (budgets)
   _PlanningHints (hints)
   _BranchExecutionResult (parallel)
   ReactPlanner (main class)
      __init__, run, resume
      _run_loop (main loop)
      _execute_parallel_plan (parallel)
      _run_parallel_branch (parallel)
      _build_messages (prompts)
      _check_action_constraints (constraints)
      _record_pause, _load_pause_record (pause)
      ... many more
   Helper functions
```

### Target Structure

```
penguiflow/planner/
   __init__.py           # Public exports
   react.py              # ReactPlanner class (slim coordinator)
   models.py             # PlannerAction, PlannerPause, PlannerFinish, etc.
   context.py            # ToolContext protocol, _PlannerContext (Phase 2)
   trajectory.py         # Trajectory, TrajectoryStep, serialization
   constraints.py        # _ConstraintTracker, budget/deadline logic
   hints.py              # _PlanningHints, hint parsing
   parallel.py           # Parallel execution, branching, joining
   pause.py              # Pause/resume logic, state storage
   llm.py                # LLM interaction, message building
   prompts.py            # (existing) prompt templates
```

### Deliverables

1. **Create `models.py`**
   - Move: `PlannerAction`, `ParallelCall`, `ParallelJoin`, `PlannerPause`, `PlannerFinish`
   - Move: `PlannerPauseReason`, `PlannerEvent`

2. **Create `trajectory.py`**
   - Move: `Trajectory`, `TrajectoryStep`
   - Move: Serialization/deserialization logic

3. **Create `constraints.py`**
   - Move: `_ConstraintTracker`
   - Move: Budget and deadline enforcement

4. **Create `hints.py`**
   - Move: `_PlanningHints`
   - Move: Hint parsing from dict/config

5. **Create `parallel.py`**
   - Move: `_BranchExecutionResult`
   - Move: `_execute_parallel_plan`, `_run_parallel_branch`
   - Move: Join logic (from Phase 3)

6. **Create `pause.py`**
   - Move: `_PauseRecord`
   - Move: `_record_pause`, `_load_pause_record`
   - Move: State store integration

7. **Create `llm.py`**
   - Move: `_build_messages`, `_call_llm`
   - Move: Response parsing, repair logic

8. **Slim down `react.py`**
   - Keep: `ReactPlanner` class
   - Coordinator that imports from other modules
   - Target: < 500 lines

### Acceptance Criteria

- [ ] All modules created with clear responsibilities
- [ ] No circular imports
- [ ] All tests pass without modification
- [ ] `react.py` under 500 lines
- [ ] Public API unchanged (imports from `penguiflow.planner` work)
- [ ] mypy passes on new module structure

### Import Structure

```python
# Public API (unchanged)
from penguiflow.planner import (
    ReactPlanner,
    PlannerPause,
    PlannerFinish,
    PlannerEvent,
    ToolContext,  # NEW from Phase 2
)

# Internal imports (new)
from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory
from penguiflow.planner.parallel import execute_parallel_plan
```

---

## Phase 5  Documentation & Examples Update

**Goal**: Every feature documented, all examples using best practices.

### Documentation Updates

1. **REACT_PLANNER_INTEGRATION_GUIDE.md**
   - [x] Already updated with parallel execution
   - [ ] Update Section 4 for new `tool_context` parameter
   - [ ] Update Section 5 orchestrator example
   - [ ] Update Section 11 for explicit join injection
   - [ ] Add deprecation notices for `_SerializableContext`, `ctx.meta`

2. **Create MIGRATION_V24.md**
   ```markdown
   # Migrating to PenguiFlow v2.4

   ## Breaking Changes
   - None (fully backward compatible)

   ## Deprecations
   - `context_meta` parameter ’ use `llm_context`
   - `_SerializableContext` ’ use separate `llm_context` + `tool_context`
   - `ctx.meta` ’ use `ctx.llm_context` or `ctx.tool_context`
   - Magic join field injection ’ use explicit `inject` mapping

   ## Migration Steps
   1. Update `planner.run()` calls...
   2. Update tool signatures...
   3. Update join configurations...
   ```

3. **Update CLAUDE.md**
   - Add v2.4 to version history
   - Update any outdated patterns

4. **API Reference** (if exists)
   - Document `ToolContext` protocol
   - Document new parameters

### Example Updates

1. **planner_enterprise_agent_v2/**
   - [ ] Replace `context_meta` with `llm_context` + `tool_context`
   - [ ] Remove `_SerializableContext` usage
   - [ ] Update tool signatures to use `ToolContext`
   - [ ] Add explicit join injection if using parallel
   - [ ] Verify all tests pass

2. **react_memory_context/**
   - [ ] Update to new context pattern
   - [ ] Add `ToolContext` type hints

3. **react_pause_resume/**
   - [ ] Verify current patterns
   - [ ] Update if needed

4. **NEW: react_parallel_join/**
   - [ ] Create example demonstrating:
     - Parallel fan-out
     - Explicit join injection
     - Failure handling
     - Partial result processing
   - [ ] Include README.md

5. **NEW: react_typed_tools/**
   - [ ] Create example demonstrating:
     - `ToolContext` protocol usage
     - Proper type hints throughout
     - IDE autocomplete benefits

### Acceptance Criteria

- [ ] All deprecated APIs have warnings in docs
- [ ] Migration guide complete with before/after examples
- [ ] All examples run without deprecation warnings
- [ ] All examples use `ToolContext` type hints
- [ ] New examples created for new features
- [ ] README updated with v2.4 highlights

---

## Phase 6  Cleanup & Release

**Goal**: Final polish, remove deprecated code paths, release v2.4.

### Tasks

1. **Code Cleanup**
   - Remove `# type: ignore` comments where possible
   - Fix any remaining mypy issues
   - Run ruff with strict settings

2. **Test Coverage**
   - Ensure e85% coverage maintained
   - Add edge case tests for new features
   - Add deprecation warning tests

3. **Performance Validation**
   - Benchmark parallel execution
   - Ensure no regression from modularization
   - Profile memory usage

4. **Release Checklist**
   - [ ] All phases complete
   - [ ] All tests pass (Python 3.11, 3.12, 3.13)
   - [ ] Coverage e85%
   - [ ] No mypy errors
   - [ ] No ruff errors
   - [ ] CHANGELOG.md updated
   - [ ] Version bumped to 2.4.0
   - [ ] Git tag created

### Deprecation Timeline

| Deprecated | Warning in | Removed in |
|------------|------------|------------|
| `context_meta` | v2.3 | v2.5 |
| `_SerializableContext` | v2.4 | v2.6 |
| `ctx.meta` (direct access) | v2.4 | v2.6 |
| Magic join injection | v2.4 | v2.6 |

---

## Implementation Order

```
Phase 1: Context API Separation     [~3 days]
    “
Phase 2: Typed Tool Context         [~2 days]
    “
Phase 3: Explicit Join Config       [~2 days]
    “
Phase 4: Modularize react.py        [~4 days]
    “
Phase 5: Docs & Examples            [~3 days]
    “
Phase 6: Cleanup & Release          [~2 days]

Total: ~16 days of focused work
```

---

## Success Metrics

1. **Developer Experience**
   - IDE autocomplete works for tool context
   - No more "what fields are injected?" confusion
   - Clear separation of concerns in API

2. **Code Quality**
   - No file over 500 lines in planner module
   - All public APIs typed
   - e85% test coverage

3. **Documentation**
   - Every feature in guide has working example
   - Migration path clear for existing users
   - No deprecated APIs in examples

4. **Stability**
   - All existing tests pass
   - No breaking changes for valid v2.3 code
   - Deprecation warnings guide migration

---

## Appendix: File Changes Summary

### New Files
```
penguiflow/planner/context.py
penguiflow/planner/models.py
penguiflow/planner/trajectory.py
penguiflow/planner/constraints.py
penguiflow/planner/hints.py
penguiflow/planner/parallel.py
penguiflow/planner/pause.py
penguiflow/planner/llm.py
penguiflow/planner/join.py
examples/react_parallel_join/
examples/react_typed_tools/
docs/MIGRATION_V24.md
```

### Modified Files
```
penguiflow/planner/__init__.py    # Export ToolContext, new structure
penguiflow/planner/react.py       # Slim coordinator
penguiflow/planner/prompts.py     # Join injection docs
examples/planner_enterprise_agent_v2/*
examples/react_memory_context/*
REACT_PLANNER_INTEGRATION_GUIDE.md
CLAUDE.md
CHANGELOG.md
pyproject.toml (version bump)
```

### Deleted Files
```
(none - backward compatibility maintained)
```
