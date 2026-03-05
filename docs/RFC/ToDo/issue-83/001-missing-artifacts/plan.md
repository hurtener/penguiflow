# Plan: Integrate persistent ArtifactStore into `list_artifacts` tool + unify artifact access

## Context

The `list_artifacts` tool in `penguiflow/rich_output/nodes.py` only queries the in-run `ArtifactRegistry` (tracked during a planner ReAct loop). It never checks the persistent `ArtifactStore` (accessible via `ctx.artifacts`). This means binary artifacts stored via `ctx.artifacts.upload()` or `put_bytes()`/`put_text()` that weren't explicitly registered into the in-run registry are invisible to the `list_artifacts` tool — the LLM cannot discover or reference them.

This is the same class of bug as issue #83: the persistent artifact store is silently ignored in a code path that should use it as a fallback.

Additionally, several internal framework modules (`ToolNode`, `ResourceCache`, `tool_jobs`) use `ctx._artifacts` (the raw `ArtifactStore`) directly instead of `ctx.artifacts` (the scoped `ScopedArtifacts` facade). This means their writes bypass scope injection — artifacts stored by these modules lack tenant/user/session/trace scope metadata and are invisible to `ScopedArtifacts.list()`.

## Audit result

Audited all `@tool` functions and internal framework modules for artifact access:

**`@tool`-decorated functions (in `penguiflow/rich_output/nodes.py`):**
- `render_component` (line 81) — uses both `get_artifact_registry(ctx)` AND `ctx._artifacts`
- `list_artifacts` (line 176) — uses in-run registry only, never checks persistent store

**Non-`@tool` framework code using `ctx._artifacts` (raw, no scope):**
- `ToolNode` (`penguiflow/tools/node.py`) — 9 write call sites (`put_bytes`/`put_text`) + 1 `ResourceCache` init
- `ResourceCache` (`penguiflow/tools/resources.py`) — uses `put_bytes`, `put_text`, `exists` on raw store
- `_extract_artifacts_from_observation` (`penguiflow/sessions/tool_jobs.py`) — uses `put_text` with manually constructed scope

**Already correct (use `ctx.artifacts` public facade):**
- `web_fetch` (`common_tools/web/fetch.py`) — uses `ctx.artifacts`
- `web_context` (`common_tools/web/specs.py`) — uses `ctx.artifacts`

## Fix 1: `render_component` — switch from `ctx._artifacts` to `ctx.artifacts`

**Files:**
- `penguiflow/rich_output/nodes.py` — `render_component` (line 106)
- `penguiflow/planner/artifact_registry.py` — `_maybe_hydrate_stored_payload` (line 534)

`render_component` currently passes `ctx._artifacts` (the raw `ArtifactStore`) to `resolve_artifact_refs_async`. Other artifact-interacting tools (`web_fetch`, `web_context`) use the public `ctx.artifacts` facade (`ScopedArtifacts`). For consistency and proper scope checking, switch `render_component` to use `ctx.artifacts`.

**Problem:** `_maybe_hydrate_stored_payload` calls `.get()` on the artifact store, but `ScopedArtifacts` exposes `.download()` instead (same semantics: `bytes | None`).

**Note:** `ScopedArtifacts.download()` is scope-checked — it returns `None` for artifacts belonging to a different tenant/user/session. This is intentional: scope enforcement is the whole point of the migration. Previously, the raw `.get()` returned any artifact regardless of scope.

**Change in `artifact_registry.py` line 534:**
```python
# Before:
get_fn = getattr(artifact_store, "get", None)
if not callable(get_fn):
    return None

# After:
get_fn = getattr(artifact_store, "get", None)
if not callable(get_fn):
    get_fn = getattr(artifact_store, "download", None)
if not callable(get_fn):
    return None
```

**Change in `nodes.py` line 106:**
```python
# Before:
artifact_store=getattr(ctx, "_artifacts", None),

# After:
artifact_store=getattr(ctx, "artifacts", None),
```

## Fix 2: `list_artifacts` — merge persistent ArtifactStore results

**File:** `penguiflow/rich_output/nodes.py` — `list_artifacts` function (line 176)

### Structure change: early return removed, persistent store appended after registry

The current code has an early return at line 179-180 (`if registry is None: return ...`). This must be removed because the persistent store should be queried regardless of whether an in-run registry exists. The registry is queried first, then persistent store results are appended after — this way `items[-limit:]` favors persistent store entries (the authoritative source for binary artifacts).

New control flow:
1. Query the in-run `ArtifactRegistry` (if available) first.
2. Then query the persistent `ArtifactStore` via `ctx.artifacts` facade (`ScopedArtifacts`).
3. Deduplicate (persistent store entries take precedence for dedup — if a registry entry has the same `artifact_id` as a persistent store entry, the persistent store entry wins).
4. Apply filters and limit on the combined list. Since persistent store items are appended after registry items, `items[-limit:]` favors persistent store entries.

### Step-by-step

1. **Query the in-run `ArtifactRegistry` first** (if available): ingest background results and llm_context, then call `registry.list_records()` WITHOUT `limit=` (limit is applied at the end on the combined list). Collect into `items`.
2. **Get the scoped facade:** `scoped = getattr(ctx, "artifacts", None)`
3. **List persistent artifacts:** `refs = await scoped.list()` — scope is automatic via `_read_scope` (tenant/user/session, excludes trace_id).
4. **Respect `kind` filter:** Persistent store artifacts are `"binary"`. If `kind` is set to `"ui_component"`, skip the persistent store query entirely.
5. **Respect `source_tool` filter:** If `args.source_tool` is set, only include persistent store entries where `ref.source.get("tool") == args.source_tool`. Note: `ArtifactRef.source` is typed as `dict[str, Any]` with `default_factory=dict` — it is never `None`, always at least `{}`. Calling `.get("tool")` directly is safe.
6. **Convert `ArtifactRef` → `ArtifactSummary`-compatible dict:** Map fields using the same pattern as `ArtifactRecord.to_public()`. Reuse `_binary_component_name` and `_binary_summary` helpers from `penguiflow/planner/artifact_registry.py`.
7. **Set `renderable` to `True`** for all persistent store entries (consistent with `ArtifactRecord.to_public()` where `kind == "binary"` always yields `renderable=True`).
8. **Wrap persistent store query in try/except:** Best-effort — never fail listing due to store errors. Log failures at `logger.debug` level for debuggability.
9. **Deduplicate:** Build a set of `artifact_id` values from registry items (queried first). For each persistent store entry, if its `artifact_id` is already in the set, replace the registry entry with the persistent store entry (persistent store wins dedup). Otherwise append it.
10. **Apply limit** on the final combined list (take the last N items — since persistent store items come after registry items, this favors persistent store entries when truncating).

### Pseudocode

```python
async def list_artifacts(args: ListArtifactsArgs, ctx: ToolContext) -> ListArtifactsResult:
    _ensure_enabled()
    # Backward/behavioral compatibility: callers often use kind="tool_artifact"
    # when they really mean "any tool-produced artifact" (including ui_component).
    kind = None if args.kind in {"all", "tool_artifact"} else args.kind

    items: list[dict[str, Any]] = []

    # ── Step 1: Query in-run ArtifactRegistry (if available) ──
    registry = get_artifact_registry(ctx)
    if registry is not None:
        planner = getattr(ctx, "_planner", None)
        trajectory = getattr(planner, "_active_trajectory", None)
        if trajectory is not None:
            try:
                registry.ingest_background_results(getattr(trajectory, "background_results", None))
            except Exception:
                pass
        llm_context = getattr(ctx, "llm_context", None)
        if llm_context is not None:
            try:
                registry.ingest_llm_context(llm_context)
            except Exception:
                pass
        items.extend(registry.list_records(kind=kind, source_tool=args.source_tool))

    # ── Step 2: Query persistent ArtifactStore (appended after registry) ──
    if kind is None or kind == "binary":
        scoped = getattr(ctx, "artifacts", None)
        if scoped is not None:
            try:
                refs = await scoped.list()
                # Build set of IDs already in items (from registry) for dedup
                seen_ids = {item.get("artifact_id") for item in items if item.get("artifact_id")}
                for ref in refs:
                    # Persistent store wins dedup: replace registry entry if same ID
                    if ref.id in seen_ids:
                        items = [item for item in items if item.get("artifact_id") != ref.id]
                    source_tool = ref.source.get("tool")
                    if args.source_tool and source_tool != args.source_tool:
                        continue
                    items.append({
                        "ref": ref.id,
                        "kind": "binary",
                        "source_tool": source_tool,
                        "component": _binary_component_name(ref.mime_type),
                        "title": ref.filename,
                        "summary": _binary_summary(ref),
                        "artifact_id": ref.id,
                        "mime_type": ref.mime_type,
                        "size_bytes": ref.size_bytes,
                        "created_step": None,
                        "renderable": True,
                        "metadata": {},
                    })
            except Exception as e:
                logger.debug("Failed to list persistent artifacts: %s", e, exc_info=True)

    # ── Step 3: Apply limit ──
    # Persistent store items come after registry items, so [-limit:] favors them.
    if args.limit and args.limit > 0:
        items = items[-args.limit:]

    return ListArtifactsResult(artifacts=[ArtifactSummary.model_validate(item) for item in items])
```

**Note:** `registry.list_records()` is called WITHOUT `limit=` here, because the limit is applied once on the combined list at the end. Persistent store items are appended after registry items so that `items[-limit:]` favors persistent store entries when truncating.

### Imports to add

1. Add `import logging` and `logger = logging.getLogger(__name__)` at the top of `nodes.py` (the file currently has no logger — needed for the `logger.debug` call in the persistent store try/except).
2. Add `_binary_component_name` and `_binary_summary` to the existing import block from `penguiflow.planner.artifact_registry` (line 12-16). These are private (`_`-prefixed) helper functions — importing them cross-module is intentional since `nodes.py` and `artifact_registry.py` are closely-related internal modules within the same package.

## Fix 3: `ToolNode` — switch 9 direct call sites from `ctx._artifacts` to `ctx.artifacts`

**File:** `penguiflow/tools/node.py`

All 9 direct call sites use `ctx._artifacts.put_bytes(...)` or `ctx._artifacts.put_text(...)` without passing `scope=`. This means artifacts stored by `ToolNode` lack scope metadata and are invisible to `ScopedArtifacts.list()`.

`ScopedArtifacts` exposes `.upload(data, ...)` which accepts both `bytes` and `str`, auto-injects scope, and maps to `put_bytes`/`put_text` internally. The parameter signatures are compatible (both accept `mime_type`, `filename`, `namespace`, `meta`), minus `scope` (which is auto-injected by `ScopedArtifacts`).

### Migration pattern

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

### All 9 call sites to change

| Line | Current call | Change to |
|------|-------------|-----------|
| 1099 | `ctx._artifacts.put_bytes(data, mime_type=mime, namespace=self.config.name)` | `ctx.artifacts.upload(data, mime_type=mime, namespace=self.config.name)` |
| 1252 | `ctx._artifacts.put_bytes(data, mime_type=mime, namespace=self.config.name)` | `ctx.artifacts.upload(data, mime_type=mime, namespace=self.config.name)` |
| 1278 | `ctx._artifacts.put_bytes(data, mime_type=mime, namespace=self.config.name)` | `ctx.artifacts.upload(data, mime_type=mime, namespace=self.config.name)` |
| 1352 | `ctx._artifacts.put_bytes(data, mime_type=mime_type, namespace=self.config.name)` | `ctx.artifacts.upload(data, mime_type=mime_type, namespace=self.config.name)` |
| 1467 | `ctx._artifacts.put_text(result, namespace=self.config.name)` | `ctx.artifacts.upload(result, namespace=self.config.name)` |
| 1502 | `ctx._artifacts.put_text(value, namespace=self.config.name)` | `ctx.artifacts.upload(value, namespace=self.config.name)` |
| 1835 | `ctx._artifacts.put_bytes(data, mime_type=... , namespace=f"{self.config.name}.resource")` | `ctx.artifacts.upload(data, mime_type=..., namespace=f"{self.config.name}.resource")` |
| 1849 | `ctx._artifacts.put_text(text, mime_type=..., namespace=f"{self.config.name}.resource")` | `ctx.artifacts.upload(text, mime_type=..., namespace=f"{self.config.name}.resource")` |
| 2021 | `ctx._artifacts.put_text(html, mime_type=UI_MIME_TYPE, namespace=f"{self.config.name}.app", meta=app_meta_payload)` | `ctx.artifacts.upload(html, mime_type=UI_MIME_TYPE, namespace=f"{self.config.name}.app", meta=app_meta_payload)` |

## Fix 4: `ResourceCache` — remove stored `ArtifactStore`, accept `ScopedArtifacts` per-call

**File:** `penguiflow/tools/resources.py`

`ResourceCache` is constructed at `tools/node.py:1773` with `artifact_store=ctx._artifacts`. It caches this store instance and uses `.put_bytes()`, `.put_text()`, and `.exists()` on it.

**Problem:** The `ResourceCache` is lazily initialized once per `ToolNode` instance. If a `ToolNode` is reused across sessions with different scopes, the cached `ScopedArtifacts` would hold the wrong scope. To fix this, remove the stored `artifact_store` from the constructor and instead accept `ScopedArtifacts` as a parameter on `get_or_fetch` (which already receives `ctx: ToolContext`).

### Changes

**Constructor (`resources.py` line 144-148):**
```python
# Before:
def __init__(
    self,
    artifact_store: ArtifactStore,
    namespace: str,
    config: ResourceCacheConfig | None = None,
) -> None:

# After:
def __init__(
    self,
    namespace: str,
    config: ResourceCacheConfig | None = None,
) -> None:
```

Remove `self._artifact_store = artifact_store` from the constructor body (line 157).

Replace the `ArtifactStore` import with `ScopedArtifacts` (line 23: change `from penguiflow.artifacts import ArtifactRef, ArtifactStore` to `from penguiflow.artifacts import ArtifactRef, ScopedArtifacts`).

**`get_or_fetch` and `_fetch_and_store` methods:** These already receive `ctx: ToolContext`. Use `ctx.artifacts` (the `ScopedArtifacts` facade) directly instead of `self._artifact_store`.

**Note:** `ScopedArtifacts.exists()` is scope-checked (returns `False` for artifacts from a different scope), unlike `ArtifactStore.exists()` which only checks storage. This is intentional — scope enforcement is the purpose of this migration. Since `ResourceCache` stores and reads back its own artifacts within the same session, the scope is consistent and this works correctly.

**Call site in `node.py` line 1773-1774:**
```python
# Before:
self._resource_cache = ResourceCache(
    artifact_store=ctx._artifacts,
    namespace=self.config.name,
    config=ResourceCacheConfig(...),
)

# After:
self._resource_cache = ResourceCache(
    namespace=self.config.name,
    config=ResourceCacheConfig(...),
)
```

**Internal method calls in `resources.py` — replace `self._artifact_store` with `ctx.artifacts`:**

| Line | Current call | Change to |
|------|-------------|-----------|
| 188 | `self._artifact_store.exists(entry.artifact_ref.id)` | `ctx.artifacts.exists(entry.artifact_ref.id)` |
| 249 | `self._artifact_store.put_bytes(data, mime_type=..., namespace=...)` | `ctx.artifacts.upload(data, mime_type=..., namespace=...)` |
| 275 | `self._artifact_store.put_text(text, mime_type=..., namespace=...)` | `ctx.artifacts.upload(text, mime_type=..., namespace=...)` |

Both `get_or_fetch` (line 164) and `_fetch_and_store` (line 200) already receive `ctx: ToolContext` as a parameter, so `ctx.artifacts` is accessible in both methods without any signature changes.

## Fix 5: `_extract_artifacts_from_observation` — switch from `ArtifactStore` to `ScopedArtifacts`

**File:** `penguiflow/sessions/tool_jobs.py`

This function currently accepts `artifact_store: ArtifactStore` and `scope: ArtifactScope | None`, and calls `artifact_store.put_text(..., scope=scope)`. The scope is manually constructed at the call site (lines 282-287) from `snapshot.tool_context` — the same values that `ToolJobContext._scoped_artifacts` is constructed with (lines 46-52). So the manual scope construction is redundant when using the facade.

### Changes

**Function signature (`tool_jobs.py` line 165-172):**
```python
# Before:
async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifact_store: ArtifactStore,
    scope: ArtifactScope | None,
) -> list[dict[str, Any]]:

# After:
async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifacts: ScopedArtifacts,
) -> list[dict[str, Any]]:
```

**Internal `_store` function (`tool_jobs.py` line 193):**
```python
# Before:
ref = await artifact_store.put_text(
    serialized,
    mime_type="application/json",
    filename=f"{_node_name}.{_field_name}.json",
    namespace=f"tool_artifact.{_node_name}.{_field_name}",
    scope=scope,
    meta={...},
)

# After:
ref = await artifacts.upload(
    serialized,
    mime_type="application/json",
    filename=f"{_node_name}.{_field_name}.json",
    namespace=f"tool_artifact.{_node_name}.{_field_name}",
    meta={...},
)
```

**Call site (`tool_jobs.py` lines 280-296):**
```python
# Before:
session_id = snapshot.session_id if isinstance(snapshot.session_id, str) and snapshot.session_id else None
tool_ctx = snapshot.tool_context or {}
artifact_scope = ArtifactScope(
    session_id=session_id,
    tenant_id=tool_ctx.get("tenant_id"),
    user_id=tool_ctx.get("user_id"),
    trace_id=tool_ctx.get("trace_id"),
) if session_id else None
extracted_artifacts = []
if isinstance(payload, Mapping):
    extracted_artifacts = await _extract_artifacts_from_observation(
        node_name=spec.name,
        out_model=spec.out_model,
        observation=payload,
        artifact_store=ctx._artifacts,
        scope=artifact_scope,
    )

# After:
extracted_artifacts = []
if isinstance(payload, Mapping):
    extracted_artifacts = await _extract_artifacts_from_observation(
        node_name=spec.name,
        out_model=spec.out_model,
        observation=payload,
        artifacts=ctx.artifacts,
    )
```

The manual `artifact_scope` construction (lines 280-287) is deleted — `ctx.artifacts` already has the correct scope baked in.

Update imports: `ScopedArtifacts` is already imported on line 22. Remove `ArtifactScope` from the import line — after this fix, it is no longer used anywhere in the file (verified: only used in the deleted function signature at line 171 and the deleted scope construction at lines 282-287). The import line becomes: `from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore, ScopedArtifacts`.

## Files to modify

- `penguiflow/rich_output/nodes.py` — `list_artifacts` function + import block, `render_component` line 106
- `penguiflow/planner/artifact_registry.py` — `_maybe_hydrate_stored_payload` line 534 (add `.download()` fallback)
- `penguiflow/tools/node.py` — 9 call sites (`put_bytes`/`put_text` → `upload`) + ResourceCache init (line 1774)
- `penguiflow/tools/resources.py` — Constructor type, 2 write call sites (`put_bytes`/`put_text` → `upload`)
- `penguiflow/sessions/tool_jobs.py` — `_extract_artifacts_from_observation` signature + body + call site

## Tests

### New tests to add (in `tests/test_rich_output_nodes.py`)

1. **Persistent store fallback when no registry exists:** Create a `DummyContext` with `_planner = None` (so registry is `None`) and an `InMemoryArtifactStore` containing a stored artifact. Verify that `list_artifacts` returns the persistent artifact.

2. **Deduplication:** When the registry has a binary artifact AND the persistent store has the same artifact (same `artifact_id`), it should appear only once in results.

3. **`source_tool` filter applied to persistent store:** Store an artifact with `meta={"tool": "web_fetch"}` (the `meta` dict passed to `upload()`/`put_bytes()` becomes `ArtifactRef.source` — see `InMemoryArtifactStore.put_bytes` line 637: `source=dict(meta or {})`). Call `list_artifacts(source_tool="other_tool")`. Verify the persistent artifact is excluded.

4. **`kind="ui_component"` skips persistent store:** Store a binary artifact. Call `list_artifacts(kind="ui_component")`. Verify the persistent artifact is not returned.

### Existing test to update

- **`test_list_artifacts_returns_empty_without_registry`** (line 272): This test currently asserts that when there's no registry, results are empty. After the fix, the `DummyContext` has a `ScopedArtifacts` backed by an empty `InMemoryArtifactStore`, so the result will still be empty (no artifacts in the store). **No change needed** — the test remains valid as-is because the store is empty.

### Existing tests to verify still pass (Fix 3-5)

The `ToolNode` and `tool_jobs` changes are behavioral (scope injection added). Existing tests using `DummyContext` or `ToolJobContext` should continue to pass since both contexts provide `ctx.artifacts` as `ScopedArtifacts`. Run the full test suite to confirm no regressions.

## Verification

1. `uv run ruff check penguiflow/rich_output/nodes.py penguiflow/tools/node.py penguiflow/tools/resources.py penguiflow/sessions/tool_jobs.py`
2. `uv run mypy penguiflow/rich_output/nodes.py penguiflow/tools/node.py penguiflow/tools/resources.py penguiflow/sessions/tool_jobs.py`
3. `uv run pytest tests/ -k "list_artifacts or rich_output or tool_node or tool_jobs or resources"`
4. `uv run pytest` (full suite)
