# Plan: Documentation updates for issue-74 (ArtifactStore `list` + ScopedArtifacts)

## Context

Issue-74 added a `list` method to the `ArtifactStore` protocol and introduced a `ScopedArtifacts` facade. The implementation is complete, but several documentation files were not updated — and existing docs don't clearly communicate the **porcelain vs. plumbing** distinction:

- **Porcelain (tool/agent developers):** `ctx.artifacts` → `ScopedArtifacts` facade with `upload`/`download`/`list`/etc.
- **Plumbing (penguiflow internals):** `ctx._artifacts` → raw `ArtifactStore` with `put_bytes`/`put_text`/`get`/etc.

### mkdocs context

The published docs site (`mkdocs.yml`) **excludes** `spec/**`, `tools/artifacts-guide.md`, and root-level files (`REACT_PLANNER_INTEGRATION_GUIDE.md`, `TEMPLATING_QUICKGUIDE.md`). The **published pages** that mention artifacts are:

| Published page | Nav location | Status |
|---|---|---|
| `docs/tools/artifacts-and-resources.md` | Tools > Artifacts & resources | In plan (File 3) |
| `docs/planner/tool-design.md` | Planner > Tool design | In plan (File 4) |
| `docs/tools/configuration.md` | Tools > Configuration | In plan (File 6) |
| `docs/tools/statestore.md` | Tools > State store | In plan (File 8 — new) |
| `docs/planner/overview.md` | Planner > Overview | Generic mention only, no change needed |

### Implementation note

> **Line numbers are approximate references only (indicated by `~`).** Always match on the **exact text content** shown in each "Current:" block, not on absolute line numbers. Process edits **top-to-bottom within each file** — earlier insertions shift subsequent line numbers. When inserting blockquote notes, always add a **blank line before and after** the blockquote for proper markdown rendering.

---

## File 1: `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`

### 1.1 Add `list` to ArtifactStore protocol code block (line ~1136)

Insert **inside** the code block, between the `exists` method (line 1135) and the closing ``` (line 1136):

```python
    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter.
        None fields in scope = don't filter on that dimension.
        If scope is None, returns all artifacts.
        """
        ...
```

### 1.2 Fix scope enforcement note (line 1163)

**Current:**
> **Note:** Scope is stored by ArtifactStore but **enforcement happens at HTTP layer**.

**Replace with:**
> **Note:** Scope is stored by `ArtifactStore` and **enforced at two layers**: the `ScopedArtifacts` facade (application-level, for tool/agent developers via `ctx.artifacts`) and the HTTP layer (for API consumers). See [ScopedArtifacts Facade](#scopedartifacts-facade) below.

### 1.3 Add `ScopedArtifacts Facade` subsection

Insert after the updated note from 1.2 (line ~1163) and before `### ArtifactRetentionConfig` (line ~1165). Insert the content below verbatim (**do not** include the ```` fence delimiters — only the content between them):

````markdown
### ScopedArtifacts Facade

Tool and agent developers interact with artifacts through `ctx.artifacts` — an instance of `ScopedArtifacts`. This is the **porcelain** API. PenguiFlow internals (ToolNode extraction pipeline, playground, etc.) use `ctx._artifacts` — the raw `ArtifactStore` — which is the **plumbing** API.

```python
class ScopedArtifacts:
    """Scoped facade over ArtifactStore for tool developers.

    Automatically injects tenant_id/user_id/session_id/trace_id on writes
    and enforces scope on reads. Immutable after construction.
    """

    def __init__(
        self,
        store: ArtifactStore,
        *,
        tenant_id: str | None,
        user_id: str | None,
        session_id: str | None,
        trace_id: str | None,
    ) -> None: ...

    @property
    def scope(self) -> ArtifactScope: ...

    async def upload(self, data: bytes | str, *, mime_type=None, filename=None, namespace=None, meta=None) -> ArtifactRef: ...
    async def download(self, artifact_id: str) -> bytes | None: ...
    async def get_metadata(self, artifact_id: str) -> ArtifactRef | None: ...
    async def list(self) -> list[ArtifactRef]: ...
    async def exists(self, artifact_id: str) -> bool: ...
    async def delete(self, artifact_id: str) -> bool: ...
```

#### Scope semantics

| Operation | Scope behavior |
|---|---|
| `upload()` | Injects **full scope** (tenant + user + session + trace) into the stored artifact |
| `download()` / `get_metadata()` / `exists()` / `delete()` | Checks **tenant + user + session only** (not trace) — so reads return artifacts across traces within the same session |
| `list()` | Delegates to `ArtifactStore.list(scope=...)` with **tenant + user + session** (not trace) |

#### Immutability

`ScopedArtifacts` is immutable after construction — `__setattr__` raises `AttributeError`. The `_store`, `_scope`, and `_read_scope` attributes are set via `object.__setattr__` in `__init__` only.
````

### 1.4 Update Implementation Checklist (line ~2278)

In `### Artifact Store (if applicable)`, after the last existing checklist item (`- [ ] Session-scoped access patterns supported`), add:
```
- [ ] `list(scope=...)` method implemented on ArtifactStore
```

---

## File 2: `docs/tools/artifacts-guide.md`

### 2.1 Clarify porcelain API at line ~264

**Current (line 264):**
> Tools can store binary/large text via the `ToolContext.artifacts` API:

**Replace with:**
> Tools store binary/large text via `ctx.artifacts` — a `ScopedArtifacts` facade that automatically injects tenant/user/session/trace scope on writes and enforces it on reads. This is the **porcelain** API for tool and agent developers; penguiflow internals use `ctx._artifacts` (the raw `ArtifactStore`) instead.

### 2.2 Fix summary table row (line ~829)

**Current:**
> | **Binary Storage** | `ctx.artifacts.put_bytes()` | Store binary out-of-band |

This row describes the internal pipeline layer but uses the porcelain name. Update to:
> | **Binary Storage** | `ctx._artifacts.put_bytes()` | Store binary out-of-band (internal) |

---

## File 3: `docs/tools/artifacts-and-resources.md`

### 3.1 Fix internal pipeline references (lines ~70–71)

**Current:**
> - clamp large inline strings into `ctx.artifacts.put_text(...)`
> - extract binary content into `ctx.artifacts.put_bytes(...)`

These describe what ToolNode does **internally**. After issue-74, the internal code uses `ctx._artifacts`. Update to:
> - clamp large inline strings into `ctx._artifacts.put_text(...)` (internal)
> - extract binary content into `ctx._artifacts.put_bytes(...)` (internal)

### 3.2 Add a note clarifying the distinction

After the full bullet list (after line ~72, the `resource_link` bullet), add a blank line and then the following blockquote (with a blank line after it too):
> **Note:** `ctx._artifacts` is the raw `ArtifactStore` used by penguiflow internals. Tool developers should use `ctx.artifacts` (the `ScopedArtifacts` facade) which provides `upload()`/`download()`/`list()` with automatic scope injection.

---

## File 4: `docs/planner/tool-design.md`

### 4.1 Clarify `ToolContext` listing (line ~99)

**Current:**
> - `artifacts`: artifact store for large/binary payloads

**Replace with:**
> - `artifacts`: scoped artifact facade (`ScopedArtifacts`) — use `upload()`/`download()`/`list()` for large/binary payloads with automatic scope injection

---

## File 5: `REACT_PLANNER_INTEGRATION_GUIDE.md`

### 5.1a Fix import (line ~468)

**Current:**
```python
from penguiflow.artifacts import ArtifactStore
```

**Replace with:**
```python
from penguiflow.artifacts import ScopedArtifacts
```

### 5.1b Fix `artifacts` property (lines ~484–486)

**Current:**
```python
    @property
    def artifacts(self) -> ArtifactStore:
        """Binary/large-text artifact storage (out-of-band)."""
```

**Replace with:**
```python
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers (porcelain API).

        Use upload()/download()/list() — scope is injected automatically.
        Internal framework code uses ctx._artifacts (raw ArtifactStore) instead.
        """
```

### 5.2 Update architecture diagram (lines ~1298–1302)

The diagram shows the internal flow but uses an incorrect method name `get_bytes(id, scope)` — the actual protocol method is `get(artifact_id: str) -> bytes | None`. Two changes:

**5.2a** Fix the diagram at line ~1301. Replace:
```
│  │  get_bytes(id, scope) → bytes                           │    │
```
With:
```
│  │  get(artifact_id) → bytes | None                        │    │
```

**5.2b** Add a brief note **between** the `### Architecture` heading (line ~1287) and the diagram's opening ` ``` ` (line ~1289). Insert after the blank line following the heading, before the code fence:

> **Note:** This diagram shows the internal plumbing. Tool developers interact with `ctx.artifacts` (a `ScopedArtifacts` facade providing `upload()`/`download()`/`list()`). The facade delegates to the raw `ArtifactStore` internally, injecting scope automatically.

### 5.3 Update "MCP Tool Integration" section (lines ~1378–1396)

**Current (lines 1380–1384):**
> MCP tools automatically use the artifact store when configured. The planner wraps the store with an event-emitting proxy that:
> 1. Injects `session_id` from `tool_context` into artifact scope
> 2. Emits `artifact_stored` events for real-time UI updates
> 3. Stores artifacts with proper session isolation

Update to clarify the two layers:
> MCP tools access artifacts through `ctx.artifacts` — a `ScopedArtifacts` facade that automatically injects `tenant_id`/`user_id`/`session_id`/`trace_id` from `tool_context`. Internally, the facade delegates to an `_EventEmittingArtifactStoreProxy` (plumbing) that:
> 1. Emits `artifact_stored` events for real-time UI updates
> 2. Delegates to the underlying `ArtifactStore` for persistence

**Current code example (lines 1387–1395)** — replace the **entire content** between the ` ```python ` and ` ``` ` fences (lines 1386–1396). The fences themselves stay. Current content inside the fences:
```python
# Example: Tableau MCP tool storing a PDF
# (handled automatically by ToolNode)
ref = await artifact_store.put_bytes(
    pdf_data,
    mime_type="application/pdf",
    filename="dashboard_export.pdf",
    scope=ArtifactScope(session_id=session_id),
)
# ref.id is now accessible for download
```

Replace the content inside the fences with this porcelain example:
```python
# Tool developer stores a PDF via the porcelain API
ref = await ctx.artifacts.upload(
    pdf_data,
    mime_type="application/pdf",
    filename="dashboard_export.pdf",
)
# Scope (tenant/user/session/trace) is injected automatically
```

### 5.4 Update "Best Practices" item 4 (line ~1445)

**Current:**
> 4. **Session isolation**: Always pass `session_id` in `tool_context` for proper scoping

**Replace with:**
> 4. **Session isolation**: Always pass `session_id`, `tenant_id`, and `user_id` in `tool_context` — the `ScopedArtifacts` facade uses these to enforce scope automatically

---

## File 6: `docs/tools/configuration.md`

### 6.1 Add porcelain/plumbing note to artifact extraction section (lines ~91–98)

**Current:**
> ### Artifact extraction and resources
> ToolNode can extract large/binary content into artifacts and handle MCP resources:
> - `ExternalToolConfig.artifact_extraction` controls the extraction pipeline
> - MCP resources can generate tools like `{namespace}.resources_read` (see **[MCP resources](mcp-resources.md)**)
>
> See **[Artifacts & resources](artifacts-and-resources.md)**.

This describes the internal ToolNode extraction pipeline (plumbing). Add a note **before** the cross-reference link (`See **[Artifacts & resources]...**` at line ~98), after the bullet points. Insert a blank line before and after the blockquote:

> **Note:** The extraction pipeline is an internal (plumbing) mechanism — it uses `ctx._artifacts` (the raw `ArtifactStore`) directly. Tool developers storing artifacts manually should use `ctx.artifacts` (the `ScopedArtifacts` facade) with `upload()`/`download()`/`list()`.

---

## File 7: `TEMPLATING_QUICKGUIDE.md`

### 7.1 Add note to "Artifact Store Configuration" section (after line ~2089)

**Current (lines 2085–2089):**
> **When artifact store is enabled:**
> - `InMemoryArtifactStore` is created with retention config
> - Artifacts stored by MCP tools (e.g., Tableau PDFs) are accessible
> - Playground UI shows artifacts in the sidebar
> - REST endpoint `/artifacts/{id}` serves binary content

After this block, add a blank line and then the following blockquote (with a blank line after it too):

> **Developer API:** Tool developers access artifacts via `ctx.artifacts` — a `ScopedArtifacts` facade that provides `upload()`/`download()`/`list()` with automatic scope injection. The raw `ArtifactStore` (`ctx._artifacts`) is used internally by the ToolNode extraction pipeline and playground.

---

## File 8: `docs/tools/statestore.md` (published in mkdocs)

### 8.1 Update artifacts mention (line ~68)

**Current:**
> - expose `artifact_store` or implement `ArtifactStore` so the planner can discover it (`discover_artifact_store`)

**Replace with:**
> - expose `artifact_store` or implement `ArtifactStore` (including the `list` method) so the planner can discover it (`discover_artifact_store`). Tool developers access artifacts via `ctx.artifacts` (a `ScopedArtifacts` facade); the raw `ArtifactStore` is plumbing.

---

## Verification

1. Read through each updated file and confirm the porcelain (`ctx.artifacts` / `ScopedArtifacts`) vs. plumbing (`ctx._artifacts` / `ArtifactStore`) distinction is clear
2. Confirm the `ArtifactStore` protocol block in the spec includes all 7 methods
3. Confirm no doc incorrectly uses `ctx.artifacts.put_bytes()` or `ctx.artifacts.put_text()` for tool-developer examples (should be `ctx.artifacts.upload()`)
4. Confirm internal pipeline references use `ctx._artifacts`
5. Confirm `REACT_PLANNER_INTEGRATION_GUIDE.md` ToolContext protocol listing shows `ScopedArtifacts`, not `ArtifactStore`
6. Ensure the docs extra is installed (`uv pip install -e ".[dev,docs]"`), then run `uv run mkdocs build --strict` and verify no broken links or build errors in the published pages (Files 3, 4, 6, 8)
