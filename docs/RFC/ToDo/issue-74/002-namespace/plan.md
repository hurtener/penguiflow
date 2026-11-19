# RFC 002: Add `namespace` field to `ArtifactRef`

## Context

The `ArtifactStore` protocol accepts a `namespace: str | None` parameter on `put_bytes()` and `put_text()`, but the returned `ArtifactRef` model has no `namespace` field. Once an artifact is stored, the namespace is lost at the protocol level. It can only be recovered by parsing the artifact ID (implementation-specific convention, not protocol-guaranteed) or by inspecting side-channel metadata (e.g., PlannerEvent extras, ArtifactRegistry records).

This change promotes `namespace` to a first-class field on `ArtifactRef` so that consumers can identify the origin/grouping of any artifact without relying on implementation details.

## Change summary

Add `namespace: str | None = None` to `ArtifactRef`. Fully backward-compatible — existing code that omits it continues to work, and existing serialized JSON without the field deserializes with `None`.

Phases: `phase-000` (Layers 1–2), `phase-001` (Python tests, Layer 4), `phase-003` (docs, Layer 5). There is no `phase-002`: it covered a UI client that is no longer part of the library.

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

## Layer 3 — SSE event emission (no changes)

**File:** `penguiflow/planner/artifact_handling.py` (~line 121–134)

No changes needed. The `_emit_artifact_stored_event` method already sends `"source": {"namespace": ...}` inside the `extra` dict. A UI client can read the namespace from `source.namespace`.

## Layer 4 — Tests

| File | What to update |
|---|---|
| `tests/test_artifacts.py` | `test_minimal_ref` — assert `ref.namespace is None` |
| `tests/test_artifacts.py` | `test_full_ref` — pass and assert `namespace` |
| `tests/test_artifacts.py` | `test_ref_serialization` — verify namespace round-trips |
| `tests/test_artifacts.py` | `test_namespace_in_id` — also assert `ref.namespace == "tableau"` |
| `tests/test_artifacts.py` | `test_put_bytes_with_namespace_and_scope` — assert returned ref has namespace |
| `tests/test_artifacts.py` | `test_put_text_with_namespace_and_scope` — assert returned ref has namespace |
| `tests/test_artifacts.py` | `test_upload_passes_namespace` — assert `ref.namespace == "my_tool"` |

## Layer 5 — Documentation

| File | Change |
|---|---|
| `docs/tools/artifacts-guide.md` | Update **both** the main `ArtifactRef` example (~line 73) **and** the S3 custom store `ArtifactRef` construction (~line 758) to include `namespace` |

## What does NOT need to change

- **Protocol method signatures** — `put_bytes`/`put_text` already accept `namespace`; `get_ref`/`list` return `ArtifactRef` so namespace flows through automatically.
- **`ScopedArtifacts`** — passes namespace through to underlying store, returns whatever `ArtifactRef` the store returns.
- **`_EventEmittingArtifactStoreProxy`** — delegates to underlying store, returns its ref.
- **`ArtifactRegistry`** — reads fields from ref; won't break, could optionally start using `ref.namespace`.
- **`ToolNode` artifact extraction** (`tools/node.py`) — calls `ref.model_dump()`, so namespace is included automatically.
- **`payload_builders.py`** / **`sessions/tool_jobs.py`** — callers of `put_text`/`put_bytes`; they already pass namespace, the returned ref will now carry it.
- **`state/in_memory.py` (`SessionArtifactStore`)** — delegates to `InMemoryArtifactStore`, returns its refs.

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
