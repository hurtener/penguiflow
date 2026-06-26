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
