# Plan: Ensure custom state store is used everywhere in the Playground

## Context

When a custom state store is passed to `create_playground_app(state_store=my_store)`, we need to ensure it's consistently used across all playground subsystems and never silently replaced by an `InMemoryStateStore()` default.

After a thorough audit of the code, the main flow is correct — the custom store flows through `load_agent`, `SessionManager`, `_build_planner_factory`, and all endpoint closures. However, there is **one concrete issue** where the custom state store's artifact store can be bypassed.

## Issue: `_discover_artifact_store()` ignores the state store

**File:** `penguiflow/cli/playground.py:1020-1040`

The `_discover_artifact_store()` function only looks at the planner for an artifact store:

```python
def _discover_artifact_store() -> Any | None:
    planner = _discover_planner()
    if planner is None:
        return None          # <-- gives up here
    store = getattr(planner, "artifact_store", None)
    ...
```

This means the artifact store from the custom state store is **ignored** when:
- The agent is orchestrator-based and the orchestrator has no discoverable `_planner` attribute
- The planner's builder function doesn't accept `state_store` in its signature (so the planner never received the custom store, and its artifact store is `NoOpArtifactStore`)

All artifact-related endpoints (`/artifacts`, `/artifacts/{id}`, `/artifacts/{id}/meta`, `/resources/read`) call `_discover_artifact_store()` and will return 501 "Artifact storage not enabled" even when the custom state store has a perfectly valid artifact store.

## Fix

Modify `_discover_artifact_store()` to fall back to `store.artifact_store` (the outer closure variable that holds the custom state store) when the planner-based discovery fails.

```python
def _discover_artifact_store() -> Any | None:
    from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

    planner = _discover_planner()
    if planner is not None:
        found = getattr(planner, "artifact_store", None)
        if found is None:
            found = getattr(planner, "_artifact_store", None)
        if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
            return found

    # Fallback: check the playground state store directly
    if store is not None:
        found = getattr(store, "artifact_store", None)
        if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
            return found

    return None
```

## Files to modify

- `penguiflow/cli/playground.py` — `_discover_artifact_store()` function (~line 1020)
- `tests/cli/test_playground_backend.py` — Add unit tests (see below)

## Unit tests

Add tests to `tests/cli/test_playground_backend.py` covering the `_discover_artifact_store()` fallback behavior. Follow existing patterns in the file (MagicMock wrappers, `create_playground_app`, `AsyncClient` with `ASGITransport`).

**Test cases:**

1. **`test_discover_artifact_store_falls_back_to_state_store`** — When the agent wrapper has no discoverable planner (orchestrator-based agent), but the custom state store has a valid `artifact_store`, the artifact endpoints should return 200 (not 501). Create an `InMemoryStateStore`, store an artifact in it, pass it to `create_playground_app`, mock the agent wrapper without a `_planner` attribute, and hit `GET /artifacts/{id}`.

2. **`test_discover_artifact_store_prefers_planner`** — When both the planner and the state store have artifact stores, the planner's store should be used (existing behavior preserved). Set up both, store an artifact only in the planner's store, and verify it's found.

3. **`test_discover_artifact_store_skips_noop`** — When the state store's artifact store is a `NoOpArtifactStore`, the fallback should return `None` (no false positives). Verify the endpoint returns 501.

**Reuse existing patterns:**
- `InMemoryStateStore` from `penguiflow.state.in_memory` (has `.artifact_store` property)
- `InMemoryArtifactStore` / `NoOpArtifactStore` from `penguiflow.artifacts`
- `MagicMock` / `AsyncMock` for agent wrappers (see `test_playground_phase3.py:352-358`)

## Audit summary (no changes needed)

These paths were verified as correct:

| Path | How custom store flows | Status |
|------|----------------------|--------|
| `create_playground_app` → `store` variable | `store = state_store or InMemoryStateStore()` — uses provided store when non-None | OK |
| `store` → `SessionManager` | Lines 843-848: store passed as `session_store` if it has `save_task` or `save_event`+`load_history` | OK |
| `SessionManager` → `StreamingSession` | Line 2025: `state_store=self._state_store` passed to each session | OK |
| `store` → `load_agent` | Line 852: `state_store=store` | OK |
| `load_agent` → `_call_builder` / `_instantiate_orchestrator` | Signature introspection passes `state_store` when accepted | OK (by design) |
| `store` → `_build_planner_factory` | Line 853: captured in closure | OK |
| `store` → `app.state.state_store` | Line 2178 | OK |
| `/events` + `/trajectory` endpoints | Use `store` closure variable directly | OK |
| `tool_jobs.py` KV store resolution | Uses `session._state_store` which came from `SessionManager` | OK |

## Verification

1. Run existing tests: `uv run pytest tests/ -k "playground"`
2. Run full test suite: `uv run pytest`
3. Run linter: `uv run ruff check penguiflow/cli/playground.py`
4. Run type checker: `uv run mypy penguiflow/cli/playground.py`
