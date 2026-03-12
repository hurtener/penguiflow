# Phase 001: Fix Merge Order in All 7 Planner Templates

## Objective
Swap the spread order in the `merged_tool_context` dictionary so that caller-provided `tool_context` values take precedence over the orchestrator's `base_tool_context` defaults. Currently `base_tool_context` is spread last, silently overwriting all caller-provided values (`tenant_id`, `user_id`, `session_id`, `trace_id`, `turn_id`, `task_id`, `is_subagent`). After this fix, the merge priority becomes: `_tool_context_defaults` (lowest) < `base_tool_context` (middle) < caller `tool_context` (highest).

## Tasks
1. Fix merge order in `execute()` for all 7 planner templates
2. Fix merge order in `resume()` for all 7 planner templates

## Detailed Steps

### Step 1: Update the `react` template
- Open `penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja`
- Lines 226-230 (`execute()`): Change the `merged_tool_context` block from:
  ```python
  merged_tool_context = {
      **self._tool_context_defaults,
      **(dict(tool_context or {})),
      **base_tool_context,
  }
  ```
  to:
  ```python
  merged_tool_context = {
      **self._tool_context_defaults,
      **base_tool_context,
      **(dict(tool_context or {})),
  }
  ```
- Lines 327-331 (`resume()`): Apply the same swap (move `**base_tool_context,` before `**(dict(tool_context or {})),`)

### Step 2: Update the `minimal` template
- Open `penguiflow/templates/new/minimal/src/__package_name__/orchestrator.py.jinja`
- Lines 345-349 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 451-455 (`resume()`): Apply the same swap

### Step 3: Update the `enterprise` template
- Open `penguiflow/templates/new/enterprise/src/__package_name__/orchestrator.py.jinja`
- Lines 232-236 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 337-341 (`resume()`): Apply the same swap

### Step 4: Update the `analyst` template
- Open `penguiflow/templates/new/analyst/src/__package_name__/orchestrator.py.jinja`
- Lines 231-235 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 336-340 (`resume()`): Apply the same swap

### Step 5: Update the `wayfinder` template
- Open `penguiflow/templates/new/wayfinder/src/__package_name__/orchestrator.py.jinja`
- Lines 232-236 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 337-341 (`resume()`): Apply the same swap

### Step 6: Update the `parallel` template
- Open `penguiflow/templates/new/parallel/src/__package_name__/orchestrator.py.jinja`
- Lines 230-234 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 331-335 (`resume()`): Apply the same swap

### Step 7: Update the `rag_server` template
- Open `penguiflow/templates/new/rag_server/src/__package_name__/orchestrator.py.jinja`
- Lines 231-235 (`execute()`): Swap `**base_tool_context,` and `**(dict(tool_context or {})),` so `base_tool_context` comes before `tool_context`
- Lines 336-340 (`resume()`): Apply the same swap

## Required Code

The target state for every `merged_tool_context` block in all 7 templates (the only change is swapping lines 2 and 3):

```python
# Pattern to apply in EVERY execute() and resume() across all 7 templates
        merged_tool_context = {
            **self._tool_context_defaults,
            **base_tool_context,
            **(dict(tool_context or {})),
        }
```

## Exit Criteria (Success)
- [ ] All 7 template files have been modified (react, minimal, enterprise, analyst, wayfinder, parallel, rag_server)
- [ ] In every `merged_tool_context` block, `**base_tool_context,` appears BEFORE `**(dict(tool_context or {})),`
- [ ] The `_tool_context_defaults` spread remains first in every block
- [ ] Each template has exactly 2 `merged_tool_context` assignment blocks (one in `execute()`, one in `resume()`)
- [ ] No syntax errors introduced in the Jinja templates
- [ ] The `flow` and `controller` templates are NOT modified

## Implementation Notes
- This is a line-swap edit, not a line-replacement. The two lines `**(dict(tool_context or {})),` and `**base_tool_context,` simply trade positions.
- The indentation must be preserved exactly (12 spaces of leading whitespace for the dict entries).
- This phase depends on Phase 000 being completed first. The trace_id pre-resolution fix ensures that `base_tool_context` already contains the caller's `trace_id` (or a correctly generated fallback). The merge order fix then ensures that if the caller provides any additional overrides via `tool_context`, those win.
- Both fixes together are needed for full correctness. Fix 1 (Phase 000) ensures internal consistency of `base_tool_context`. Fix 2 (this phase) ensures the final `merged_tool_context` gives caller values highest priority.

## Verification Commands
```bash
# Confirm merge order: base_tool_context should appear BEFORE tool_context in every merge block
# Expected: In every match, **base_tool_context appears on the line BEFORE **(dict(tool_context
grep -A3 "merged_tool_context = {" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja

# Count: should be 14 blocks total (2 per template x 7 templates) where base_tool_context precedes tool_context
# Quick sanity: no block should have tool_context before base_tool_context anymore
grep -B1 "base_tool_context," penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep "tool_context or {}" | wc -l
# Expected: 0 (meaning base_tool_context never appears AFTER tool_context)
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- Swapped the order of `**(dict(tool_context or {})),` and `**base_tool_context,` in all 14 `merged_tool_context = {` blocks across 7 planner template files (2 blocks per file: one in `execute()`, one in `resume()`).
- The fix was applied identically to: `react`, `minimal`, `enterprise`, `analyst`, `wayfinder`, `parallel`, `rag_server`.
- The `flow` and `controller` templates were confirmed to not contain any `merged_tool_context` blocks and were left unmodified.

### Key Considerations
- Since all 14 blocks had an identical 5-line pattern, a `replace_all` edit strategy was used on each file, which cleanly swapped both occurrences (execute + resume) in a single operation per file.
- The indentation was preserved exactly (8 spaces for the assignment, 12 spaces for each dict entry) by replacing the full 5-line block rather than individual lines.
- Before editing, every block was read and confirmed to match the expected buggy pattern (tool_context spread before base_tool_context).

### Assumptions
- The phase file's line numbers were treated as approximate guides. The actual matching was done by content pattern rather than line number, which proved correct (all line numbers matched the plan exactly).
- The `flow` and `controller` templates do not need this fix because they do not contain `merged_tool_context` blocks at all (confirmed via grep).
- Phase 000 changes (trace_id pre-resolution) are assumed to already be in place on this branch, as indicated by the `phase-000.md` file appearing in the git diff.

### Deviations from Plan
None. The implementation followed the plan exactly as specified.

### Potential Risks & Reviewer Attention Points
- These are Jinja template files (`.py.jinja`), not directly executed Python. The syntax correctness cannot be fully validated by ruff/mypy/pytest since they are template source files. However, the edit is a pure line swap with no structural changes, so the risk of introducing syntax errors is minimal.
- The full CI suite (ruff, mypy, pytest with 84.5% coverage threshold) was run and all checks passed (2617 tests passed, 85.08% coverage).
- The merge priority is now correctly: `_tool_context_defaults` (lowest, set first) < `base_tool_context` (middle, overwrites defaults) < caller `tool_context` (highest, overwrites everything). This means caller-provided values like `tenant_id`, `user_id`, `session_id`, `trace_id`, etc. will no longer be silently overwritten by the orchestrator's internal `base_tool_context`.

### Files Modified
- `penguiflow/templates/new/react/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/minimal/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/enterprise/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/analyst/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/wayfinder/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/parallel/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/rag_server/src/__package_name__/orchestrator.py.jinja`
- `docs/RFC/ToDo/issue-78/001-playground-bug/phases/phase-001.md` (this file, implementation notes appended)
