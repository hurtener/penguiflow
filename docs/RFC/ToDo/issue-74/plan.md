# Plan: ArtifactStore `list` Method + Scoped Artifacts Facade

> **Note on line numbers:** All line numbers in this plan refer to the **pre-edit state** of each file. As edits are applied (especially Enhancement 1 adding code to `artifacts.py`), line numbers in subsequent sections will drift. The implementing agent should always use grep/search to locate the exact positions rather than relying on line numbers alone.

## Context

Currently `ctx.artifacts` exposes the raw `_EventEmittingArtifactStoreProxy` (which implements the full `ArtifactStore` protocol) directly to tool developers. This has two problems:

1. **No `list` method** — there's no way to enumerate artifacts by tenant/session/user.
2. **No scope enforcement** — developers can override or omit tenant_id/user_id/session_id. The proxy only auto-injects `session_id`, and only when the caller doesn't provide one.

This plan introduces a `list` method on the `ArtifactStore` protocol and creates a `ScopedArtifacts` facade that replaces `ctx.artifacts` for tool developers, with automatic, immutable scope injection.

---

## Enhancement 1: Add `list` to ArtifactStore Protocol

### 1.1 Protocol method — `penguiflow/artifacts.py`

Add inside the `ArtifactStore` protocol class body, after the `exists()` method (line ~218), but still within the class:

```python
async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
    """List artifacts matching the given scope filter.
    None fields in scope = don't filter on that dimension.
    If scope is None, returns all artifacts.
    """
    ...
```

### 1.1b `_scope_matches` helper — `penguiflow/artifacts.py`

Add a module-level helper function (outside any class, e.g. near `_generate_artifact_id`) reusable by InMemory and Playground stores:

```python
def _scope_matches(artifact_scope: ArtifactScope | None, filter_scope: ArtifactScope) -> bool:
    """Check if an artifact's scope matches a filter scope.

    Matching rules:
    - If filter_scope has a non-None field, the artifact must have the same value
      in that field to match.
    - If filter_scope has a None field, any value (including None) matches.
    - If the artifact has no scope at all (artifact_scope is None), it is treated
      as having all-None fields — so it matches any filter field that is None,
      but FAILS any filter field that is non-None.
    """
    for field in ("tenant_id", "user_id", "session_id", "trace_id"):
        filter_val = getattr(filter_scope, field)
        if filter_val is None:
            continue  # None filter = wildcard, matches anything
        artifact_val = getattr(artifact_scope, field) if artifact_scope is not None else None
        if artifact_val != filter_val:
            return False
    return True
```

### 1.2 NoOpArtifactStore — `penguiflow/artifacts.py`

Add `async def list(...)` that returns `[]` always.

### 1.3 InMemoryArtifactStore — `penguiflow/artifacts.py`

Add `async def list(...)`. Calls `_expire_old_artifacts()`, then filters `_artifacts` values using `_scope_matches` on each stored artifact's `ref.scope`. Returns `[stored.ref for stored in self._artifacts.values() if _scope_matches(stored.ref.scope, scope)]` when scope is provided, or all refs when scope is None.

### 1.4 PlaygroundArtifactStore — `penguiflow/state/in_memory.py`

Add `async def list(...)`. If `scope` is not None and `scope.session_id` is set, query only that session's inner `InMemoryArtifactStore.list(scope=scope)`. Otherwise iterate all session stores and aggregate results.

**Lock strategy:** Snapshot the stores dict under the lock, then iterate and call `list()` on each store **outside** the lock. This matches the existing pattern used by `get`, `put_bytes`, etc. — acquire `self._lock` briefly to copy references, then call async store methods without holding the lock.

```python
async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
    if scope is not None and scope.session_id is not None:
        async with self._lock:
            store = self._stores.get(scope.session_id)
        if store is None:
            return []
        return await store.list(scope=scope)
    # No session filter — snapshot all stores, iterate outside lock
    async with self._lock:
        stores = list(self._stores.values())
    results: list[ArtifactRef] = []
    for store in stores:
        results.extend(await store.list(scope=scope))
    return results
```

Import `_scope_matches` is NOT needed here — delegate to the inner `InMemoryArtifactStore.list()` which handles filtering.

### 1.5 `_EventEmittingArtifactStoreProxy` — `penguiflow/planner/artifact_handling.py`

Simple delegation: `return await self._store.list(scope=scope)` (no event needed for reads).

---

## Enhancement 2: Rename `artifacts` → `_artifacts` (internal access)

This step is a pure mechanical rename. After this step, all existing code continues to work identically — `_artifacts` returns the same raw `ArtifactStore`/proxy it always did. Run `pytest`, `ruff check`, and `mypy` after this step to confirm nothing broke before moving on.

> **ATOMICITY:** All sub-steps §2.1 through §2.10 must be applied together before running the checkpoint. Applying §2.1 alone (renaming the protocol property) without updating all implementations and call sites will break tests and type checks. Do NOT run `pytest`/`ruff`/`mypy` until every §2.x sub-step is complete.

### 2.1 Update `ToolContext` Protocol — `penguiflow/planner/context.py`

- Rename the existing `artifacts` property to `_artifacts` (same return type: `ArtifactStore`)
- Do **NOT** add the `ScopedArtifacts` import here — defer that to §3.3 (Enhancement 3) when the class actually exists. Adding it now under `TYPE_CHECKING` would break mypy at the Enhancement 2 checkpoint.

```python
@property
def _artifacts(self) -> ArtifactStore:
    """Raw artifact store for framework-internal use."""
```

### 2.2 Rename in `_PlannerContext` — `penguiflow/planner/planner_context.py`

- Rename current `artifacts` property to `_artifacts` (returns the raw `_EventEmittingArtifactStoreProxy`)
- **CRITICAL — `kv` property call site (line 102):** The `kv` property passes `artifacts=self.artifacts` to `SessionKVFacade`. This must be updated to `artifacts=self._artifact_proxy` (referencing the raw proxy directly). Using `self._artifacts` would also work, but `self._artifact_proxy` is more explicit and avoids going through the property layer. **If this site is not updated:** after Enhancement 2, `self.artifacts` will raise `AttributeError` (property no longer exists). After Enhancement 3, `self.artifacts` would pass a `ScopedArtifacts` facade to `SessionKVFacade` which expects `ArtifactStore` — the facade has different method names (`upload`/`download` vs `put_text`/`get`) and would break at runtime.

### 2.3 Rename in `ToolJobContext` — `penguiflow/sessions/tool_jobs.py`

- Rename its current `artifacts` property to `_artifacts` (keeps returning the raw `ArtifactStore`)
- Rename internal attribute `self._artifacts` → `self._artifacts_store` to avoid collision with the new `_artifacts` property name. This includes updating the `__init__` assignment:
  - **Before:** `self._artifacts = artifacts or NoOpArtifactStore()`
  - **After:** `self._artifacts_store = artifacts or NoOpArtifactStore()`
- **`kv` property (line 71):** The `kv` property currently has `artifacts=self._artifacts`, referencing the **instance attribute**. After the rename, the instance attribute is `self._artifacts_store`, and `self._artifacts` now resolves to the new **property** `_artifacts` (which returns `self._artifacts_store`). This still produces the correct value (the raw `ArtifactStore`), so **no change is needed on line 71**. Do NOT rename it to `self._artifacts_store` — leaving it as `self._artifacts` is correct and consistent with the property-based access pattern.

### 2.4 Migrate all call sites from `ctx.artifacts` to `ctx._artifacts`

**`penguiflow/tools/node.py`** (9 sites):
- Lines 984, 1093, 1119, 1193: `ctx.artifacts.put_bytes(...)` → `ctx._artifacts.put_bytes(...)`
- Lines 1308, 1343: `ctx.artifacts.put_text(...)` → `ctx._artifacts.put_text(...)`
- Lines 1671, 1685: `ctx.artifacts.put_bytes(...)` / `ctx.artifacts.put_text(...)` → `ctx._artifacts.*`
- Line 1615: `artifact_store=ctx.artifacts` → `artifact_store=ctx._artifacts`

**`penguiflow/sessions/tool_jobs.py`** (1 site):
- Line 275: `artifact_store=ctx.artifacts` → `artifact_store=ctx._artifacts`

### 2.4b Verify completeness with codebase-wide grep

After migrating the listed sites, run a codebase-wide search to ensure no references were missed:

```bash
grep -rn 'ctx\.artifacts' penguiflow/ tests/
grep -rn '\.artifacts' penguiflow/planner/ penguiflow/sessions/ penguiflow/tools/ penguiflow/cli/ | grep -v '_artifacts' | grep -v '__pycache__'
```

Any remaining hits (excluding `__pycache__`, documentation, and `_artifacts` references) must be migrated before proceeding. If new sites are found, add them to this section and migrate them.

**Expected non-code hit to ignore:** `penguiflow/planner/context.py` line ~48 contains `ctx.artifacts.put_bytes(...)` inside a **docstring** example on the `_artifacts` property (formerly `artifacts`). This is NOT a code call site — do NOT migrate it here. It will be updated in §3.3 when the new `artifacts` property is added with a `ctx.artifacts.upload(...)` example.

### 2.5 Update `DummyContext` in `tests/test_rich_output_nodes.py`

The `DummyContext` class (line 23) is a test fixture that satisfies the `ToolContext` protocol:

- Rename `self._artifacts` attribute → `self._artifacts_store` (to avoid collision with new `_artifacts` property). Update the `__init__` assignment:
  - **Before:** `self._artifacts = InMemoryArtifactStore()`
  - **After:** `self._artifacts_store = InMemoryArtifactStore()`
- Rename existing `artifacts` property → `_artifacts` (returns `self._artifacts_store`, the raw `InMemoryArtifactStore`)
- Line 193: `ctx.artifacts.put_text(...)` → `ctx._artifacts.put_text(...)`

### 2.6 Update `DummyCtx` in `tests/test_toolnode_phase1.py`

The `DummyCtx` class (line 33) satisfies the `ToolContext` protocol:

- Rename `self._artifacts` attribute (line 43) → `self._artifacts_store` (to avoid collision with new `_artifacts` property — same pattern as §2.3 and §2.5). Update the `__init__` assignment:
  - **Before:** `self._artifacts = artifact_store or InMemoryArtifactStore()`
  - **After:** `self._artifacts_store = artifact_store or InMemoryArtifactStore()`
- Rename `artifacts` property (line 58) → `_artifacts` (returns `self._artifacts_store`, the raw `InMemoryArtifactStore`)

### 2.7 Update `DummyCtx` in `tests/test_toolnode_phase2.py`

The `DummyCtx` class (line 37) satisfies the `ToolContext` protocol:

- Rename `self._artifacts` attribute (line 44) → `self._artifacts_store` (to avoid collision with new `_artifacts` property — same pattern as §2.3 and §2.5). Update the `__init__` assignment:
  - **Before:** `self._artifacts = artifact_store or InMemoryArtifactStore()`
  - **After:** `self._artifacts_store = artifact_store or InMemoryArtifactStore()`
- Rename `artifacts` property (line 59) → `_artifacts` (returns `self._artifacts_store`, the raw `InMemoryArtifactStore`)

### 2.8 Update `DummyContext` in `tests/test_task_tools.py`

The `DummyContext` class (line 10) satisfies the `ToolContext` protocol:

- Rename `artifacts` property (line 27) → `_artifacts` (keeps raising `RuntimeError("not_used")`)

### 2.9 Update `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py`

The `_FakeCtx` class (line 166) satisfies the `ToolContext` protocol:

- Rename `artifacts` property (line 185) → `_artifacts`
- Change the return value from `None` to `NoOpArtifactStore()` for protocol correctness (`_artifacts` must return `ArtifactStore`, not `None`). Import `NoOpArtifactStore` from `penguiflow.artifacts`.

### 2.10 Update `MinimalCtx` in `penguiflow/cli/playground.py`

**CRITICAL:** `MinimalCtx` (line 2122) creates a minimal context passed to `tool_node.read_resource(uri, ctx)`. Inside `read_resource`, line 1615 of `node.py` accesses `ctx.artifacts` (which becomes `ctx._artifacts` after this rename). If `MinimalCtx` is not updated, playground resource reads will break at runtime.

- Rename `self._artifacts` attribute (in `__init__`, line 2124) → `self._artifacts_store` (to avoid collision with new `_artifacts` property — same pattern as §2.3 and §2.5). Update the `__init__` assignment:
  - **Before:** `self._artifacts = artifacts`
  - **After:** `self._artifacts_store = artifacts`
- Rename `artifacts` property (line 2127) → `_artifacts` (returns `self._artifacts_store`)

**Checkpoint:** After completing Enhancement 2, run `uv run pytest tests/`, `uv run ruff check .`, and `uv run mypy`. All must pass with no new failures before proceeding to Enhancement 3.

---

## Enhancement 3: `ScopedArtifacts` Facade

### 3.1 Facade class — `penguiflow/artifacts.py`

New class `ScopedArtifacts` placed **between the Protocol section (ends line ~218) and the Discovery section** (`discover_artifact_store`, line ~222). This keeps the facade logically adjacent to the protocol it wraps. Add `"ScopedArtifacts"` to `__all__`.

```python
class ScopedArtifacts:
    """Scoped facade over ArtifactStore for tool developers.

    Automatically injects tenant_id/user_id/session_id/trace_id on writes
    and enforces scope on reads. Immutable after construction.
    """

    __slots__ = ("_store", "_scope", "_read_scope")

    def __init__(
        self,
        store: ArtifactStore,
        *,
        tenant_id: str | None,
        user_id: str | None,
        session_id: str | None,
        trace_id: str | None,
    ) -> None:
        # IMPORTANT: Must use object.__setattr__ because __setattr__ is
        # overridden to raise AttributeError (immutability).
        object.__setattr__(self, "_store", store)
        object.__setattr__(
            self,
            "_scope",
            ArtifactScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
            ),
        )
        # Read scope: same as _scope but without trace_id, so reads
        # return all artifacts for the tenant/user/session.
        object.__setattr__(
            self,
            "_read_scope",
            ArtifactScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=None,
            ),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ScopedArtifacts is immutable")
```

**Public methods** (no `scope` parameter — it's always injected):

| Method | Delegates to | Notes |
|---|---|---|
| `upload(data, *, mime_type, filename, namespace, meta) -> ArtifactRef` | `_store.put_bytes()` if `isinstance(data, bytes)`, `_store.put_text()` if `isinstance(data, str)` | Single entry point; auto-detects type; always passes `scope=self._scope` (full scope including `trace_id`) |
| `download(artifact_id) -> bytes \| None` | `_store.get_ref()` then scope check, then `_store.get()` | Returns content only if scope check passes |
| `get_metadata(artifact_id) -> ArtifactRef \| None` | `_store.get_ref()` then scope check | Returns metadata only if scope check passes |
| `list() -> list[ArtifactRef]` | `_store.list(scope=self._read_scope)` | Scoped on tenant/user/session only (not trace) |
| `exists(artifact_id) -> bool` | `_store.get_ref()` then scope check | True only if artifact exists AND scope check passes |
| `delete(artifact_id) -> bool` | `_store.get_ref()` then scope check, then `_store.delete()` | Deletes only if scope check passes |

**Explicit method signatures:**

```python
@property
def scope(self) -> ArtifactScope:
    """Read-only access to the fixed scope."""
    return self._scope

async def upload(
    self,
    data: bytes | str,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
    namespace: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ArtifactRef:
    """Store data and return an ArtifactRef. Scope is always injected."""
    ...

async def download(self, artifact_id: str) -> bytes | None:
    """Retrieve artifact bytes by ID (scope-checked)."""
    ...

async def get_metadata(self, artifact_id: str) -> ArtifactRef | None:
    """Retrieve artifact metadata by ID (scope-checked)."""
    ...

async def list(self) -> list[ArtifactRef]:
    """List artifacts matching this facade's tenant/user/session (not trace)."""
    ...

async def exists(self, artifact_id: str) -> bool:
    """Check if an artifact exists and passes scope check."""
    ...

async def delete(self, artifact_id: str) -> bool:
    """Delete an artifact if it passes scope check."""
    ...

def _check_scope(self, ref: ArtifactRef) -> bool:
    """Check if an artifact's scope is compatible with this facade's scope.

    Access control semantics (NOT filtering):
    - If artifact has no scope (ref.scope is None) → PASS (unrestricted artifact).
    - For each of tenant_id, user_id, session_id (NOT trace_id):
      if BOTH facade field and artifact field are non-None, they must match.
      If either is None, that dimension passes.
    """
    if ref.scope is None:
        return True
    for field in ("tenant_id", "user_id", "session_id"):
        facade_val = getattr(self._scope, field)
        artifact_val = getattr(ref.scope, field)
        if facade_val is not None and artifact_val is not None and facade_val != artifact_val:
            return False
    return True
```

**Scope check rules for `download`/`get_metadata`/`list`/`exists`/`delete` (reads & deletes):**

These methods enforce scope on `tenant_id`, `user_id`, and `session_id` only — **`trace_id` is intentionally excluded** from read/delete checks. This allows agents to see all artifacts belonging to their tenant/user/session, regardless of which trace produced them.

For `download`/`get_metadata`/`exists`/`delete`, the method first calls `_store.get_ref(artifact_id)` to retrieve the artifact's metadata. **If `get_ref` returns `None` (artifact does not exist), return immediately:** `None` for `download`/`get_metadata`, `False` for `exists`/`delete` — no scope check needed. Otherwise, compare the facade's scope against the artifact's scope:

- If the artifact has no scope (`ref.scope is None`), the check **passes** (unrestricted artifact).
- For each of `tenant_id`, `user_id`, `session_id`: if the facade's field is non-None AND the artifact's field is non-None, they must match. If either is None, that dimension passes.
- If the check fails, return `None` (for download/get_metadata), `False` (for exists/delete).

For `list()`, build a **read scope** (`self._read_scope`) that copies tenant/user/session from `self._scope` but sets `trace_id=None`. This is a private cached property (or computed once in `__init__` and stored in `__slots__`). This ensures `_store.list(scope=...)` returns all artifacts for the tenant/user/session, not just the current trace.

Add a private `_check_scope(self, ref: ArtifactRef) -> bool` helper method on the class to implement the per-artifact check.

**WARNING — `_check_scope` must NOT delegate to `_scope_matches`:** These two functions have intentionally different semantics for `None` artifact fields:
- `_scope_matches` (used by `list()` filtering): artifact field=`None` + filter field=non-`None` → **FAIL** (the artifact doesn't positively match the filter)
- `_check_scope` (used by `download`/`exists`/`delete` access control): artifact field=`None` + facade field=non-`None` → **PASS** (artifact has no restriction on that dimension, so access is allowed)

Do NOT reuse `_scope_matches` inside `_check_scope`. Implement them independently.

**Note on `upload` (writes):** `upload` still passes the **full** `self._scope` (including `trace_id`) to `put_bytes`/`put_text`, so newly created artifacts are tagged with the trace that produced them. Only reads are broadened.

The `upload` method accepts `data: bytes | str`. If `str`, it defaults `mime_type` to `"text/plain"` (matching `put_text` behavior). If `bytes`, `mime_type` defaults to `None`.

**IMPORTANT — `mime_type` defaulting:** Because `put_text`'s signature is `mime_type: str = "text/plain"` (not `str | None`), `upload` must NOT pass `None` through when data is `str`. The implementation must resolve the default before delegating:

```python
if isinstance(data, str):
    return await self._store.put_text(
        data,
        mime_type=mime_type or "text/plain",  # resolve None → "text/plain"
        filename=filename,
        namespace=namespace,
        scope=self._scope,
        meta=meta,
    )
else:
    return await self._store.put_bytes(
        data,
        mime_type=mime_type,  # None is valid for bytes
        filename=filename,
        namespace=namespace,
        scope=self._scope,
        meta=meta,
    )
```

Read-only `scope` property exposes the fixed `ArtifactScope`.

### 3.2 Wire into `_PlannerContext` — `penguiflow/planner/planner_context.py`

- Add `"_scoped_artifacts"` to `__slots__`
- In `__init__`, after constructing `_artifact_proxy`, build the facade using `self._tool_context` (already set on the line above as `dict(trajectory.tool_context or {})`):

```python
self._scoped_artifacts = ScopedArtifacts(
    self._artifact_proxy,
    tenant_id=str(self._tool_context["tenant_id"]) if self._tool_context.get("tenant_id") is not None else None,
    user_id=str(self._tool_context["user_id"]) if self._tool_context.get("user_id") is not None else None,
    session_id=str(self._tool_context["session_id"]) if self._tool_context.get("session_id") is not None else None,
    trace_id=str(self._tool_context["trace_id"]) if self._tool_context.get("trace_id") is not None else None,
)
```

- Add new `artifacts` property returning `self._scoped_artifacts`
- Update import: add `ScopedArtifacts` to the import from `..artifacts`

### 3.3 Add `artifacts` to `ToolContext` Protocol — `penguiflow/planner/context.py`

- Add `ScopedArtifacts` to the `TYPE_CHECKING` import block: `from penguiflow.artifacts import ArtifactStore, ScopedArtifacts` (deferred from §2.1 — the class now exists after §3.1)
- Add new `artifacts` property with return type `ScopedArtifacts` (alongside the existing `_artifacts`)

```python
@property
def artifacts(self) -> ScopedArtifacts:
    """Scoped artifact facade for tool developers.

    Example:
        ref = await ctx.artifacts.upload(
            pdf_bytes,
            mime_type="application/pdf",
            filename="report.pdf",
        )
        return {"artifact": ref, "summary": "Downloaded PDF"}
    """
```

- **Update the docstring** on the `artifacts` property to show `ctx.artifacts.upload(...)` instead of the old `ctx.artifacts.put_bytes(...)` example (current lines 42-54)

### 3.4 Add `artifacts` to `ToolJobContext` — `penguiflow/sessions/tool_jobs.py`

- Build the `ScopedArtifacts` in `__init__` using scope info from `tool_context`:

```python
self._scoped_artifacts = ScopedArtifacts(
    self._artifacts_store,
    tenant_id=str(tool_context["tenant_id"]) if tool_context.get("tenant_id") is not None else None,
    user_id=str(tool_context["user_id"]) if tool_context.get("user_id") is not None else None,
    session_id=str(tool_context["session_id"]) if tool_context.get("session_id") is not None else None,
    trace_id=str(tool_context["trace_id"]) if tool_context.get("trace_id") is not None else None,
)
```

- Add new `artifacts` property returning `self._scoped_artifacts`
- Import `ScopedArtifacts` from `penguiflow.artifacts`

### 3.5 Update `DummyContext` in `tests/test_rich_output_nodes.py`

- Build `ScopedArtifacts` in `__init__` and add `artifacts` property:

```python
self._scoped_artifacts = ScopedArtifacts(
    self._artifacts_store,
    tenant_id=None,
    user_id=None,
    session_id=None,
    trace_id=None,
)
```

- Import `ScopedArtifacts` from `penguiflow.artifacts`

### 3.6 Update `DummyCtx` in `tests/test_toolnode_phase1.py`

- Add `artifacts` property returning a `ScopedArtifacts` facade wrapping `self._artifacts_store` (renamed in §2.6):

```python
@property
def artifacts(self):
    from penguiflow.artifacts import ScopedArtifacts
    return ScopedArtifacts(
        self._artifacts_store,
        tenant_id=None, user_id=None, session_id=None, trace_id=None,
    )
```

Note: This can be a simple inline property since this is a test fixture. Alternatively, build once in `__init__` for consistency. Choose whichever matches the existing style of the fixture.

### 3.7 Update `DummyCtx` in `tests/test_toolnode_phase2.py`

- Same pattern as §3.6 — add `artifacts` property returning `ScopedArtifacts` wrapping `self._artifacts_store` (renamed in §2.7).

### 3.8 Update `DummyContext` in `tests/test_task_tools.py`

- Add `artifacts` property. Since this fixture never actually uses artifacts (current impl raises `RuntimeError`), it still needs a property that satisfies the protocol. Options:
  - Build a `ScopedArtifacts` wrapping a `NoOpArtifactStore`
  - Or raise `RuntimeError("not_used")` (matching existing pattern) — but this won't satisfy the return type

Recommended: Build a `ScopedArtifacts` wrapping `NoOpArtifactStore` with all-None scope fields.

### 3.9 Update `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py`

- Add `artifacts` property. Same situation as §3.8 — currently returns `None` and is never called. Build a `ScopedArtifacts` wrapping `NoOpArtifactStore` with all-None scope fields.

### 3.10 Update `MinimalCtx` in `penguiflow/cli/playground.py`

- The `MinimalCtx` is only used to pass to `read_resource()`, which accesses `ctx._artifacts` (after Enhancement 2). It does NOT need a `ScopedArtifacts` facade because it is only used for internal framework calls, not exposed to tool developers.
- **No `artifacts` property needed here** — only `_artifacts` (already renamed in §2.10) is accessed.
- **mypy note:** `read_resource()` types its `ctx` parameter as `ToolContext` (line 1586 of `node.py`). `MinimalCtx` does NOT satisfy the full `ToolContext` protocol (it is missing `llm_context`, `tool_context`, `meta`, `artifacts`, `pause`, `emit_chunk`, `emit_artifact`). This is a **pre-existing** protocol violation — it already fails to satisfy `ToolContext` before this plan's changes, and mypy currently passes because the local class definition inside a function body receives lenient checking. This plan does NOT introduce a new mypy violation here; it preserves the existing behavior.

### 3.11 Update Jinja templates for `penguiflow new`

Two template files have `DummyToolContext` that should be updated for protocol compliance:

**`penguiflow/cli/templates/conftest.py.jinja`** and **`penguiflow/templates/new/react/tests/conftest.py.jinja`**:

Both templates have identical `DummyToolContext` dataclass. Neither currently has an `artifacts` property or `emit_artifact` method. Add simple `None`-returning stubs for `_artifacts`, `artifacts`, and `emit_artifact`. Generated projects may not have `penguiflow` importable at test time, so avoid importing `ScopedArtifacts` or `NoOpArtifactStore`.

**Note:** The `emit_artifact` stub is a **pre-existing gap** — the templates were already missing this method from the `ToolContext` protocol before this plan. It is bundled here for convenience since we are already touching these templates to add `_artifacts` and `artifacts`:

```python
# Add to DummyToolContext (after the existing fields/methods):

@property
def _artifacts(self) -> Any:
    """Raw artifact store stub — not used in generated project tests."""
    return None

@property
def artifacts(self) -> Any:
    """Scoped artifacts stub — not used in generated project tests."""
    return None

async def emit_artifact(
    self,
    stream_id: str,
    chunk: Any,
    *,
    done: bool = False,
    artifact_type: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Artifact streaming stub — not used in generated project tests."""
    del stream_id, chunk, done, artifact_type, meta
```

### 3.12 Update exports

- `penguiflow/artifacts.py` `__all__` — add `"ScopedArtifacts"`
- `penguiflow/__init__.py` — artifacts are NOT currently re-exported there, so no change needed

---

## Enhancement 4: Update Documentation

Several documentation files reference the old `ctx.artifacts.put_bytes()`/`put_text()` API which is replaced by `ctx.artifacts.upload()` for tool developers. Update these references:

**Scope rule:** Only update references that describe the **tool developer** API (`ctx.artifacts.*`). References that describe the **internal framework pipeline** (e.g., what ToolNode's extraction layers do internally) should remain as-is, because internal code still uses `ctx._artifacts.put_bytes()`/`put_text()`.

### 4.0.1 `docs/tools/artifacts-guide.md`

- Line 274: `ref = await ctx.artifacts.put_bytes(` → `ref = await ctx.artifacts.upload(` — this is a tool-developer code example, so update it.
- Line 829: table entry `ctx.artifacts.put_bytes()` in the "Binary Storage" row — this describes the **internal pipeline component**, NOT the tool-developer API. **Do NOT update this row.** It documents how the framework stores artifacts internally.
- Update any surrounding prose that describes `put_bytes`/`put_text` as the tool developer interface, but leave internal architecture descriptions unchanged.

### 4.0.2 `docs/tools/artifacts-and-resources.md`

- Lines 70-71: `ctx.artifacts.put_text(...)` / `ctx.artifacts.put_bytes(...)` — these describe what ToolNode's **internal extraction pipeline** does (clamping large strings, extracting binary content). These are internal framework calls, NOT tool-developer API. **Do NOT update these lines.** After this plan, the internal pipeline uses `ctx._artifacts.put_text()`/`put_bytes()`.

### 4.0.3 `docs/planner/tool-design.md`

- Line 80: `ctx.artifacts` reference — this says "store them in `ctx.artifacts` and return a compact reference". This IS tool-developer guidance. After this plan, `ctx.artifacts` returns the `ScopedArtifacts` facade, so the reference is **correct as-is**. The method name is `upload()`, so optionally expand to `ctx.artifacts.upload(...)` for clarity, but the current text is not wrong.

---

## Tests — `tests/test_artifacts.py`

All new tests go in the existing `tests/test_artifacts.py` file alongside the current `TestArtifactRef`, `TestNoOpArtifactStore`, etc. classes.

### 4.1 `ArtifactStore.list` tests

**Class: `TestInMemoryArtifactStoreList`**

| Test | Description |
|---|---|
| `test_list_empty_store` | `list()` on a fresh store returns `[]`. |
| `test_list_no_scope_returns_all` | Store several artifacts (some with scope, some without). `list(scope=None)` returns all of them. |
| `test_list_filters_by_tenant_id` | Store artifacts with different `tenant_id` values. `list(scope=ArtifactScope(tenant_id="t1"))` returns only those matching `t1`. |
| `test_list_filters_by_session_id` | Same pattern but filtering on `session_id`. |
| `test_list_filters_by_user_id` | Same pattern but filtering on `user_id`. |
| `test_list_filters_multiple_dimensions` | Filter on `tenant_id` + `session_id` together — only artifacts matching both are returned. |
| `test_list_unscoped_artifacts_match_none_filter` | Artifacts with `scope=None` match a filter where all fields are `None`, but fail when any filter field is non-None. |
| `test_list_expired_artifacts_excluded` | Store an artifact, manipulate its `created_at` to be expired, call `list()` — expired artifact is not returned. |

**Class: `TestNoOpArtifactStoreList`**

| Test | Description |
|---|---|
| `test_list_always_empty` | `NoOpArtifactStore().list()` returns `[]` regardless of scope. |

**Class: `TestPlaygroundArtifactStoreList`** (in `tests/test_artifacts.py` alongside other artifact store tests)

| Test | Description |
|---|---|
| `test_list_scoped_to_session` | Store artifacts in two different sessions. `list(scope=ArtifactScope(session_id="s1"))` returns only session `s1` artifacts. |
| `test_list_no_scope_aggregates_all_sessions` | `list(scope=None)` returns artifacts from all sessions. |

**Class: `TestEventEmittingProxyList`**

| Test | Description |
|---|---|
| `test_list_delegates_to_inner_store` | Verify `_EventEmittingArtifactStoreProxy.list()` delegates to the wrapped store and returns the same results. |

### 4.2 `_scope_matches` helper tests

**Class: `TestScopeMatches`**

| Test | Description |
|---|---|
| `test_none_artifact_scope_matches_all_none_filter` | `_scope_matches(None, ArtifactScope())` → `True`. |
| `test_none_artifact_scope_fails_non_none_filter` | `_scope_matches(None, ArtifactScope(tenant_id="t1"))` → `False`. |
| `test_exact_match` | All fields match → `True`. |
| `test_partial_filter_match` | Filter has `tenant_id="t1"` and rest `None`; artifact has `tenant_id="t1"` + other fields set → `True`. |
| `test_mismatch` | Filter has `tenant_id="t1"`, artifact has `tenant_id="t2"` → `False`. |
| `test_none_filter_field_matches_any` | Filter field is `None` → matches any artifact value for that dimension. |

### 4.3 `ScopedArtifacts` facade tests

**Class: `TestScopedArtifactsImmutability`**

| Test | Description |
|---|---|
| `test_cannot_reassign_store` | `facade._store = ...` raises `AttributeError`. |
| `test_cannot_reassign_scope` | `facade._scope = ...` raises `AttributeError`. |
| `test_cannot_reassign_read_scope` | `facade._read_scope = ...` raises `AttributeError`. |
| `test_cannot_add_new_attribute` | `facade.foo = ...` raises `AttributeError`. |
| `test_scope_property_returns_correct_value` | `facade.scope` returns the `ArtifactScope` passed at construction. |

**Class: `TestScopedArtifactsUpload`**

| Test | Description |
|---|---|
| `test_upload_bytes` | `upload(b"data", mime_type="application/pdf")` stores via `put_bytes` and returns an `ArtifactRef` with the full scope (including `trace_id`). |
| `test_upload_str` | `upload("text data")` stores via `put_text`, defaults `mime_type` to `"text/plain"`, returns `ArtifactRef` with full scope. |
| `test_upload_str_custom_mime` | `upload("data", mime_type="text/csv")` respects the provided mime type. |
| `test_upload_passes_namespace` | `upload(b"data", namespace="my_tool")` forwards `namespace` to the underlying store. |
| `test_upload_passes_meta` | `upload(b"data", meta={"key": "val"})` forwards `meta` to the underlying store. |
| `test_upload_injects_full_scope` | Create facade with `tenant_id="t1"`, `trace_id="tr1"`. Upload an artifact. Verify the stored `ArtifactRef.scope` contains both `tenant_id="t1"` and `trace_id="tr1"`. |

**Class: `TestScopedArtifactsDownload`**

| Test | Description |
|---|---|
| `test_download_same_scope` | Upload via facade, then `download()` returns the bytes. |
| `test_download_different_trace_same_session` | Upload with `trace_id="tr1"`, create a new facade with `trace_id="tr2"` but same tenant/user/session. `download()` succeeds (trace is not checked on reads). |
| `test_download_different_tenant_denied` | Upload with `tenant_id="t1"`, create facade with `tenant_id="t2"`. `download()` returns `None`. |
| `test_download_different_session_denied` | Upload with `session_id="s1"`, create facade with `session_id="s2"`. `download()` returns `None`. |
| `test_download_unscoped_artifact_allowed` | Store an artifact with `scope=None` directly via raw store. Facade `download()` succeeds (unscoped artifacts are accessible to all). |
| `test_download_nonexistent_returns_none` | `download("nonexistent_id")` returns `None`. |

**Class: `TestScopedArtifactsGetMetadata`**

| Test | Description |
|---|---|
| `test_get_metadata_returns_ref` | Upload, then `get_metadata()` returns the `ArtifactRef` with correct fields. |
| `test_get_metadata_different_trace_allowed` | Same as download: different trace, same tenant/user/session → returns ref. |
| `test_get_metadata_different_tenant_denied` | Different `tenant_id` → returns `None`. |
| `test_get_metadata_nonexistent_returns_none` | Unknown ID → `None`. |

**Class: `TestScopedArtifactsList`**

| Test | Description |
|---|---|
| `test_list_returns_own_scope_artifacts` | Upload artifacts via facade. `list()` returns them. |
| `test_list_excludes_different_tenant` | Store artifacts for two tenants via raw store. Facade scoped to `tenant_id="t1"`. `list()` returns only `t1` artifacts. |
| `test_list_includes_all_traces` | Upload artifacts with two different `trace_id` values (same tenant/user/session). Facade's `list()` returns both — trace is not filtered on reads. |
| `test_list_empty` | No artifacts stored → `list()` returns `[]`. |

**Class: `TestScopedArtifactsExists`**

| Test | Description |
|---|---|
| `test_exists_same_scope` | Upload, `exists()` returns `True`. |
| `test_exists_different_tenant_denied` | Different tenant → `False`. |
| `test_exists_different_trace_allowed` | Different trace, same tenant/user/session → `True`. |
| `test_exists_nonexistent` | Unknown ID → `False`. |

**Class: `TestScopedArtifactsDelete`**

| Test | Description |
|---|---|
| `test_delete_same_scope` | Upload, `delete()` returns `True`, artifact is gone. |
| `test_delete_different_tenant_denied` | Different tenant → `False`, artifact still exists in raw store. |
| `test_delete_different_trace_allowed` | Different trace, same tenant/user/session → `True`, artifact is deleted. |
| `test_delete_nonexistent` | Unknown ID → `False`. |

---

## Files to Modify

| File | Enhancement | Changes |
|---|---|---|
| `penguiflow/artifacts.py` | 1, 3 | Add `list` to protocol; `_scope_matches` helper; impl in NoOp + InMemory; `ScopedArtifacts` class; update `__all__` |
| `penguiflow/state/in_memory.py` | 1 | Add `list` to `PlaygroundArtifactStore` |
| `penguiflow/planner/artifact_handling.py` | 1 | Add `list` delegation to `_EventEmittingArtifactStoreProxy` |
| `penguiflow/planner/context.py` | 2, 3 | Rename `artifacts` → `_artifacts`; later add new `artifacts` property returning `ScopedArtifacts`; add imports |
| `penguiflow/planner/planner_context.py` | 2, 3 | Rename `artifacts` → `_artifacts`; update `kv` property (line 102) `self.artifacts` → `self._artifact_proxy`; later add `_scoped_artifacts` slot + facade + new `artifacts` property |
| `penguiflow/sessions/tool_jobs.py` | 2, 3 | Rename `artifacts` → `_artifacts` + attribute rename (`self._artifacts` → `self._artifacts_store`); later add `ScopedArtifacts` facade + new `artifacts` property; 1 call-site migration (line 275) |
| `penguiflow/tools/node.py` | 2 | 9 sites: `ctx.artifacts` → `ctx._artifacts` |
| `penguiflow/cli/playground.py` | 2 | Rename `artifacts` → `_artifacts` + attribute rename (`self._artifacts` → `self._artifacts_store`) in `MinimalCtx`. **Critical:** without this, playground resource reads break at runtime. |
| `tests/test_rich_output_nodes.py` | 2, 3 | Rename attribute + property in `DummyContext`; `ctx.artifacts.put_text` → `ctx._artifacts.put_text`; later add `ScopedArtifacts` facade |
| `tests/test_toolnode_phase1.py` | 2, 3 | Rename attribute (`self._artifacts` → `self._artifacts_store`) + property (`artifacts` → `_artifacts`) in `DummyCtx`; later add `artifacts` property returning `ScopedArtifacts` |
| `tests/test_toolnode_phase2.py` | 2, 3 | Rename attribute (`self._artifacts` → `self._artifacts_store`) + property (`artifacts` → `_artifacts`) in `DummyCtx`; later add `artifacts` property returning `ScopedArtifacts` |
| `tests/test_task_tools.py` | 2, 3 | Rename `artifacts` → `_artifacts` in `DummyContext` (line 27); later add `artifacts` property returning `ScopedArtifacts` |
| `tests/a2a/test_a2a_planner_tools.py` | 2, 3 | Rename `artifacts` → `_artifacts` in `_FakeCtx` (line 185), change return from `None` to `NoOpArtifactStore()`; later add `artifacts` property returning `ScopedArtifacts` |
| `penguiflow/cli/templates/conftest.py.jinja` | 3 | Add `_artifacts` and `artifacts` `None`-stub properties to `DummyToolContext` |
| `penguiflow/templates/new/react/tests/conftest.py.jinja` | 3 | Add `_artifacts` and `artifacts` `None`-stub properties to `DummyToolContext` |
| `docs/tools/artifacts-guide.md` | 4 | Update tool-developer code example (line 274) to `ctx.artifacts.upload()`. Leave internal pipeline table row (line 829) unchanged. |
| `docs/tools/artifacts-and-resources.md` | 4 | **No changes needed** — lines 70-71 describe internal pipeline, not tool-developer API. |
| `docs/planner/tool-design.md` | 4 | **No changes needed** — line 80 `ctx.artifacts` reference is correct as-is (optionally expand to `ctx.artifacts.upload()` for clarity). |
| `tests/test_artifacts.py` | 1, 3 | See section "Tests" above — all test classes in §4.1–§4.3 |

---

## Verification

**All of the following must pass for this work to be considered complete:**

1. `uv run pytest tests/` — **no new failures** introduced. All tests that currently pass (2387) must continue to pass.
2. `uv run ruff check .` — **zero** lint errors.
3. `uv run mypy` — **zero** new type errors. If mypy reports any new error introduced by this work, it must be fixed.
4. Verify `ScopedArtifacts` scope immutability (cannot reassign `_scope` or `_store`)
5. Verify `upload` injects scope correctly (artifacts have correct tenant/user/session/trace)
6. Verify `list()` returns only scoped artifacts
7. Verify `download`/`get_metadata`/`exists`/`delete` enforce scope check on tenant/user/session only (not trace)
8. Verify scope check allows access to unscoped artifacts (scope=None)
9. Verify internal code (`node.py`, `tool_jobs.py`) still works via `ctx._artifacts`
10. Verify `ToolJobContext` satisfies updated `ToolContext` protocol

**Exit criteria:** Items 1, 2, and 3 are hard gates — if `pytest`, `ruff check`, or `mypy` report any new failure, the implementation is not done.

**Pre-existing failures (21 tests — allowed to remain failing):**
These fail due to missing `openai` module or unrelated Google provider issues. They are NOT caused by this work:
- `tests/test_databricks_provider.py::test_databricks_base_url_keeps_endpoint_segment`
- `tests/test_databricks_provider.py::test_databricks_structured_output_streaming_degrades_to_single_chunk`
- `tests/test_google_provider_streaming.py::test_google_provider_streaming_emits_deltas_for_cumulative_text`
- `tests/test_google_provider_streaming.py::test_google_provider_streaming_routes_thought_parts_to_reasoning`
- `tests/test_google_provider_streaming.py::test_google_provider_non_stream_separates_thought_parts`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderComplete::test_complete_simple`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderComplete::test_complete_extracts_reasoning_from_content_blocks`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderComplete::test_complete_timeout`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderComplete::test_complete_cancelled`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderStreaming::test_streaming_drops_response_format_and_adds_guidance`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderStreaming::test_streaming_with_tool_calls`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderStreaming::test_streaming_with_reasoning_list_content`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderStreaming::test_streaming_timeout`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_auth_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_rate_limit_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_context_length_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_server_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_bad_request_non_context_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_api_connection_error`
- `tests/test_llm_provider_databricks.py::TestDatabricksProviderErrorMapping::test_map_api_status_error_4xx`
- `tests/test_llm_provider_google.py::TestGoogleProviderBuildConfig::test_build_config_reasoning_effort_does_not_set_level_and_budget`
