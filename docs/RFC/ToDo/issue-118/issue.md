# Persist UI-component artifacts to the ArtifactStore — opt-in, non-breaking

## Summary

PenguiFlow has **two distinct things both called "artifact"**, with two ref types and two storage
paths. UI-component payloads live **inline** in an in-run registry and never reach the persistent
`ArtifactStore`, so they miss persistence, scoping, and size-offload, and they cannot be reliably
resolved across a HITL pause/resume snapshot round-trip.

We want to let **`ui_component` payloads persist to the `ArtifactStore`** and be delivered by id, with
the in-run registry kept as a thin index/cache over the same store-backed artifacts. The key change
from the original framing: this must be **opt-in and non-breaking**. The default code path stays
byte-for-byte what it is today; every new behavior sits behind one new planner flag,
`ui_component_delivery`, defaulting to `"inline"`. Net effect is an additive **minor** version bump
(3.10.x → 3.11.0) with no migration forced on anyone.

> **Scope note:** this is bigger than the first cut of this issue assumed. It is no longer a small,
> "the codebase is half-way there" change. Making it safely opt-in touches the planner init, the
> artifact-store proxy, `list_artifacts` dedup/rendering, the resolve-on-miss path, the playground
> backend **and** Svelte frontend, the `react` template, docs, and the test suite. See the detailed
> plan at [`000-initial-work/plan.md`](./000-initial-work/plan.md).

## Background

The two "artifact" concepts today:

1. **In-run planner registry** (`penguiflow/planner/artifact_registry.py`, `ArtifactRegistry`) — a
   per-execution index. `build_*`/`render_*` rich-output tools register `ui_component` records here;
   the actual component payload lives **inline** in `self._payloads`. The short `artifact_N` refs the
   LLM uses come from here.
2. **`ArtifactStore`** (`penguiflow/artifacts.py`) — a persistent, scoped content backend
   (`put_bytes`/`put_text`, returns a compact `ArtifactRef`). General-purpose: it stores text/JSON,
   not just binary.

The in-run registry is a hot cache for within-run composition; the store is the durable, scoped
backend. The goal is to make the registry a thin index over store-backed artifacts **when opted in**,
not to collapse the two unconditionally.

## Problem

- `ui_component` payloads are held **inline** in the in-run registry and are **not** persisted to the
  store, so they are not durable, not scoped, and not size-offloaded.
- After a HITL pause/resume (`snapshot()` → `from_snapshot()`), the in-run `_payloads` cache is empty
  and UI components cannot be reliably resolved.
- The persistence/scoping/size-offload that the real artifact backend already provides is unavailable
  to UI components.

## Why the original "unify everything" approach was rejected

The first version of this issue (and an earlier revision of the plan) made the unification
**mandatory**: delete inline delivery, require a store, flip the default store from `NoOp` to
in-memory, and rewrite `list_artifacts` so "persistent store wins". That is a **breaking** change to a
wire/streaming contract (`artifact_chunk`) plus a default-behavior flip, and it would downgrade rich
UI-component entries to generic `kind="binary"` rows on dedup. This issue replaces that with an
**opt-in** design.

## Desired outcome

- **Opt-in store-backing for UI components**, controlled by one new planner flag. The default
  (`inline`) is exactly today's behavior — no store writes, no new events, `NoOp` default store
  preserved.
- When opted in, `ui_component` payloads persist to the `ArtifactStore` (scoped: tenant/user/session/
  trace), are delivered to the frontend by **opaque id** (frontend fetches the full payload from the
  store), and **rehydrate reliably across HITL pause/resume and cross-run session reads**.
- The in-run registry stays a thin **index/cache**: short refs + LLM-facing metadata, with the full
  payload as an in-run hot cache. A resolve miss in store-backed modes fetches from the store by id,
  then caches locally.
- **No regression and no migration** for existing users. The full existing test suite passes unchanged
  on the default.

## The new flag: `ui_component_delivery` (tri-state)

`Literal["inline", "both", "artifact"]`, default `"inline"`.

| mode | inline `artifact_chunk` | persist to store | `artifact_stored` event |
|------|:-:|:-:|:-:|
| **`inline`** (default) | **yes (today)** | no | no |
| `both` | yes (legacy frontends keep working) | yes | yes (`render_*`) |
| `artifact` | no (suppressed) | yes | yes (`render_*`) |

- `inline` = exactly today. No store writes for UI components, no new events, `NoOp`-by-default
  preserved.
- `both` = migration runway: old frontends keep rendering from `artifact_chunk`; new frontends can
  switch to `artifact_stored` + fetch-by-id. A new additive event is non-breaking (consumers ignore
  unknown events).
- `artifact` = id-based delivery only.

The "visible" (`render_*`) vs "silent intermediate" (`build_*`) distinction is unchanged: `build_*`
persists silently so `render_*` can compose it, and never emits `artifact_chunk` or `artifact_stored`;
only `render_*` is delivered to the frontend.

Store-backed modes (`both`/`artifact`) **require** a real `ArtifactStore` and fail fast at planner
construction (clear `ValueError`) if only the `NoOp` default is configured — never mid-stream.

## Scope (surfaces touched)

This is the part that grew. The opt-in design is non-breaking precisely because each surface gets an
additive, mode-gated change rather than a rewrite:

- **Planner init** — new `ui_component_delivery` kwarg; init-time raise for store-backed modes with no
  real store; **`NoOp` default unchanged**.
- **Rich-output nodes** — async `_register_component_payload` with a mode-gated store write (full
  payload incl. `props` in the stored bytes; a light `component_data` descriptor in `ArtifactRef.source`);
  **keep** the inline `_emit_component_artifact`, gate its call by mode.
- **Artifact-store proxy** — split the conflated `_emit_artifact_stored_event` into orthogonal
  `emit` / `register` flags (both default `True`, so every existing binary caller is unchanged); no
  `namespace` comparison anywhere.
- **`list_artifacts`** — flip dedup to **index-wins** keyed on opaque `artifact_id`; render store-only
  UI components richly from `source.component_data` (no byte fetch) on resume/cross-run; widen the
  store-pass gate so a `ui_component` filter also runs the store pass.
- **Resolve-on-miss** — `resolve_ref_async` hydrates UI components from the stored opaque id on a
  `_payloads` miss in store-backed modes, then caches locally.
- **Playground backend** — verify `artifact_stored` SSE + artifact GET endpoints serve UI components
  by id; no default flip.
- **Playground frontend (Svelte)** — **add** an `artifact_stored` + fetch-by-id handler; **keep** the
  inline `artifact_chunk` path; dedupe in `both` mode; rebuild `dist/`.
- **`react` template** — surface the flag (`config.py.jinja`, `from_env`, `.env.example`,
  `planner.py.jinja`).
- **Docs** — additive sections for the flag, the three modes, the id-based delivery contract, and the
  `penguiflow_ui_component` namespace + `component_data` descriptor; existing "rich output is not a
  persistence API" guidance stays true for the default mode.
- **Tests** — existing suite green on `inline`; new tests for store-backed modes, proxy flag
  independence, index-wins dedup, pause/resume + cross-run rehydration, and the init-time raise.

## Non-goals / explicitly deferred

- **No default-store flip and no mandatory store.** `NoOp` stays the tier-3 default; `inline` works
  with it.
- **No removal of inline delivery.** `artifact_chunk` is kept and gated by mode, so AG-UI and existing
  frontends are unaffected in `inline`/`both`.
- **Registry rename** (`ArtifactRegistry` → `InRunArtifactIndex`) is out of scope; it is an internal,
  ~17-file pure rename orthogonal to this feature and can land separately. The persisted snapshot key
  `"artifact_registry"` must stay literal regardless.

## Notes / context

The codebase already provides the rails this design rides on, which is what keeps each surface change
additive rather than a rewrite:

- `resolve_ref_async` already hydrates store-backed JSON via `_maybe_hydrate_stored_payload`, which
  expects the `{"artifact": {"id": ...}}` stub shape we construct ephemerally on a miss.
- `_register_component_payload` is the **single shared choke point** for both `build_*` and `render_*`.
- The playground already has the `artifact_stored` SSE event and artifact GET endpoints; the frontend
  already fetches MCP-app artifacts by id (prior art for the new handler).
- `list_artifacts` already merges registry **and** store results — the change is flipping the dedup
  direction and rendering store-only entries richly, not building merge logic from scratch.

A detailed, file-and-line-level implementation plan lives at
[`000-initial-work/plan.md`](./000-initial-work/plan.md).
