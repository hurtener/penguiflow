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

**Important:** The existing code uses a local variable named `store` (lines 1031-1040) for the planner's artifact store. Rename this local variable to `found` to avoid shadowing the outer closure variable `store` (the state store, defined at line 825).

**Note on `isinstance` checks:** `ArtifactStore` is a `@runtime_checkable` Protocol (see `penguiflow/artifacts.py:122`). Classes like `PlaygroundArtifactStore` and `InMemoryArtifactStore` do NOT inherit from `ArtifactStore` — they satisfy the protocol structurally. The `isinstance(x, ArtifactStore)` check works at runtime because of the `@runtime_checkable` decorator. Similarly, `NoOpArtifactStore` also structurally satisfies `ArtifactStore`, which is why the explicit `isinstance(x, NoOpArtifactStore)` exclusion check must come first.

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

## Issue: `_ScopedArtifactStore` and `_DisabledArtifactStore` missing `list` method

**File:** `penguiflow/cli/playground.py:1058-1134`

Both `_ScopedArtifactStore` (line 1058) and `_DisabledArtifactStore` (line 1115) are incomplete duck-types of the `ArtifactStore` protocol (`penguiflow/artifacts.py:122`). The protocol requires 7 methods: `put_bytes`, `put_text`, `get`, `get_ref`, `delete`, `exists`, and `list`. Both classes implement the first 6 but are **missing `list`**.

**Impact:**
- Any tool or resource handler that calls `.list()` on the scoped/disabled store (via `MinimalCtx._artifacts` or `MinimalToolCtx._artifacts`) will raise `AttributeError`.
- Because `ArtifactStore` is `@runtime_checkable`, these classes will also fail `isinstance(x, ArtifactStore)` checks — meaning they don't structurally satisfy the protocol.

**Usage sites:** Both classes are instantiated in the `/resources/read` endpoint (line 2141-2155) and the `/apps/{namespace}/call-tool` endpoint (line 2231-2245), then passed as the artifact store into `MinimalCtx` / `MinimalToolCtx`.

### Fix

Add the missing `list` method to both classes:

**`_ScopedArtifactStore`** — delegate to the inner store, injecting the default scope if none provided. Use `ArtifactScope` and `ArtifactRef` types to match the protocol signature exactly:

```python
async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
    return await self._store.list(scope=scope or self._scope)
```

**Note:** `ArtifactScope` and `ArtifactRef` are already imported in nearby code (e.g., the `/artifacts` endpoint at line 1935 does `from penguiflow.artifacts import ArtifactScope`). Add the import at the top of each method or in the class scope. Since these classes are inside the `create_playground_app` closure, use a local import: `from penguiflow.artifacts import ArtifactRef, ArtifactScope`.

**`_DisabledArtifactStore`** — return an empty list (consistent with its disabled behavior):

```python
async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
    return []
```

## Files to modify

- `penguiflow/cli/playground.py` — `_discover_artifact_store()` function (~line 1020), `_ScopedArtifactStore` (add `list` after `exists`, ~line 1113), `_DisabledArtifactStore` (add `list` after `exists`, ~line 1134)
- `tests/cli/test_playground_artifact_discovery.py` — New file for artifact discovery unit tests (see below)

## Unit tests

Add tests to a **new file** `tests/cli/test_playground_artifact_discovery.py` covering the `_discover_artifact_store()` fallback behavior. Use `AsyncClient` with `ASGITransport` to match the existing artifact test patterns in `tests/test_playground_phase3.py:382-664`. This naturally supports async setup (e.g., `await store.artifact_store.put_bytes(...)`) without `asyncio.run()` workarounds.

**Important:** `_discover_artifact_store()` is a closure inside `create_playground_app` and cannot be called directly. All tests must exercise it **indirectly** through the artifact HTTP endpoints (e.g., `GET /artifacts/{id}`) using `AsyncClient`. A 501 response means discovery returned `None`; a 200 (or 404 for missing artifact) means discovery found a valid store.

**Critical — MagicMock auto-creates attributes.** `MagicMock()` auto-creates any attribute on access, which means `getattr(mock, "_planner")` returns a new `MagicMock` instead of `None`. Worse, a MagicMock instance passes `isinstance(x, ArtifactStore)` because `@runtime_checkable` Protocol only checks attribute existence and MagicMock satisfies that. This means `_discover_planner()` and `_discover_artifact_store()` will find fake planner/artifact stores from auto-created attributes, silently bypassing the state store fallback path. **For any test that needs `_discover_planner()` to return `None`, you MUST explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`.** See `tests/test_playground_phase3.py:646-649` for the existing pattern and comment explaining this.

**Important: artifact scoping in tests.** `InMemoryStateStore.artifact_store` returns a `PlaygroundArtifactStore` (a session-scoped facade wrapping `InMemoryArtifactStore`). When calling `put_bytes`, you **must** pass `scope=ArtifactScope(session_id="test-session")` so the artifact is discoverable. The `GET` request must use the **same** `session_id` (e.g., `?session_id=test-session`). Without matching scopes, the `list` and `get` calls will return empty/None even though the artifact was stored.

**Test cases:**

1. **`test_discover_artifact_store_falls_back_to_state_store`** — When the agent wrapper has no discoverable planner (orchestrator-based agent), but the custom state store has a valid `artifact_store`, the artifact endpoints should return 200 (not 501). Create an `InMemoryStateStore`, store an artifact in it via `await state_store.artifact_store.put_bytes(data, scope=ArtifactScope(session_id="test-session"))`, use the returned `ArtifactRef.id` in the `GET /artifacts/{id}?session_id=test-session` request. Pass the store to `create_playground_app`. Mock the agent wrapper with `MagicMock()` and **explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`** to prevent MagicMock auto-created attributes from being picked up by `_discover_planner()`.

2. **`test_discover_artifact_store_prefers_planner`** — When both the planner and the state store have artifact stores, the planner's store should be used (existing behavior preserved). Create an `InMemoryArtifactStore()` for the planner and store an artifact in it with `scope=ArtifactScope(session_id="test-session")`. Set `mock_wrapper._planner = MagicMock(artifact_store=planner_artifact_store)`. Also pass a separate `InMemoryStateStore` (which has its own artifact store). Verify the planner's artifact is found via `GET /artifacts/{id}?session_id=test-session` (returns 200).

3. **`test_discover_artifact_store_skips_noop`** — When the state store's artifact store is a `NoOpArtifactStore`, the fallback should return `None` (no false positives). Since `InMemoryStateStore` always creates a `PlaygroundArtifactStore` (not NoOp), construct a mock state store: `mock_store = MagicMock(); mock_store.artifact_store = NoOpArtifactStore()`. Also **explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`** on the agent mock so the planner path returns None and the fallback is actually exercised. Verify the `GET /artifacts/{id}` endpoint returns 501.

4. **`test_discover_artifact_store_list_endpoint_uses_fallback`** — When the agent wrapper has no discoverable planner but the custom state store has a valid artifact store with stored artifacts, the `GET /artifacts?session_id=test-session` endpoint should return a non-empty list (not `[]`). **Explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`** on the agent mock. This tests the `/artifacts` list endpoint specifically, which returns `[]` (not 501) when discovery fails — a subtler failure mode that could silently hide a regression.

**Reuse existing patterns:**
- `InMemoryStateStore` from `penguiflow.cli.playground_state` (re-exports `penguiflow.state.in_memory.InMemoryStateStore`; has `.artifact_store` property). Use this import path to match existing playground tests.
- `InMemoryArtifactStore` / `NoOpArtifactStore` from `penguiflow.artifacts`
- `MagicMock` / `AsyncMock` for agent wrappers (see `tests/test_playground_phase3.py:352-358`)

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
