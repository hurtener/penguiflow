# Unify UI-component artifacts into the ArtifactStore

## Summary

PenguiFlow currently has **two distinct things both called "artifact"**, with two different
ref types and two different storage paths. This split is confusing and means UI-component
payloads never get the persistence, scoping, and size-offload that the real artifact backend
provides. We should make **every `ui_component` payload persist to the `ArtifactStore`** and be
referenced through it, while keeping the in-run registry as a thin index/cache.

## Background

The two "artifact" concepts today:

1. **Planner `ArtifactRegistry`** (`penguiflow/planner/artifact_registry.py`) — an in-run,
   per-execution index. `build_*`/`render_*` rich-output tools register `ui_component` records
   here, and the actual component payload lives **inline** in `self._payloads`. The short
   `artifact_N` refs the LLM uses come from here.
2. **`ArtifactStore`** (`penguiflow/artifacts.py`) — a persistent, scoped content backend
   (`put_bytes`/`put_text`, returns a compact `ArtifactRef`). General-purpose: it stores
   text/JSON, not just binary.

Having two "artifact" concepts and two ref types is a maintenance and conceptual burden. UI
payloads also miss out on persistence/scoping/size-offload, and resolution is unreliable across
HITL pause/resume because the inline payloads do not survive a snapshot round-trip.

## Problem

- Two overlapping concepts named "artifact", each with its own ref type.
- `ui_component` payloads are held inline in the in-run registry and are **not** persisted to the
  store, so they are not durable, not scoped, and not size-offloaded.
- After a HITL pause/resume (`snapshot()` → `from_snapshot()`), the in-run `_payloads` cache is
  empty and UI components cannot be reliably resolved.

## Desired outcome

- **One artifact home, one external ref concept.** Every `ui_component` payload persists to the
  `ArtifactStore` and is referenced through it.
- The in-run registry remains a thin **index/cache**: short refs + LLM-facing metadata, with the
  full payload as an in-run hot cache. First resolve fetches from the store, then caches locally.
- Persistence, scoping, and size-offload for UI payloads come "for free" from the store.
- Reliable resolution across HITL pause/resume — UI components rehydrate from the store after a
  snapshot round-trip.

## Notes / context

The codebase is already half-way there, which keeps the change small:

- `list_artifacts` already merges registry **and** store results ("persistent store wins" dedup).
- `resolve_ref_async` already hydrates store-backed JSON via `_maybe_hydrate_stored_payload`,
  which expects exactly the stub shape `{"artifact": {"id": ...}}` we would write.
- `_register_component_payload` is the **single shared choke point** for both `build_*` and
  `render_*`.

The visible-UI delivery path should stay unchanged: `render_*` still streams props inline via
`artifact_chunk`. The store is durability/backing, not the frontend contract.

A detailed implementation plan lives at
[`000-initial-work/plan.md`](./000-initial-work/plan.md).
