# Phase 000: Fix trace_id Pre-resolution in 6 Templates

## Objective
Replace the unconditional `trace_id = secrets.token_hex(8)` with a caller-aware fallback `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)` in both `execute()` and `resume()` methods across the 6 affected orchestrator templates. This ensures that when the playground (or any caller) passes a `trace_id` via `tool_context`, the orchestrator reuses it instead of generating a new one, preventing the trace ID mismatch that causes 404s and "No trajectory yet" in the UI. The `react` template already has this fix and is excluded from this phase.

## Tasks
1. Update `trace_id` assignment in `execute()` for all 6 templates
2. Update `trace_id` assignment in `resume()` for all 6 templates

## Detailed Steps

### Step 1: Update the `minimal` template
- Open `penguiflow/templates/new/minimal/src/__package_name__/orchestrator.py.jinja`
- Line 327: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 435: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

### Step 2: Update the `enterprise` template
- Open `penguiflow/templates/new/enterprise/src/__package_name__/orchestrator.py.jinja`
- Line 215: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 322: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

### Step 3: Update the `analyst` template
- Open `penguiflow/templates/new/analyst/src/__package_name__/orchestrator.py.jinja`
- Line 213: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 320: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

### Step 4: Update the `wayfinder` template
- Open `penguiflow/templates/new/wayfinder/src/__package_name__/orchestrator.py.jinja`
- Line 213: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 320: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

### Step 5: Update the `parallel` template
- Open `penguiflow/templates/new/parallel/src/__package_name__/orchestrator.py.jinja`
- Line 213: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 319: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

### Step 6: Update the `rag_server` template
- Open `penguiflow/templates/new/rag_server/src/__package_name__/orchestrator.py.jinja`
- Line 213: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- Line 320: Replace `trace_id = secrets.token_hex(8)` with `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`

## Exit Criteria (Success)
- [ ] All 6 template files have been modified (minimal, enterprise, analyst, wayfinder, parallel, rag_server)
- [ ] Each file's `execute()` method uses `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- [ ] Each file's `resume()` method uses `trace_id = (tool_context or {}).get("trace_id") or secrets.token_hex(8)`
- [ ] No remaining unconditional `trace_id = secrets.token_hex(8)` in any of the 7 planner templates (the 6 above + react)
- [ ] The `flow` and `controller` templates are NOT modified (they use a different architecture)
- [ ] No syntax errors introduced in the Jinja templates

## Implementation Notes
- The replacement is a single-line edit on each occurrence. The indentation must be preserved exactly (8 spaces of leading whitespace).
- The `react` template already has this fix at lines 209 and 315 -- do NOT touch it in this phase.
- The `flow` template (line 96) and `controller` template (line 84) also have `trace_id = secrets.token_hex(8)` but they are intentionally excluded -- they use a different architecture where `execute()` takes `tenant_id`/`user_id`/`session_id` directly rather than a `tool_context` dict.
- The `or` operator is used (not `if/else`) so that both `None` and empty-string trace_ids fall back to generating a new one.
- All files are Jinja templates (`.py.jinja`), not plain Python. The edit locations are within raw Python blocks, not Jinja control flow, so no special Jinja syntax is needed.

## Verification Commands
```bash
# Confirm no remaining unconditional trace_id in the 7 planner templates
# Expected: zero matches
grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep -v "tool_context"

# Confirm the new pattern exists in all 7 planner templates (react already had it)
# Expected: 14 matches (2 per template x 7 templates)
grep -c '(tool_context or {}).get("trace_id") or secrets.token_hex(8)' penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja

# Confirm flow and controller are untouched
# Expected: 2 matches (1 in flow, 1 in controller) -- both unconditional, as intended
grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{flow,controller}/src/__package_name__/orchestrator.py.jinja
```
