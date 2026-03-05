# Phase 003: `ToolNode` -- migrate 9 `ctx._artifacts` call sites to `ctx.artifacts.upload()`

## Objective

All 9 direct call sites in `penguiflow/tools/node.py` use `ctx._artifacts.put_bytes(...)` or `ctx._artifacts.put_text(...)` without passing `scope=`. This means artifacts stored by `ToolNode` lack scope metadata (tenant/user/session/trace) and are invisible to `ScopedArtifacts.list()`. This phase migrates all 9 call sites to use `ctx.artifacts.upload(...)`, which auto-injects scope and accepts both `bytes` and `str`.

## Tasks
1. Replace all 9 `ctx._artifacts.put_bytes(...)` / `ctx._artifacts.put_text(...)` calls with `ctx.artifacts.upload(...)`.

## Detailed Steps

### Step 1: Understand the migration pattern

`ScopedArtifacts.upload(data, ...)` accepts both `bytes` and `str` as `data`, auto-injects scope, and maps to `put_bytes`/`put_text` internally. The parameter signatures are compatible -- both accept `mime_type`, `filename`, `namespace`, `meta` -- minus `scope` (which is auto-injected by `ScopedArtifacts`).

```python
# Before (no scope injected):
ref = await ctx._artifacts.put_bytes(data, mime_type=mime, namespace=self.config.name)

# After (scope auto-injected):
ref = await ctx.artifacts.upload(data, mime_type=mime, namespace=self.config.name)
```

```python
# Before:
ref = await ctx._artifacts.put_text(text, namespace=self.config.name)

# After:
ref = await ctx.artifacts.upload(text, namespace=self.config.name)
```

### Step 2: Replace all 9 call sites in `penguiflow/tools/node.py`

Each change is a simple find-and-replace of `ctx._artifacts.put_bytes(` to `ctx.artifacts.upload(` or `ctx._artifacts.put_text(` to `ctx.artifacts.upload(`. The arguments remain identical.

**Call site 1 -- line 1099** (L4: field extraction, `put_bytes`):
```python
# Before:
ref = await ctx._artifacts.put_bytes(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
```

**Call site 2 -- line 1252** (L2: MCP content blocks, EmbeddedResource blob, `put_bytes`):
```python
# Before:
ref = await ctx._artifacts.put_bytes(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
```

**Call site 3 -- line 1278** (L2: MCP content blocks, dict blob, `put_bytes`):
```python
# Before:
ref = await ctx._artifacts.put_bytes(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime,
    namespace=self.config.name,
)
```

**Call site 4 -- line 1352** (L3: heuristic binary detection, `put_bytes`):
```python
# Before:
ref = await ctx._artifacts.put_bytes(
    data,
    mime_type=mime_type,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime_type,
    namespace=self.config.name,
)
```

**Call site 5 -- line 1467** (L0: size safety net, large string, `put_text`):
```python
# Before:
ref = await ctx._artifacts.put_text(
    result,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    result,
    namespace=self.config.name,
)
```

**Call site 6 -- line 1502** (size limits walk, large string value, `put_text`):
```python
# Before:
ref = await ctx._artifacts.put_text(
    value,
    namespace=self.config.name,
)
# After:
ref = await ctx.artifacts.upload(
    value,
    namespace=self.config.name,
)
```

**Call site 7 -- line 1835** (resource processing, blob, `put_bytes`):
```python
# Before:
ref = await ctx._artifacts.put_bytes(
    data,
    mime_type=mime_type or "application/octet-stream",
    namespace=f"{self.config.name}.resource",
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime_type or "application/octet-stream",
    namespace=f"{self.config.name}.resource",
)
```

**Call site 8 -- line 1849** (resource processing, large text, `put_text`):
```python
# Before:
ref = await ctx._artifacts.put_text(
    text,
    mime_type=mime_type or "text/plain",
    namespace=f"{self.config.name}.resource",
)
# After:
ref = await ctx.artifacts.upload(
    text,
    mime_type=mime_type or "text/plain",
    namespace=f"{self.config.name}.resource",
)
```

**Call site 9 -- line 2021** (app HTML storage, `put_text`):
```python
# Before:
ref = await ctx._artifacts.put_text(
    html,
    mime_type=UI_MIME_TYPE,
    namespace=f"{self.config.name}.app",
    meta=app_meta_payload,
)
# After:
ref = await ctx.artifacts.upload(
    html,
    mime_type=UI_MIME_TYPE,
    namespace=f"{self.config.name}.app",
    meta=app_meta_payload,
)
```

## Required Code

The changes are mechanical replacements. Use a global find-and-replace approach:

```python
# Target file: penguiflow/tools/node.py
# Replace ALL occurrences of:
#   ctx._artifacts.put_bytes(
# with:
#   ctx.artifacts.upload(
#
# Replace ALL occurrences of:
#   ctx._artifacts.put_text(
# with:
#   ctx.artifacts.upload(
```

After replacement, verify there are exactly 0 remaining occurrences of `ctx._artifacts.put_bytes` or `ctx._artifacts.put_text` in the file. The only remaining `ctx._artifacts` reference should be the `ResourceCache` init at line 1774 (`artifact_store=ctx._artifacts`), which is handled in Phase 004.

## Exit Criteria (Success)
- [ ] Zero occurrences of `ctx._artifacts.put_bytes` in `penguiflow/tools/node.py`
- [ ] Zero occurrences of `ctx._artifacts.put_text` in `penguiflow/tools/node.py`
- [ ] Exactly 9 occurrences of `ctx.artifacts.upload(` in `penguiflow/tools/node.py`
- [ ] The `ResourceCache` init at line 1773-1774 (`artifact_store=ctx._artifacts`) is NOT changed in this phase (handled in Phase 004)
- [ ] No import or syntax errors
- [ ] Existing tests pass

## Implementation Notes
- This is a mechanical replacement. The argument lists are unchanged -- only the method target changes from `ctx._artifacts.put_bytes`/`put_text` to `ctx.artifacts.upload`.
- `ScopedArtifacts.upload()` dispatches to `put_bytes` or `put_text` based on the type of `data` (`bytes` vs `str`), so no logic change is needed.
- The `DummyCtx` in `tests/test_toolnode_phase2.py` already exposes `ctx.artifacts` as `ScopedArtifacts`, so existing ToolNode tests should continue to pass.
- There is one remaining `ctx._artifacts` reference at line 1774 (`artifact_store=ctx._artifacts`) inside the `ResourceCache` constructor -- this is addressed in Phase 004.
- This phase is independent of Phases 000-002 but is logically ordered after them.

## Verification Commands
```bash
# Verify no remaining ctx._artifacts.put_bytes or ctx._artifacts.put_text:
grep -n "ctx._artifacts.put_bytes\|ctx._artifacts.put_text" penguiflow/tools/node.py && echo "FAIL: still has old calls" || echo "OK: no old calls remain"

# Verify ctx.artifacts.upload count:
grep -c "ctx.artifacts.upload(" penguiflow/tools/node.py

# Lint and type check:
uv run ruff check penguiflow/tools/node.py
uv run mypy penguiflow/tools/node.py

# Run relevant tests:
uv run pytest tests/test_toolnode_phase2.py -v
```
