# Phase 001: Proxy `emit` / `register` orthogonal flags

## Objective
Split the two conflated concerns in the artifact-store proxy — emitting the `artifact_stored` event and registering
a binary index record — into independent, individually gateable flags. This lets the rich-output store write
(Phase 002) persist a UI component WITHOUT creating a phantom `kind="binary"` index record and WITHOUT firing the
event for silent `build_*` components. Defaults are `True` so every existing binary caller (MCP/tool binaries,
resources, web fetch) is byte-for-byte unchanged.

## Tasks
1. Add `emit: bool = True` and `register: bool = True` keyword params to both `put_text` and `put_bytes` on
   `_EventEmittingArtifactStoreProxy`.
2. Rewrite `_emit_artifact_stored_event` (or inline two gated blocks) so `register` gates the
   `register_binary_artifact` + `write_snapshot` bookkeeping and `emit` gates the `artifact_stored` event —
   independently, with NO `namespace` comparison anywhere.
3. Thread the two flags from `put_text`/`put_bytes` into the gating logic.

## Detailed Steps

### Step 1: Add flags to `put_bytes` (`artifact_handling.py:63-84`)
- Add `emit: bool = True` and `register: bool = True` to the keyword-only params.
- Pass them into the gating call (replacing the current unconditional `self._emit_artifact_stored_event(...)`).

### Step 2: Add flags to `put_text` (`artifact_handling.py:86-107`)
- Same additions as `put_bytes`.

### Step 3: Make registration and emission independent (`artifact_handling.py:109-138`)
- Replace `_emit_artifact_stored_event` with a version that takes `emit` / `register` and applies each concern under
  its own guard. The `register` block keeps today's `register_binary_artifact(...)` + `write_snapshot(...)`; the
  `emit` block keeps today's `PlannerEvent("artifact_stored", ...)`.
- Do NOT add or rely on any `namespace == "penguiflow_ui_component"` comparison here — gating is purely by the two
  boolean flags (the namespace still flows into the event `extra["source"]` exactly as today).

## Required Code

```python
# Target file: penguiflow/planner/artifact_handling.py  (put_bytes signature additions)
    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
        emit: bool = True,
        register: bool = True,
    ) -> ArtifactRef:
        """Store binary data and (optionally) register + emit artifact_stored event."""
        resolved_scope = self._resolve_scope(scope)
        ref = await self._store.put_bytes(
            data,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=resolved_scope,
            meta=meta,
        )
        self._emit_artifact_stored_event(ref, len(data), namespace, emit=emit, register=register)
        return ref
```

```python
# Target file: penguiflow/planner/artifact_handling.py  (put_text signature additions)
    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
        emit: bool = True,
        register: bool = True,
    ) -> ArtifactRef:
        """Store large text and (optionally) register + emit artifact_stored event."""
        resolved_scope = self._resolve_scope(scope)
        ref = await self._store.put_text(
            text,
            mime_type=mime_type,
            filename=filename,
            namespace=namespace,
            scope=resolved_scope,
            meta=meta,
        )
        self._emit_artifact_stored_event(ref, len(text.encode("utf-8")), namespace, emit=emit, register=register)
        return ref
```

```python
# Target file: penguiflow/planner/artifact_handling.py  (independent register / emit)
    def _emit_artifact_stored_event(
        self,
        ref: ArtifactRef,
        size_bytes: int,
        namespace: str | None,
        *,
        emit: bool = True,
        register: bool = True,
    ) -> None:
        """Register a binary index record (if register) and/or emit artifact_stored (if emit).

        The two concerns are independent: rich-output UI writes pass register=False (they own
        their index entry via register_tool_artifact) and emit=emit_visible.
        """
        if register and self._registry is not None:
            source_tool = namespace or self._namespace
            self._registry.register_binary_artifact(
                ref,
                source_tool=source_tool,
                step_index=len(self._trajectory.steps),
            )
            if isinstance(self._trajectory.metadata, MutableMapping):
                self._registry.write_snapshot(self._trajectory.metadata)
        if emit:
            self._emit_event(
                PlannerEvent(
                    event_type="artifact_stored",
                    ts=self._time_source(),
                    trajectory_step=len(self._trajectory.steps),
                    extra={
                        "artifact_id": ref.id,
                        "mime_type": ref.mime_type,
                        "size_bytes": size_bytes,
                        "artifact_filename": ref.filename,
                        "source": {"namespace": namespace or self._namespace},
                    },
                )
            )
```

## Exit Criteria (Success)
- [ ] `put_text`/`put_bytes` accept `emit` and `register`, both defaulting to `True`.
- [ ] With defaults (`emit=True, register=True`), behavior is identical to today: a binary record is registered
      (with `write_snapshot`) AND an `artifact_stored` event fires.
- [ ] `register=False` writes NO binary index record (and no `write_snapshot` from the proxy) but the event still
      fires when `emit=True`.
- [ ] `emit=False` emits NO event but the binary record is still created when `register=True`.
- [ ] No `namespace` string comparison gates either concern; the namespace still appears in the event's
      `extra["source"]["namespace"]`.
- [ ] The public `ArtifactStore` protocol (`artifacts.py`) is NOT widened — the new kwargs live only on the proxy.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path; existing binary
      callers (MCP/web-fetch) are unaffected.

## Implementation Notes
- This phase must land BEFORE Phase 002, which calls `put_text(..., emit=emit_visible, register=False)`.
- Keeping the defaults `True` is load-bearing for non-breaking: every current call site (`ctx._artifacts.put_*`
  with no new kwargs) behaves exactly as today.
- Do NOT change `register_binary_artifact` — it stays correct for real binaries.
- The typing concern for the *caller* (mypy seeing the public protocol without these kwargs) is handled in
  Phase 002 via a cast at the call site; this phase does not widen the public protocol.

## Verification Commands
```bash
uv run pytest tests/ -k "rich_output or artifact" -q

# Targeted proxy independence smoke test (illustrative — actual test added in tests/)
uv run python -c "
import inspect
from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
sig_text = inspect.signature(_EventEmittingArtifactStoreProxy.put_text)
sig_bytes = inspect.signature(_EventEmittingArtifactStoreProxy.put_bytes)
for sig in (sig_text, sig_bytes):
    assert sig.parameters['emit'].default is True
    assert sig.parameters['register'].default is True
print('OK')
"

uv run ruff check . && uv run mypy
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes

All changes are confined to two files (one source, one test), exactly as the phase scopes:

- `penguiflow/planner/artifact_handling.py` — `_EventEmittingArtifactStoreProxy`:
  - `put_bytes` (was `:63-84`, now `:63-86`): added keyword-only `emit: bool = True` and
    `register: bool = True`; updated the docstring to "...and (optionally) register + emit..."; threaded both
    flags into the gating call: `self._emit_artifact_stored_event(ref, len(data), namespace, emit=emit, register=register)`.
  - `put_text` (was `:86-107`, now `:88-111`): identical additions; size argument stays
    `len(text.encode("utf-8"))`.
  - `_emit_artifact_stored_event` (was `:109-138`, now `:113-149`): added keyword-only `emit`/`register` params
    (both default `True`); the `register_binary_artifact` + `write_snapshot` bookkeeping is now guarded by
    `if register and self._registry is not None:` and the `artifact_stored` `PlannerEvent` is guarded by
    `if emit:`. The two concerns are fully independent. NO `namespace` comparison gates either concern; the
    namespace still flows into `extra["source"]["namespace"]` exactly as before. The pre-existing
    `# Use artifact_filename to avoid LogRecord conflict` comment was preserved.
- `tests/test_artifact_handling.py` — added 5 focused independence tests (and the supporting `_make_real_proxy`
  helper + an `InMemoryArtifactStore` import) covering every Exit Criterion behaviorally:
  - `test_put_bytes_defaults_register_and_emit` / `test_put_text_defaults_register_and_emit` — defaults
    register a binary record AND emit the event (today's behavior, byte-for-byte for binary callers).
  - `test_register_false_skips_index_but_still_emits` — `register=False` writes NO binary index record
    (`_binary_index == {}`, `_records == []`) but the event still fires (`emit=True`), with the namespace
    preserved in `extra["source"]["namespace"]`.
  - `test_emit_false_skips_event_but_still_registers` — `emit=False` emits NO event but the binary record is
    still created.
  - `test_register_false_emit_false_only_stores` — both `False` → pure store write, no index, no event.

### Key Considerations

- **Followed the phase's "Required Code" almost verbatim.** The only intentional deviation from the literal
  snippet is keeping the existing inline comment `# Use artifact_filename to avoid LogRecord conflict` on the
  `artifact_filename` line. The phase's snippet omitted it, but it documents a real LogRecord-collision guard and
  dropping it would lose institutional knowledge for no benefit. The functional code matches the snippet exactly.
- **Defaults are load-bearing for non-breaking.** Both flags default to `True`, so every existing caller
  (`ctx._artifacts.put_text/put_bytes` with no new kwargs — MCP/tool binaries, resources, web fetch) is
  unchanged. Verified by re-running the full `rich_output or artifact` suite (306 pre-existing tests still pass)
  plus the binary-caller test files.
- **Public protocol untouched (Exit Criterion 6).** `penguiflow/artifacts.py`'s `ArtifactStore.put_text`/
  `put_bytes` were NOT widened — confirmed there are no `emit`/`register` params anywhere in `artifacts.py`. The
  new kwargs live ONLY on the proxy. The caller-side mypy concern (call site typed as the public protocol) is
  explicitly Phase 002's responsibility (cast at the call site), per the phase notes; this phase does not touch
  any caller, so mypy is clean today.
- **Added behavioral tests proactively.** The phase's verification block calls the proxy-independence test
  "illustrative — actual test added in tests/", and the broader plan lists proxy `emit`/`register` independence
  as a required test. I added them in the existing `tests/test_artifact_handling.py` (the natural home), using a
  real `InMemoryArtifactStore` + real `ArtifactRegistry` + real `Trajectory` so the `register_binary_artifact`/
  `write_snapshot` path actually executes when `register=True`, rather than mocking it away. This gives genuine
  coverage of the independence guarantee rather than just an `inspect.signature` check.

### Assumptions

- **Assumed `Trajectory` requires a `query` argument.** The existing helper in this test file uses
  `MagicMock(spec=Trajectory)`, but my new tests needed a real `Trajectory` so `write_snapshot(self._trajectory.metadata)`
  runs against a real `MutableMapping`. The real constructor is `Trajectory(query=..., tool_context=...)`
  (`metadata` defaults to an empty dict, which is a `MutableMapping`), so the `register=True` path exercises the
  `write_snapshot` branch end-to-end.
- **Assumed `ArtifactRegistry._binary_index` / `_records` are stable enough to assert against in tests.** These
  are private attributes, but the registry exposes no public "list binary records" accessor and the existing
  test file already uses the proxy's private members (`_resolve_scope`), so asserting on `_binary_index`/`_records`
  is consistent with the file's established style and is the most direct way to prove "no phantom binary record".
- **Assumed `InMemoryArtifactStore` is the right backing store for the tests** (over `NoOpArtifactStore`, which
  the pre-existing tests use). NoOp returns a stub ref and never persists; InMemory returns a realistic
  content-hashed `ArtifactRef` with `.id`/`.mime_type`/`.size_bytes`, which makes the event/registration
  assertions meaningful.

### Deviations from Plan

- **Preserved the `artifact_filename` LogRecord-conflict comment** that the phase's "Required Code" snippet
  omitted (see Key Considerations). Purely additive; no behavior change.
- **Added 5 tests** beyond the phase's literal task list. The phase's three numbered tasks are strictly the
  source-code changes; the verification section flags the independence test as "actual test added in tests/", so
  this is fulfilling that intent rather than diverging from it. No source behavior was changed for the tests.

### Potential Risks & Reviewer Attention Points

- **None of the per-concern behavior changes when both flags are `True`** — this is the crux of the non-breaking
  guarantee. The reviewer should confirm that the default path (`emit=True, register=True`) produces the exact
  same `register_binary_artifact` + `write_snapshot` + `PlannerEvent` sequence as before. It does: the bodies of
  both guarded blocks are byte-identical to the pre-change code; only the guards were added.
- **The new tests assert against private registry internals** (`_binary_index`, `_records`). If a future refactor
  renames these, the tests will need updating. This is a known, accepted tradeoff matching the file's existing
  style.
- **This phase is a prerequisite for Phase 002**, which will call `put_text(..., emit=emit_visible, register=False)`
  from `nodes.py` through a cast to the proxy type. The mypy gate is clean now only because no caller yet passes
  the new kwargs through the public protocol; Phase 002 owns the cast. The reviewer should NOT expect a cast or
  any `nodes.py`/`artifacts.py` change in this phase.

### Files Modified

- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/artifact_handling.py` (modified)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_artifact_handling.py` (modified — added tests)

### Verification Results

- `uv run pytest tests/ -k "rich_output or artifact" -q` → **311 passed**, 2542 deselected (306 pre-existing + 5 new).
- Proxy independence smoke test (the `inspect.signature` check) → **OK** (both `emit` and `register` default `True`
  on `put_text` and `put_bytes`).
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 228 source files.**
- Spot regression of binary callers (`test_artifacts.py`, `test_artifact_registry.py`,
  `test_playground_artifact_discovery.py`) → all passing.
