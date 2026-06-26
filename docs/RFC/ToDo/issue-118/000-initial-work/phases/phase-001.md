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
