# Phase 002: Verification and Validation

## Objective
Run the full verification suite to confirm that both fixes (trace_id pre-resolution and merge order) are correctly applied across all 7 planner templates, that no regressions were introduced, and that the excluded templates (`flow`, `controller`) remain untouched. This phase produces no code changes -- it is purely a validation step.

## Tasks
1. Run ruff lint check to confirm no lint errors
2. Run the full pytest suite to confirm no test regressions
3. Run grep checks to confirm trace_id fix is correct across all templates
4. Run grep checks to confirm merge order fix is correct across all templates

## Detailed Steps

### Step 1: Run ruff lint check
- Execute `uv run ruff check .` from the repository root
- Expected: zero errors, clean exit

### Step 2: Run the full test suite
- Execute `uv run pytest` from the repository root
- Expected: all tests pass, no failures or errors

### Step 3: Verify no remaining unconditional trace_id in planner templates
- Run: `grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja`
- Expected: every match includes `(tool_context or {}).get("trace_id") or` before `secrets.token_hex(8)`. No unconditional `trace_id = secrets.token_hex(8)` lines.
- Filter check: `grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep -v "tool_context"` should return zero lines.

### Step 4: Verify flow and controller templates are untouched
- Run: `grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{flow,controller}/src/__package_name__/orchestrator.py.jinja`
- Expected: exactly 2 matches (flow line 96, controller line 84), both unconditional `trace_id = secrets.token_hex(8)` -- confirming they were intentionally left alone.

### Step 5: Verify merge order is correct in all 7 planner templates
- Run: `grep -A3 "merged_tool_context = {" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja`
- Expected: in every block, the order is:
  1. `**self._tool_context_defaults,`
  2. `**base_tool_context,`
  3. `**(dict(tool_context or {})),`
- There should be 14 such blocks total (2 per template x 7 templates).

### Step 6: Verify no merge block has the old (broken) order
- Run: `grep -B1 "base_tool_context," penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep "tool_context or {}" | wc -l`
- Expected: 0 (no block has `tool_context` spread before `base_tool_context`)

## Exit Criteria (Success)
- [ ] `uv run ruff check .` exits with zero errors
- [ ] `uv run pytest` passes all tests with no failures
- [ ] Zero unconditional `trace_id = secrets.token_hex(8)` lines in the 7 planner templates
- [ ] Exactly 14 occurrences of `(tool_context or {}).get("trace_id") or secrets.token_hex(8)` across the 7 planner templates (2 per template)
- [ ] `flow` and `controller` templates still have their unconditional `trace_id = secrets.token_hex(8)` (untouched)
- [ ] All 14 `merged_tool_context` blocks show `base_tool_context` before `tool_context` in the spread order
- [ ] Zero `merged_tool_context` blocks have the old broken order

## Implementation Notes
- This phase makes NO code changes. It is purely verification.
- If any check fails, go back to the relevant phase (Phase 000 for trace_id issues, Phase 001 for merge order issues) and correct the edit.
- The ruff and pytest checks use `uv run` as specified in the project's CLAUDE.md development commands.
- The grep commands must be run from the repository root directory (`/Users/martin.alonso/Documents/lg/repos/penguiflow`).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow

# 1. Lint check
uv run ruff check .

# 2. Full test suite
uv run pytest

# 3. No unconditional trace_id in planner templates (expect 0 lines)
grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep -v "tool_context"

# 4. Correct trace_id pattern count (expect each file shows count of 2)
grep -c '(tool_context or {}).get("trace_id") or secrets.token_hex(8)' penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja

# 5. Flow and controller untouched (expect 2 unconditional matches)
grep -rn "trace_id = secrets.token_hex" penguiflow/templates/new/{flow,controller}/src/__package_name__/orchestrator.py.jinja

# 6. Merge order correct (visually inspect output -- base_tool_context before tool_context in every block)
grep -A3 "merged_tool_context = {" penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja

# 7. No blocks with old broken order (expect 0)
grep -B1 "base_tool_context," penguiflow/templates/new/{react,minimal,enterprise,analyst,wayfinder,parallel,rag_server}/src/__package_name__/orchestrator.py.jinja | grep "tool_context or {}" | wc -l
```
