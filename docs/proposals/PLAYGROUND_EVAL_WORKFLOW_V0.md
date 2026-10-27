# Playground Eval Workflow v0

## Status

This document now reflects the implemented v0 baseline and the remaining follow-up work.

The core Playground eval workflow is live (trace tagging, dataset export/load, run, case review, open trace).

## Goal

Provide a Playground-first eval and debug workflow that lets a user:

- tag traces inside Playground
- build a dataset from tagged traces
- load an existing dataset from a path on disk
- run evaluation from Playground against that dataset
- open prediction traces directly in the Playground trace viewer for debugging

The workflow must remain compatible with the existing CLI dataset and eval formats.

## Current Product Position

Playground eval is now a first-class center workflow and remains CLI-format-compatible.

The product now follows this model:

- author/debug interactively in Playground
- operationalize/re-run in `penguiflow eval`

## What v0 Includes

- trace tagging in Playground
- dataset export from tagged traces
- dataset load from a path on disk
- Playground-native eval execution using the running backend process
- per-case eval results with prediction trace pointers
- trace opening for debug loops inside Playground

## What v0 Does Not Include

- dataset file upload
- persistent eval-run storage beyond the running Playground process
- candidate optimization UI
- importing arbitrary trajectory JSON into the Playground state store
- new CLI commands

## Design Principles

- Keep the CLI workflow fully viable and format-compatible.
- Treat Playground eval as an interactive debug loop, not a separate eval system.
- Reuse the existing dataset bundle shape: `dataset.jsonl` plus `manifest.json`.
- Reuse CLI-compatible `metric_spec` shapes.
- Keep path-based dataset loading for v0; do not add upload in this phase.
- Make the UI reflect the workflow clearly: trace selection -> dataset -> run -> inspect.
- Do not keep the eval UX as a right-rail utility card; it should be a first-class Playground section.

## Current Constraints

- Trace inspection still requires `trace_id` plus `session_id`.
- The default Playground state store is still process-local and in-memory.
- Loading a dataset from disk does not import traces into the state store.
- Eval runs only become debuggable because Playground-mode execution stores prediction traces in the current process store.
- Path access for dataset load/export must remain restricted to the active project root.

## Current Semantics

### Trace tagging

Tagging updates `trajectory.metadata["tags"]` on a stored trajectory.

This is the source of truth for dataset selection.

### Dataset export

Dataset export selects stored traces by tag and writes a standard dataset bundle to disk.

This is compatible with the CLI export/eval path.

Default export directory behavior:

- with `agent_package`: `<project_root>/src/<agent_package>/evals/playground_export/dataset` when `src/` exists, otherwise `<project_root>/<agent_package>/evals/playground_export/dataset`
- without `agent_package`: `<project_root>/evals/playground_export/dataset`
- collision policy: auto-rename (`dataset-2`, `dataset-3`, ...), never overwrite by default

### Dataset load

Dataset load is a read/validate/preview action.

It reads `dataset.jsonl` and optional `manifest.json` from disk and returns a summary to the UI.

It does **not** populate the Playground in-memory state store.

### Eval run

Eval run reads dataset rows from disk, executes them through the Playground backend using the running agent wrapper, computes metric results, and stores prediction traces in the active Playground process.

This is what makes "Open trace" possible for eval results.

Dataset split semantics:

- at least one `val` row is required
- `test` rows are optional for diagnostic runs
- when `min_test_score` is set and no `test` rows exist, `passed_threshold` is `null`

## Implemented Backend Scope

The following backend pieces are already done:

- `GET /traces`
- `POST /traces/{trace_id}/tags`
- `POST /eval/datasets/export`
- `POST /eval/datasets/load`
- `POST /eval/run`
- path safety checks under project root
- per-case eval results with `pred_trace_id` and `pred_session_id`
- eval hard cap for `max_cases`
- prediction-trace persistence for Playground debug flow

## Implemented UI Scope (Current v0 Surface)

The current v0 UI surface includes:

- Eval section in center workflow (trace selection, dataset export/load, run, results)
- metric metadata and structured checks rendering
- clear pass/fail row semantics (`✓ All pass`, `Failed: ...`)
- case-level open-trace debug loop
- comparison + divergence review for selected eval cases
- contextual `Copy` in trajectory view:
  - `actual` view -> full trajectory JSON
  - `reference` view -> full trajectory JSON
  - `divergence` view -> structured diff JSON (`path`, `reference`, `actual`)

## Remaining Product Work

Most foundational v0 work is complete. Remaining work is coherence and trace-centric synchronization polish.

### Pending: trace-driven UI sync across existing widgets

Current behavior when selecting a trace is trajectory-first.

Pending improvement: selecting a trace should synchronize the existing widgets so the whole UI reflects that trace.

Use existing elements (no net-new major widgets):

- keep `Trajectory` as anchor
- synchronize `EventsCard` to selected trace history + follow stream
- synchronize `ArtifactsCard` to selected trace/session context using existing artifact plumbing
- continue reusing `ContextTab` from trajectory payload (`llm_context`, `tool_context`, memory, background results)

This is currently the highest-value hanging fruit for review/debug coherence.

### Pending: trace/eval review coherence

- ensure all trace-open entry points (Traces tab, Eval case open, chat completion) trigger the same sync behavior
- keep session-level widgets (`Tasks`, `Notifications`) session-scoped; do not force trace-level semantics there

### Done: tagging UX baseline

- recent traces available for selection
- arbitrary tag add/remove supported
- split tags (`split:val`, `split:test`) part of trace tagging flow

### Done: dataset UX baseline

- export from tags and load-from-path are both available
- dataset load returns summary/preview
- load semantics remain preview/staging, not state-store import

### Done: eval-run UX baseline

- run controls are exposed in Eval workflow
- run counts/threshold/case list are visible
- explicit case -> open trace debug loop is available

### Done: result/debug baseline

- results are presented in scannable rows
- open trace is integrated into eval case review
- trajectory comparison/divergence is available for failed-case triage

## Recommended v0+ Iteration Shape

Keep `Eval` as a top-level center section and iterate on trace-synced coherence.

That section should contain four sub-areas:

1. `Trace Selection`
   - recent traces
   - visible tags
   - add/remove tags
   - split assignment

2. `Dataset`
   - export from tagged traces
   - load dataset from path
   - dataset summary and examples

3. `Run`
   - selected dataset path
   - metric spec
   - max cases
   - run action

4. `Results`
   - counts and threshold state
   - per-case rows sorted worst-first
   - open trace for debug

## Delivery Increments From Current State

### Increment 1: Trace selection should synchronize Events

Outcome:

- Selecting/opening a trace updates EventsCard to that trace history (and follow stream)
- Traces tab / Eval open trace / chat completion share the same sync behavior

### Increment 2: Trace selection should synchronize Artifacts

Outcome:

- Existing ArtifactsCard reflects selected trace/session context using current artifact store/services
- Preserve session-level artifact behavior where trace metadata is unavailable

### Increment 3: Keep Context/Trajectory coupling explicit

Outcome:

- Make it explicit in UX copy that `llm_context`, `tool_context`, memory, and background results come from the selected trajectory payload
- avoid introducing duplicate context widgets

### Increment 4: Optional artifact filtering by active trace

Outcome:

- If low-risk, add trace-level filtering in ArtifactsCard using existing trace/session metadata
- keep fallback to session-level list when filtering metadata is incomplete

### Increment 5: Coherence validation pass

Outcome:

- verify that selecting a trace synchronizes trajectory, context, events, and artifacts reliably across all entry points
- ensure no regressions in eval run flow, case comparison, and copy behavior

## Success Criteria

v0 is complete when:

- Eval is exposed as a first-class Playground section.
- A user can tag traces and export a dataset without ambiguity.
- A user can load a dataset from disk and understand that this is preview/staging, not state import.
- A user can run eval from Playground and inspect per-case results.
- A user can open prediction traces from failing or low-scoring cases in the trace viewer.
- Selecting a trace synchronizes trajectory + context + events (+ artifacts where available) using existing UI widgets.
- The workflow remains compatible with the existing CLI dataset/eval formats.

## Explicit Non-Goals For This Revision

- Do not add upload support in this revision.
- Do not redefine dataset load as state-store population.
- Do not add new CLI commands.
- Do not add a separate parallel trace-review surface when existing widgets can be synchronized.
