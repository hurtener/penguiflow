# Phase 003: Orchestrator Wrapper Trace ID Propagation and Template Fix

## Objective
Update `OrchestratorAgentWrapper.chat()` and `OrchestratorAgentWrapper.resume()` to pre-compute `trace_id` and inject it into `tool_ctx` before calling the orchestrator, and fix the orchestrator Jinja template to respect caller-provided `trace_id` from `tool_context`. This ensures the planner's persistence uses the frontend's `run_id` (`trace_id_hint`) as the source of truth, not a freshly generated one.

**CRITICAL:** The wrapper changes and template fix are co-dependent. The wrapper injects `trace_id` into `tool_ctx`, but the template currently merges `base_tool_context` last -- overriding the wrapper's `trace_id`. Without the template fix, the planner will still see a freshly generated `trace_id`, breaking persistence alignment.

## Tasks
1. Update `OrchestratorAgentWrapper.chat()` to pre-compute `trace_id` and inject it into `tool_ctx`.
2. Simplify the post-call block in `OrchestratorAgentWrapper.chat()`.
3. Update `OrchestratorAgentWrapper.resume()` with the same pattern.
4. Simplify the post-call block in `OrchestratorAgentWrapper.resume()`.
5. Fix the orchestrator template `orchestrator.py.jinja` to respect caller-provided `trace_id`.

## Detailed Steps

### Step 1: Update `OrchestratorAgentWrapper.chat()` tool_ctx construction
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_wrapper.py`.
- In `OrchestratorAgentWrapper.chat()`, locate the `tool_ctx` construction block (currently around lines 542-548).
- The current code is:
  ```python
        ctx = dict(llm_context or {})
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id_hint}
  ```
- Change it to:
  ```python
        ctx = dict(llm_context or {})
        trace_id = trace_id_hint or secrets.token_hex(8)
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id}
  ```
- Key changes: (a) compute `trace_id` eagerly, (b) inject `session_id` and `trace_id` into `tool_ctx`, (c) initialize `trace_holder["id"]` with `trace_id` (not `trace_id_hint`) so `_trace_id_supplier` returns the correct id during execution.

### Step 2: Simplify post-call block in `OrchestratorAgentWrapper.chat()`
- After the `try/finally` block in `chat()`, locate the post-call trace_id derivation block (currently around lines 572-576):
  ```python
        trace_id = trace_id_hint or _get_attr(response, "trace_id") or _trace_id_supplier() or secrets.token_hex(8)
        trace_holder["id"] = trace_id
        await self._event_recorder.persist(trace_id)
  ```
- The `persist()` call was already removed in Phase 2. Now also replace the `trace_id = ...` fallback chain and `trace_holder["id"] = trace_id` lines with a comment:
  ```python
        # trace_id is already pre-computed and trace_holder already set -- nothing needed here
  ```
- The variable `trace_id` is already defined before the `try` block so it remains available for `ChatResult` construction and log statements.

### Step 3: Update `OrchestratorAgentWrapper.resume()` tool_ctx construction
- In `OrchestratorAgentWrapper.resume()`, locate the `tool_ctx` construction block (currently around lines 652-657):
  ```python
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id_hint}
  ```
- Change it to:
  ```python
        trace_id = trace_id_hint or secrets.token_hex(8)
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id}
  ```

### Step 4: Simplify post-call block in `OrchestratorAgentWrapper.resume()`
- After the `try/finally` block in `resume()`, locate the post-call block (currently around lines 685-687):
  ```python
        trace_id = trace_id_hint or _get_attr(response, "trace_id") or _trace_id_supplier() or secrets.token_hex(8)
        trace_holder["id"] = trace_id
        await self._event_recorder.persist(trace_id)
  ```
- The `persist()` call was already removed in Phase 2. Replace the remaining lines with:
  ```python
        # trace_id is already pre-computed and trace_holder already set -- nothing needed here
  ```

### Step 5: Fix the orchestrator template
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja`.
- **This is a Jinja template, not plain Python.** The lines to change are outside any Jinja conditional blocks (`{% if ... %}`), so they can be treated as regular Python. Be careful not to disturb surrounding Jinja syntax.
- In `execute()` (currently line 209), change:
  ```python
        trace_id = secrets.token_hex(8)
  ```
  to:
  ```python
        trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)
  ```
- In `resume()` (currently line 315), change:
  ```python
        trace_id = secrets.token_hex(8)
  ```
  to:
  ```python
        trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)
  ```

## Required Code

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.chat() tool_ctx block ---
        ctx = dict(llm_context or {})
        trace_id = trace_id_hint or secrets.token_hex(8)
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id}
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.chat() post-call block (after try/finally) ---
        # trace_id is already pre-computed and trace_holder already set -- nothing needed here
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.resume() tool_ctx block ---
        trace_id = trace_id_hint or secrets.token_hex(8)
        tool_ctx = {
            **self._tool_context_defaults,
            **dict(tool_context or {}),
            "session_id": session_id,
            "trace_id": trace_id,
        }
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id}
```

```python
# Target file: penguiflow/cli/playground_wrapper.py
# --- OrchestratorAgentWrapper.resume() post-call block (after try/finally) ---
        # trace_id is already pre-computed and trace_holder already set -- nothing needed here
```

```python
# Target file: penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja
# --- In execute() method ---
        trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)
```

```python
# Target file: penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja
# --- In resume() method ---
        trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)
```

## Exit Criteria (Success)
- [ ] `OrchestratorAgentWrapper.chat()` computes `trace_id = trace_id_hint or secrets.token_hex(8)` before the orchestrator call and injects it (along with `session_id`) into `tool_ctx`.
- [ ] `OrchestratorAgentWrapper.chat()` initializes `trace_holder` with `{"id": trace_id}` (not `trace_id_hint`).
- [ ] `OrchestratorAgentWrapper.chat()` post-call block has no `trace_id = trace_id_hint or _get_attr(...)` fallback chain -- only a comment.
- [ ] `OrchestratorAgentWrapper.resume()` has the same pattern as `chat()`.
- [ ] The template file `orchestrator.py.jinja` uses `(tool_context or {}).get("trace_id") or secrets.token_hex(8)` in both `execute()` and `resume()`.
- [ ] No `await self._event_recorder.persist(...)` calls remain in any OrchestratorAgentWrapper method.
- [ ] `uv run ruff check penguiflow/cli/playground_wrapper.py` reports zero errors.
- [ ] `uv run mypy` reports zero new type errors.

## Implementation Notes
- **`OrchestratorAgentWrapper.resume()` trace_id simplification:** The old fallback chain (`trace_id_hint or _get_attr(response, "trace_id") or _trace_id_supplier() or secrets.token_hex(8)`) is intentionally replaced with `trace_id_hint or secrets.token_hex(8)`. The wrapper is the source of truth -- the orchestrator receives the wrapper's `trace_id` via `tool_context` anyway. Discarding the response's `trace_id` is correct.
- **Post-call block replacement:** Keep the explanatory comment `# trace_id is already pre-computed and trace_holder already set -- nothing needed here` to document why the old 3-line block was removed.
- The `_trace_id_supplier` closure and `_planner_trace_id` helper are still used by the SSE streaming path during execution -- do NOT remove them.
- **Template note:** `session_id` is already a first-class parameter of `execute()` and `resume()` in the template, so it does not need the same treatment.
- **Custom orchestrators:** Projects generated from the old template will have the unconditional `secrets.token_hex(8)` pattern. They must be updated manually. A documentation note about this is added in Phase 4.
- Depends on Phase 2 (the `persist()` calls must already be removed).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/cli/playground_wrapper.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/cli/test_playground_wrapper_helpers.py::test_orchestrator_wrapper_forwards_tool_context_when_supported -x -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/cli/test_playground_wrapper_helpers.py::test_orchestrator_agent_wrapper_initialize_and_chat_pause -x -v
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes

- **`penguiflow/cli/playground_wrapper.py` -- `OrchestratorAgentWrapper.chat()`:**
  - Added `trace_id = trace_id_hint or secrets.token_hex(8)` computation before `tool_ctx` construction.
  - Injected `"session_id": session_id` and `"trace_id": trace_id` into `tool_ctx` dict, placed after the caller's `tool_context` spread so they take precedence.
  - Changed `trace_holder` initialization from `{"id": trace_id_hint}` to `{"id": trace_id}`.
  - Replaced the post-call 3-line fallback chain (`trace_id = trace_id_hint or _get_attr(...) ...` / `trace_holder["id"] = trace_id`) with a single explanatory comment.

- **`penguiflow/cli/playground_wrapper.py` -- `OrchestratorAgentWrapper.resume()`:**
  - Applied the identical pattern: pre-computed `trace_id`, injected `session_id` and `trace_id` into `tool_ctx`, initialized `trace_holder` with `trace_id`, replaced post-call fallback chain with comment.

- **`penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja`:**
  - In `execute()`: changed `trace_id = secrets.token_hex(8)` to `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`.
  - In `resume()`: same change.

### Key Considerations

- The `_trace_id_supplier` closure and `_planner_trace_id` helper were intentionally preserved. They are still used by the SSE streaming path during execution to supply the trace ID to event callbacks before the orchestrator call returns.
- The `session_id` and `trace_id` are placed last in the `tool_ctx` dict spread, meaning they override any values from `self._tool_context_defaults` or caller-provided `tool_context`. This is intentional: the wrapper is the authoritative source for these values.
- In the template, the `base_tool_context` dict is spread last in `merged_tool_context`, so its `trace_id` key would normally override the one from `tool_context`. However, since `base_tool_context` now derives `trace_id` from `tool_context` (or generates a fresh one), the value is consistent throughout.

### Assumptions

- The `persist()` calls referenced in the plan were already removed by Phase 2. This was confirmed -- no `persist` references exist in the current `playground_wrapper.py`.
- The `_build_trajectory` function and `state_store` parameter referenced in the test file (`test_playground_wrapper_helpers.py`) are from a prior code state and were removed by earlier phases. The test file has not yet been updated for these removals. This is a pre-existing issue, not introduced by Phase 3.
- The `llm_context` parameter was not mentioned for injection into `tool_ctx` in the phase plan, so it was not added. Only `session_id` and `trace_id` are injected.

### Deviations from Plan

None. All five tasks were implemented exactly as specified.

### Potential Risks & Reviewer Attention Points

- **Pre-existing test failures:** The verification commands referencing `test_playground_wrapper_helpers.py` cannot run because that test file imports `_build_trajectory` (removed in earlier phases) and constructs `_EventRecorder` with a `state_store` argument (also removed). These tests will need to be updated in a later phase. The failures are confirmed to be pre-existing (all tests passed on HEAD before the working tree modifications from phases 0-2).
- **Template merge order:** In the template's `merged_tool_context`, `base_tool_context` is spread last, which means it still overrides caller-provided `tool_context` for keys like `tenant_id`, `user_id`, etc. This is the existing behavior and was not changed. The only change is that `trace_id` in `base_tool_context` now respects the caller-provided value via `(tool_context or {}).get("trace_id")`.
- **Existing orchestrator projects:** Projects generated from the old template will still have the unconditional `secrets.token_hex(8)` pattern. As noted in the phase file, a documentation note about this is planned for Phase 4.

### Files Modified

- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_wrapper.py` -- modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja` -- modified
