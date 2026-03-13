# Playground Eval Workflow v0

## Status

This document describes the current state of the Playground eval workflow, what is already implemented, what is intentionally discarded, and the remaining delivery increments.

It is written as a standalone plan for finishing v0 from the current repo state.

## Goal

Provide a Playground-first eval and debug workflow that lets a user:

- tag traces inside Playground
- build a dataset from tagged traces
- load an existing dataset from a path on disk
- run evaluation from Playground against that dataset
- open prediction traces directly in the Playground trace viewer for debugging

The workflow must remain compatible with the existing CLI dataset and eval formats.

## Current Product Position

The backend foundation for Playground evals now exists.

The current UI implementation does not match the intended product shape and should be treated as disposable.

Specifically, the temporary right-sidebar Eval card should be considered discarded design, even if parts of its API wiring can still be reused internally.

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

### Dataset load

Dataset load is a read/validate/preview action.

It reads `dataset.jsonl` and optional `manifest.json` from disk and returns a summary to the UI.

It does **not** populate the Playground in-memory state store.

### Eval run

Eval run reads dataset rows from disk, executes them through the Playground backend using the running agent wrapper, computes metric results, and stores prediction traces in the active Playground process.

This is what makes "Open trace" possible for eval results.

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

## Implemented but Discarded UI Scope

The following UI work exists in code but should not be treated as the v0 product surface:

- right-sidebar `EvalCard`
- sidebar-based trace tagging controls
- sidebar-based dataset export/load/run forms
- sidebar-based eval results list

Reason: this shape is too cramped, obscures the workflow, and does not make evals feel like a first-class Playground activity.

## Missing v0 Product Work

Almost all remaining work is now UI and UX restructuring.

### Missing information architecture

- Add Eval as a first-class Playground section alongside `Chat`, `Setup`, and `Context`.
- Remove the right-sidebar Eval card from the intended UX.
- Make the eval flow navigable as a whole, not as unrelated controls.

### Missing tagging UX

- Show recent traces in a trace-selection workflow.
- Support adding/removing arbitrary tags, not just split toggles.
- Make split assignment (`split:val`, `split:test`) a clear part of tagging.
- Explain that tagged traces are what power dataset export.

### Missing dataset UX

- Clarify the difference between:
  - building a dataset from tagged traces
  - loading an existing dataset from disk
- Show the loaded dataset summary more clearly.
- Make it obvious that load is preview/staging for evaluation, not state-store import.

### Missing eval-run UX

- Present metric configuration in a clearer form.
- Show run counts, threshold state, and worst cases more clearly.
- Support an explicit debug loop from failing case to trace inspection.

### Missing result/debug UX

- Make "Open trace" part of a proper results table or list.
- Show question, score, split, and feedback in a scannable layout.
- Make it easy to move from results back into trace/context inspection.

## Recommended v0 UI Shape

Add an `Eval` top-level section in the center workspace.

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

### Increment 1: Replace sidebar eval surface

Outcome:

- Eval becomes a first-class center section/tab.
- The right-sidebar Eval card is removed from the intended experience.

Notes:

- Existing service calls and backend endpoints can be reused.
- This increment is mostly structural UI work.

### Increment 2: Build proper trace-tagging workflow

Outcome:

- Users can tag traces intentionally for dataset construction.
- Users can manage arbitrary tags and split tags clearly.

Notes:

- Current split-only controls are insufficient.
- This increment should explain dataset selection semantics directly in the UI.

### Increment 3: Build proper dataset section

Outcome:

- Users can export datasets from tags.
- Users can load an existing dataset from a path.
- Users can see clearly what was loaded and what it means.

Notes:

- Keep path-based loading only for v0.
- Do not add upload in this phase.

### Increment 4: Build proper eval-run section

Outcome:

- Users can run evaluation with clear metric and limit controls.
- The run summary is understandable without reading raw JSON-like fragments.

Notes:

- Reuse current backend eval execution.
- Keep CLI-compatible `metric_spec` input.

### Increment 5: Build proper results/debug section

Outcome:

- Users can inspect worst cases, read feedback, and open prediction traces directly.
- The debug loop becomes concrete and discoverable.

Notes:

- Prediction traces already exist in backend behavior.
- This increment is about making the flow legible and useful.

## Success Criteria

v0 is complete when:

- Eval is exposed as a first-class Playground section.
- A user can tag traces and export a dataset without ambiguity.
- A user can load a dataset from disk and understand that this is preview/staging, not state import.
- A user can run eval from Playground and inspect per-case results.
- A user can open prediction traces from failing or low-scoring cases in the trace viewer.
- The workflow remains compatible with the existing CLI dataset/eval formats.

## Explicit Non-Goals For This Revision

- Do not preserve the current sidebar Eval card UX.
- Do not add upload support in this revision.
- Do not redefine dataset load as state-store population.
- Do not add new CLI commands.
