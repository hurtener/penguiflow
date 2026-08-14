# Playground Eval Workflow v0 (Tag -> Export/Load -> Evaluate -> Debug)

## Goal

Add a Playground-first debugging workflow for the new eval API without breaking the existing CLI path.

The v0 UX should let a user:

- tag traces inside the Playground UI
- export tagged traces into a dataset bundle (JSONL + manifest)
- load an existing dataset bundle from disk
- run baseline evaluation over that dataset
- inspect failing cases by opening the exact prediction trace in the Playground trace viewer

Non-goals for v0:

- no new CLI commands
- no requirement for a persistent StateStore (Playground can remain in-memory)
- no candidate optimization UI
- no “import trace from file into Playground”

## Design Principles

- Keep the current CLI workflow fully viable in parallel.
- Prefer reusing existing data formats (`dataset.jsonl`, `manifest.json`) and existing eval internals.
- Make the debug loop concrete: “case row -> feedback -> open trace -> fix -> rerun”.
- Avoid coupling the UI to a specific example. The UI should work for any agent project that works in `penguiflow dev`.
- Reuse CLI spec shapes where possible (especially `metric_spec`) so users can copy/paste between UI and CLI.

## Current Constraints (Relevant)

- Playground trace inspection requires `trace_id` + `session_id` (`GET /trajectory/{trace_id}?session_id=...`).
- In-memory StateStore is process-local; it is fine for a Playground-only loop, but cannot bridge CLI runs.
- Today there is no “import trajectory JSON” endpoint.

## Decisions (v0)

- **Dataset selection**: a dataset is defined by selecting stored traces via tags.
  - Selection semantics: `ANY` tag match (OR) for a list of tags is sufficient for v0.
  - Tag matching is exact string match.
- **Split assignment**: split is determined by tags on the trace (`split:val`, `split:test`).
  - UI may offer a convenience dropdown that just adds/removes those tags.
  - If no split tag, treat as `unknown` and exclude from threshold gating; still show in results.
- **Replay contexts**: evaluation runs the agent with the dataset `question` and (optionally) the stored contexts.
  - v0 default: provide `llm_context` and `tool_context` from `gold_trace.inputs` when present.
  - Reason: improves reproducibility and enables “debug what happened under the same context”.
- **Failing cases**: `min_test_score` gates the aggregate `test_score`; it is not a per-case threshold.
  - UI default: always show “worst N by score” for each split.
- **Persistence**: everything required for the debug loop is held in the running Playground process.
  - Datasets can be read/written on disk using the same bundle format as CLI.
  - Eval runs themselves need not be persisted for v0.

## Key Idea

Run eval *inside* the Playground backend process, using the same agent wrapper + same Playground `StateStore`.

If we run each evaluated case as a normal chat call (with an eval-specific `session_id`), the wrapper will persist each prediction trajectory into the Playground store. Then the UI can open failing traces immediately.

This keeps the CLI stable because we are not changing `penguiflow eval ...`; we are adding a Playground-only orchestration layer.

## Data Model

### Tags

Use trajectory metadata tags (already present) to select traces:

- `dataset:<name>` (user-defined)
- optionally `split:val` / `split:test` (already supported by export logic)

Tagging means: read trajectory -> update `trajectory.metadata["tags"]` -> re-save trajectory with same `(trace_id, session_id)`.

Constraints:

- Tagging requires a stored trajectory. If a trace has events but no trajectory, tagging should return a clear error.
- Tag updates must preserve all existing metadata fields.

### Dataset bundle

Use existing bundle format:

- `dataset.jsonl` rows shaped like:
  - `example_id`
  - `split`
  - `question`
  - `gold_trace` (portable trace row)
- `manifest.json` (existing schema)

We can generate this via existing `penguiflow.evals.api.export_dataset(...)` when exporting to disk.

Interoperability requirement:

- The Playground must produce the same dataset bundle shape as the CLI export so the dataset can be evaluated by either path.

### Eval run results

For Playground UI, we need per-case rows (not only aggregates):

- `run_id`
- `dataset_path` (if from disk)
- `counts` (val/test/total)
- `min_test_score` + `passed_threshold` (aggregate gating)
- `cases[]` (sorted worst-first by score):
  - `example_id`, `split`
  - `score`, `feedback`
  - `pred_trace_id`, `pred_session_id` (so UI can open the trace)
  - optional: `question` (for quick scanning)
  - optional: `context_match` (if available)

Note: `min_test_score` is an aggregate threshold. For per-case filtering, the UI should default to “worst N” and optionally allow “score < X”.

## Backend Plan (Playground API)

Add a small set of Playground-only endpoints (under `penguiflow/cli/playground.py`) that orchestrate existing eval code.

### 1) Trace list + tagging

- `GET /traces` -> list recent trace refs across sessions (uses StateStore optional `list_trace_refs` if available).
- `POST /traces/{trace_id}/tags` (body: `session_id`, `add[]`, `remove[]`) -> updates tags on the stored trajectory.

This unlocks “tag any trace” without touching CLI or eval internals.

### 2) Export dataset from tags

- `POST /eval/datasets/export`
  - input: `selector` (tag match), `output_dir` (relative to project root), `redaction_profile`
  - output: `dataset_path`, `manifest_path`, `trace_count`

Implementation: call existing `penguiflow.evals.api.export_dataset(state_store=store, output_dir=..., selector=...)`.

### 3) Load dataset from disk

- `POST /eval/datasets/load`
  - input: `dataset_path` (and optional `manifest_path`)
  - output: `dataset_summary` (counts/splits, first N example_ids/questions)

Implementation: read JSONL + manifest in backend, return a safe summary.
Safety: restrict allowed paths to under `project_root`.

Notes:

- “Dataset path” should accept either the dataset file (`.../dataset.jsonl`) or a dataset directory; if directory, resolve `dataset.jsonl` + `manifest.json` within.

### 4) Run evaluation (Playground-mode)

- `POST /eval/run`
  - input:
    - `dataset_path`
    - `metric_spec` (same structure as CLI evaluate spec)
    - optional: `min_test_score`
    - optional: `max_cases`, `only_split`, `worst_n`
  - output: `EvalRunResult` (summary + per-case rows with trace pointers)

Implementation approach (path of least resistance, reusing CLI internals):

1. Load dataset rows from `dataset_path` (same `dataset.jsonl` used by CLI).
2. Build a metric callable using the same `metric_spec` resolution as `penguiflow eval evaluate`.
3. For each dataset row, execute the agent via the existing Playground agent wrapper (same as Chat):
   - `pred_session_id = "eval:" + run_id` (stable per run)
   - `query = row["question"]`
   - `llm_context` / `tool_context` from `row["gold_trace"]["inputs"]` when available.
4. Capture the returned `pred_trace_id` from the wrapper output; that trace is now persisted in the Playground StateStore and can be opened immediately.
5. Compute score + feedback by calling the metric callable with the same argument conventions as the eval runner.
6. Compute aggregate `val_score`/`test_score` and `passed_threshold` using existing eval helper logic (do not fork semantics).
7. Return `cases[]` with `(pred_trace_id, pred_session_id)` so the UI can open traces.

This yields immediate debug ability without any new storage, and it keeps the “metric spec” compatible with the CLI.

### Cancellation / limits (v0)

- v0 must include a hard `max_cases` cap and expose it in the UI.
- Cancellation can be best-effort by running evaluation in a background task and supporting a `DELETE /eval/run/{run_id}` later; not required for the first increment.

## UI Plan (Playground UI)

Add an “Eval” surface with minimal UI work:

- A new card in the right sidebar (or a small tab in the center column) named `Eval`.
- Sections:
  1) **Trace tagging**: show recent traces, allow toggle tags, set split tag.
  2) **Dataset**: export dataset from a tag selector; load dataset from a path.
  3) **Evaluate**: choose dataset, choose metric preset/path, run eval.
  4) **Results**: table of cases with score + feedback; click “Open trace” to load `pred_trace_id` under `pred_session_id`.

Opening a trace can reuse existing UI logic that calls `fetchTrajectory(traceId, sessionId)`.

## Compatibility / Keeping CLI Stable

- CLI remains the durable/reproducible path.
- Playground eval is a convenience loop and can share the same dataset bundle format so you can:
  - export from Playground -> run CLI evaluate later
  - collect/export via CLI -> load + evaluate in Playground

No CLI option changes required.

Explicit reuse points:

- Dataset bundle shape is identical to CLI output (`dataset.jsonl` + `manifest.json`).
- `metric_spec` payload shape is identical to CLI evaluate spec.
- Threshold gating semantics (`min_test_score`) match CLI semantics.

## Incremental Delivery (Recommended)

1. Backend: `GET /traces` + `POST /traces/{trace_id}/tags`.
2. UI: minimal trace list + tag toggles.
3. Backend: dataset export endpoint via `export_dataset`.
4. UI: dataset export form (selector + output dir).
5. Backend: dataset load summary endpoint.
6. Backend: Playground-mode eval run endpoint with per-case results + persisted pred traces.
7. UI: results table + “Open trace” button.

## Risks / Edge Cases

- Trajectory may be missing for some trace ids (events-only). Tagging should fail gracefully.
- Re-saving trajectories must preserve existing metadata and avoid clobbering fields.
- Metrics may depend on parts of `gold_trace` that are absent due to redaction; v0 should document this.
- Path safety for dataset load/export: constrain to `project_root`.
- Long-running eval runs: v0 can be synchronous; later we can add async + progress.
- Context replay can cause side effects if `tool_context` includes credentials/ids; v0 should recommend “side-effect free” tools for evals.

## Success Criteria

- User can tag a trace, export dataset, run eval, and open a failing prediction trace in the Playground UI in under 2 minutes.
- CLI continues to work unchanged.
- Dataset bundles are interoperable between Playground and CLI.
