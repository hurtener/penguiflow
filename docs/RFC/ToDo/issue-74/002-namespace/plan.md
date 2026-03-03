# RFC 002: Add `namespace` field to `ArtifactRef`

## Context

The `ArtifactStore` protocol accepts a `namespace: str | None` parameter on `put_bytes()` and `put_text()`, but the returned `ArtifactRef` model has no `namespace` field. Once an artifact is stored, the namespace is lost at the protocol level. It can only be recovered by parsing the artifact ID (implementation-specific convention, not protocol-guaranteed) or by inspecting side-channel metadata (e.g., PlannerEvent extras, ArtifactRegistry records).

This change promotes `namespace` to a first-class field on `ArtifactRef` so that consumers can identify the origin/grouping of any artifact without relying on implementation details.

## Change summary

Add `namespace: str | None = None` to `ArtifactRef`. Fully backward-compatible — existing code that omits it continues to work, and existing serialized JSON without the field deserializes with `None`.

---

## Layer 1 — Python model

**File:** `penguiflow/artifacts.py` (lines 56–82)

- Add field `namespace: str | None = None` to the `ArtifactRef` Pydantic model, placed **after `scope` and before `source`** (grouping metadata together).

## Layer 2 — ArtifactStore implementations (construction sites)

All places that construct an `ArtifactRef(...)` must pass `namespace=namespace`.

| File | Location | Notes |
|---|---|---|
| `penguiflow/artifacts.py` | `NoOpArtifactStore.put_bytes()` ~line 483 | Pass `namespace=namespace` |
| `penguiflow/artifacts.py` | `NoOpArtifactStore.put_text()` ~line 530 | Pass `namespace=namespace` |
| `penguiflow/artifacts.py` | `InMemoryArtifactStore.put_bytes()` ~line 624 | Pass `namespace=namespace` |

`InMemoryArtifactStore.put_text()` delegates to `put_bytes()`, so it is covered.

## Layer 3 — Frontend TypeScript types

Changing the TS source files requires a `vite build` to regenerate the `dist/` assets, which are checked into git.

| File | Change |
|---|---|
| `penguiflow/cli/playground_ui/src/lib/types/artifacts.ts` | Add `namespace: string \| null` to `ArtifactRef` interface, placed **after `sha256` and before `source`** (mirrors Python field ordering). Do **not** add it to `ArtifactStoredEvent` — namespace is derived from `source`. |
| `penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts` | In `addArtifact()` (~line 53), derive namespace inline: `namespace: typeof event.source?.namespace === 'string' ? event.source.namespace : null`. Do **not** import `getString` from `session-stream.ts` — it is a file-private helper and not exported. |
| `penguiflow/cli/playground_ui/src/lib/services/session-stream.ts` | In `toArtifactRef()` (~line 298), derive namespace using the existing `getString()` helper (line 228): `namespace: getString(stored.source?.namespace) ?? null`. No changes needed in `toArtifactStoredEvent()` (~line 275) since namespace stays inside `source`. |

After source changes, run from `penguiflow/cli/playground_ui/`:
```bash
npm run build
```
Commit the regenerated `dist/` files.

## Layer 4 — SSE event emission (no changes)

**File:** `penguiflow/planner/artifact_handling.py` (~line 121–134)

No changes needed. The `_emit_artifact_stored_event` method already sends `"source": {"namespace": ...}` inside the `extra` dict. The frontend extracts namespace from `source.namespace` (see Layer 3).

## Layer 5 — Tests

| File | What to update |
|---|---|
| `tests/test_artifacts.py` | `test_minimal_ref` — assert `ref.namespace is None` |
| `tests/test_artifacts.py` | `test_full_ref` — pass and assert `namespace` |
| `tests/test_artifacts.py` | `test_ref_serialization` — verify namespace round-trips |
| `tests/test_artifacts.py` | `test_namespace_in_id` — also assert `ref.namespace == "tableau"` |
| `tests/test_artifacts.py` | `test_put_bytes_with_namespace_and_scope` — assert returned ref has namespace |
| `tests/test_artifacts.py` | `test_put_text_with_namespace_and_scope` — assert returned ref has namespace |
| `tests/test_artifacts.py` | `test_upload_passes_namespace` — assert `ref.namespace == "my_tool"` |
| `penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts` | (1) In `'stores correct artifact properties'` test (~line 44), add assertion `expect(artifact?.namespace).toBeNull()` since the mock event's `source` has no `namespace` key. (2) Add **one new test** in the `addArtifact` describe block that creates a mock event with `source: { tool: 'test_tool', namespace: 'my_ns' }` and asserts the resulting ref has `namespace === 'my_ns'`. |
| `penguiflow/cli/playground_ui/tests/unit/components/sidebar-right/ArtifactsCard.test.ts` | **No changes needed.** This file asserts on DOM elements, not `ArtifactRef` shape. Mock events are `ArtifactStoredEvent` which don't have a top-level `namespace` field. |

## Layer 6 — Documentation

| File | Change |
|---|---|
| `docs/tools/artifacts-guide.md` | Update **both** the main `ArtifactRef` example (~line 73) **and** the S3 custom store `ArtifactRef` construction (~line 758) to include `namespace` |

## What does NOT need to change

- **Protocol method signatures** — `put_bytes`/`put_text` already accept `namespace`; `get_ref`/`list` return `ArtifactRef` so namespace flows through automatically.
- **`ScopedArtifacts`** — passes namespace through to underlying store, returns whatever `ArtifactRef` the store returns.
- **`_EventEmittingArtifactStoreProxy`** — delegates to underlying store, returns its ref.
- **`ArtifactRegistry`** — reads fields from ref; won't break, could optionally start using `ref.namespace`.
- **`ToolNode` artifact extraction** (`tools/node.py`) — calls `ref.model_dump()`, so namespace is included automatically.
- **Playground API endpoints** (`cli/playground.py`) — `get_artifact_meta` returns `ref.model_dump()`, namespace flows through.
- **`payload_builders.py`** / **`sessions/tool_jobs.py`** — callers of `put_text`/`put_bytes`; they already pass namespace, the returned ref will now carry it.
- **`state/in_memory.py` (`PlaygroundArtifactStore`)** — delegates to `InMemoryArtifactStore`, returns its refs.

## Verification

1. **Unit tests:**
   ```bash
   uv run pytest tests/test_artifacts.py -v
   ```
2. **Full test suite + coverage:**
   ```bash
   uv run pytest --cov=penguiflow --cov-report=term --cov-fail-under=84.5
   ```
3. **Lint + type check:**
   ```bash
   uv run ruff check . && uv run mypy
   ```
4. **Frontend tests:**
   ```bash
   cd penguiflow/cli/playground_ui && npm test
   ```
5. **Frontend build (regenerate dist/):**
   ```bash
   cd penguiflow/cli/playground_ui && npm run build
   ```
