# Phase 010: Docs — additive `ui_component_delivery` documentation

## Objective
Document the new opt-in feature additively, without rewriting existing truths as false. Add a section covering
`ui_component_delivery` (default `inline`), the three modes, the id-based delivery contract, the
`penguiflow_ui_component` namespace + `component_data` descriptor, opaque store ids, the init-time raise for
store-backed modes, and the session-scoped cross-run composition boundary.

## Tasks
1. Add a `ui_component_delivery` section to `docs/planner/rich-output.md` (+ `rich-output-extensions.md`,
   `rich-output-skills.md`).
2. Add the opt-in store-backed model to `docs/tools/artifacts-guide.md`.
3. Frame store-backing as opt-in; keep existing "build_* are not persistence APIs" / "rich output does not replace
   binary artifacts" statements true for the default (`inline`) mode.

## Detailed Steps

### Step 1: `docs/planner/rich-output.md` (+ siblings)
- Add a section documenting:
  - The flag `ui_component_delivery: Literal["inline","both","artifact"]`, default `"inline"`.
  - The three modes table: `inline` (today; inline `artifact_chunk`, no store, no event), `both` (inline chunk +
    store + `artifact_stored`, migration runway), `artifact` (id-based delivery only, inline suppressed).
  - The id-based delivery contract: `artifact_stored` carries an OPAQUE id; the frontend fetches the full payload by
    id. Store ids are never predicted/constructed by callers.
  - The `penguiflow_ui_component` namespace and the `component_data` descriptor shape
    (`kind`/`component`/`title`/`summary`/`metadata`; `props` lives in the bytes, not the descriptor).
  - Store-backed modes REQUIRE a real `ArtifactStore` — a `ValueError` is raised at planner init for
    `both`/`artifact` with a NoOp/None store (point to passing `artifact_store=InMemoryArtifactStore()`).
  - Cross-run composition is session-scoped: store-only components are reusable building blocks WITHIN the same
    tenant/user/session; resolution refuses across sessions.
- Apply the same additive note to `rich-output-extensions.md` and `rich-output-skills.md` where relevant.

### Step 2: `docs/tools/artifacts-guide.md`
- Add the opt-in store-backed UI-component model: how UI components persist to the `ArtifactStore` in `both`/
  `artifact`, the namespace, and the id-based fetch contract.

### Step 3: Preserve existing truths
- Do NOT rewrite "build_* are not persistence APIs" / "rich output does not replace binary artifacts" as false —
  they remain TRUE in the default (`inline`) mode. Frame store-backing strictly as opt-in.
- Do NOT add any version/CHANGELOG note (decision 5 — out of scope).

## Required Code
```text
No code changes — Markdown documentation only. Files:
- docs/planner/rich-output.md            (primary section)
- docs/planner/rich-output-extensions.md (additive note)
- docs/planner/rich-output-skills.md     (additive note)
- docs/tools/artifacts-guide.md          (opt-in store-backed model)
```

## Exit Criteria (Success)
- [ ] `docs/planner/rich-output.md` documents `ui_component_delivery` (default `inline`), the three modes, the
      id-based delivery contract, the `penguiflow_ui_component` namespace + `component_data` descriptor, opaque store
      ids, and the init-time raise for store-backed modes.
- [ ] The session-scoped cross-run composition boundary is documented (reusable within a session; refused across
      sessions).
- [ ] `docs/tools/artifacts-guide.md` documents the opt-in store-backed UI-component model.
- [ ] Existing "build_* are not persistence APIs" / "rich output does not replace binary artifacts" statements are
      preserved as true for `inline` mode (not rewritten as false).
- [ ] No version/CHANGELOG content is added.
- [ ] `mkdocs build --strict` succeeds (no broken links / strict-mode errors).

## Implementation Notes
- Depends on Phase 000 (the flag semantics, modes, and init-raise wording must be accurate). Best authored after the
  implementation phases stabilize so examples match real behavior, but only hard-depends on Phase 000.
- Additive, not a rewrite — the headline is that the default is unchanged and store-backing is opt-in.
- The docs build requires the docs extra: `uv pip install -e ".[dev,docs]"`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv pip install -e ".[dev,docs]"
uv run mkdocs build --strict

# Confirm the flag is documented in the primary files
grep -rl "ui_component_delivery" docs/planner/rich-output.md docs/tools/artifacts-guide.md
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes

Documentation-only phase. All factual claims were cross-checked against the actual
implementation before writing (the flag name, mode semantics, the `penguiflow_ui_component`
namespace string, the `component_data` descriptor shape, the init-time `ValueError`, and the
session-scoped cross-run boundary all match the code).

- **`docs/planner/rich-output.md`** (primary):
  - Added a new `### UI-component delivery (\`ui_component_delivery\`)` section under "Contract
    surface", immediately after "The downstream frontend contract does not change". It documents:
    the flag (`Literal["inline","both","artifact"]`, default `"inline"`); the three-mode table;
    the id-based delivery contract (opaque `artifact_id`, frontend fetch-by-id, `both`-mode
    shared-id dedupe); the `penguiflow_ui_component` namespace and the exact `component_data`
    descriptor (`kind`/`component`/`title`/`summary`/`metadata`, with `props` explicitly in the
    bytes, not the descriptor); the init-time `ValueError` for store-backed modes with a
    NoOp/None store (with a correct/incorrect code example pointing to
    `artifact_store=InMemoryArtifactStore()`); and the session-scoped cross-run composition
    boundary (reusable within tenant/user/session, refused across sessions, namespace-gated,
    unknown refs still raise `Unknown artifact_ref`).
  - Reframed the two "Non-goals / boundaries" bullets ("rich output does not replace binary
    artifacts" and "`build_*` tools are not general persistence APIs") with parenthetical
    additive notes: they remain TRUE for the default `inline` mode and the opt-in store-backed
    modes are called out as additive, not a contradiction. Both link to the new section.
  - Augmented the "Unknown `artifact_ref`" failure-mode fix with a note that `inline` refs do
    not survive across runs and that store-backed modes enable cross-run composition within a
    session.

- **`docs/planner/rich-output-extensions.md`** (additive note): added an admonition under "The
  invariant you should preserve" explaining that the `artifact_chunk` contract is the default
  and unchanged, that opt-in store-backed modes persist + deliver by id additively, and that new
  renderers need no special handling (the canonical `{component, props, title?}` payload is what
  is persisted/hydrated). Links to the primary section.

- **`docs/planner/rich-output-skills.md`** (additive note): added an admonition under "Pattern B:
  build-first composition skill" noting that build-first is unchanged in `inline`, and that in
  store-backed modes persisted `build_*` components become reusable across runs within the same
  session, scope-checked by the planner. Links to the primary section.

- **`docs/tools/artifacts-guide.md`** (opt-in store-backed model): added an "Opt-in: persisting
  UI components to the ArtifactStore" subsection under "Artifact Registry (UI Components)",
  covering the mode table, the plumbing/scope/event path, the namespace, the bytes-vs-descriptor
  split, the id-based fetch contract (`GET /artifacts/{id}`), the store-required init raise, and
  the session scope boundary. Closes with an admonition reaffirming that the inline default is
  unchanged and the "not persistence APIs"/"does not replace binary artifacts" statements stay
  true for the default mode. Links to the primary section (relative `../planner/rich-output.md`).

### Key Considerations

- **Placement of the primary section.** I put it inside "Contract surface" right after the
  frontend-contract subsection rather than at the end of the file, because the delivery mode IS a
  contract concern and readers encountering the `artifact_chunk` contract immediately benefit from
  learning it can additionally be store-backed. The anchor mkdocs generates is
  `#ui-component-delivery-ui_component_delivery` (verified in the built HTML); all cross-doc links
  use that exact anchor.
- **Additive framing, per Step 3 / Exit Criteria.** I did not rewrite any existing "build_* are
  not persistence APIs" / "rich output does not replace binary artifacts" statement as false.
  Instead I qualified them inline as true for the default `inline` mode and pointed to the opt-in
  feature. This satisfies the "preserve existing truths" requirement literally.
- **Accuracy over the plan's paraphrase.** Where the plan and the code could differ, I documented
  the code. Verified directly: the namespace literal `"penguiflow_ui_component"`
  (`nodes.py:557`, `artifact_registry.py:389`); the descriptor shape (`nodes.py:559-565`); the
  init `ValueError` raised in `_init_react_planner` (`react_init.py:475-483`) with wording that
  points to `InMemoryArtifactStore()`; the inline-emit gate
  `if emit_visible and delivery in {"inline", "both"}:` and the `both`-mode shared-id threading
  into the inline chunk `meta` (`nodes.py:497-500`); the cross-run namespace gate via
  `get_metadata().namespace` and scope-checked `ScopedArtifacts.download` (`artifact_registry.py:381-394`).
- **No CHANGELOG / version content** was added (Step 3 / decision 5).

### Assumptions

- I assumed the documented behavior is the **final** state from Phases 000–009 (the prompt states
  they are implemented and verified). I verified the load-bearing facts against the current source
  rather than relying solely on the plan, so the docs describe what the code does today.
- I assumed `docs/tools/artifacts-guide.md` should still be edited even though it is in mkdocs'
  `exclude_docs` (see Risks below): the phase's Tasks, Exit Criteria, and the verification grep all
  explicitly target that file, so documenting there is required regardless of whether it renders in
  the published site.
- I assumed the relative link from `artifacts-guide.md` to `../planner/rich-output.md#...` is the
  correct form (it is, by directory layout), even though strict mode does not validate it because
  the file is excluded from the build.

### Deviations from Plan

None. All three tasks and all detailed steps were implemented as specified. No code changed
(documentation only, as the phase requires).

### Potential Risks & Reviewer Attention Points

- **`docs/tools/artifacts-guide.md` is excluded from the mkdocs build.** `mkdocs.yml`
  `exclude_docs` includes `tools/*-guide.md` (line 22), which matches `artifacts-guide.md`. So my
  edit there is NOT rendered into the published site and its internal link to the primary section
  is NOT validated by `--strict`. This is consistent with the phase, which still names that file
  and greps for the flag in it — the edit is correct and useful in-source, but a reviewer should
  know it does not appear on the docs site. The canonical, *rendered* documentation of the
  feature lives in `docs/planner/rich-output.md` (which IS in the nav and IS validated).
- **Anchor stability.** All cross-doc/in-doc links target
  `#ui-component-delivery-ui_component_delivery`. If the heading text changes later, those four
  links must be updated. I verified the anchor against the built HTML.
- **No content drift risk on existing truths.** I deliberately kept the original "non-goals"
  wording intact and only appended qualifiers, so the diff is minimal and the existing statements
  remain readable as-is.

### Verification Results

- `uv run mkdocs build --strict` → **exit code 0** (succeeds; the only output is pre-existing
  INFO-level "pages not in nav" notices unrelated to this phase — no strict errors, no broken
  links). Confirmed the generated anchor `id="ui-component-delivery-ui_component_delivery"` exists
  in `site/planner/rich-output/index.html`.
- `grep -rl "ui_component_delivery" docs/planner/rich-output.md docs/tools/artifacts-guide.md` →
  both files match.
- `uv run ruff check .` → **All checks passed!** (run for completeness; no Python/config changed
  in this phase).

### Files Modified

- `docs/planner/rich-output.md`
- `docs/planner/rich-output-extensions.md`
- `docs/planner/rich-output-skills.md`
- `docs/tools/artifacts-guide.md`
- `docs/RFC/ToDo/issue-118/000-initial-work/phases/phase-010.md` (this notes section)
