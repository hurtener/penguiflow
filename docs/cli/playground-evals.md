# Playground eval workflow

## What it is / when to use it

Use Playground evals when you want an interactive trace-to-dataset-to-debug loop:

- curate datasets from real traces
- load an existing dataset and inspect it before rerunning
- run evals and review worst cases in the same UI
- open prediction traces directly from failing rows

This complements `penguiflow eval`:

- Playground is best for authoring, inspection, and triage
- `penguiflow eval` is best for committed specs, CI, and repeatable reruns

## Core workflow

1. Run the Playground:

```bash
uv run penguiflow dev --project-root <agent_project>
```

2. Exercise the agent in `Chat` and inspect traces.

3. In `Eval`:

- tag traces you want to keep
- export a dataset from tagged traces, or load a dataset bundle from disk
- run eval with a CLI-compatible `metric_spec`
- inspect low-scoring rows and open prediction traces for debugging

4. In `Trajectory` / divergence review:

- use `Copy` to export the currently active trajectory view
- in `actual` view it copies full trajectory JSON for the prediction path
- in `reference` view it copies full trajectory JSON for the reference path
- in `divergence` view it copies structured diff JSON with path-level changes

5. Once the workflow is stable, commit the dataset/specs and rerun with:

```bash
uv run penguiflow eval evaluate --spec path/to/evaluate.spec.json
```

## Why Playground adds value

Playground does more than mirror CLI eval commands.

It lets you work from observed behavior:

- real user-like traces instead of synthetic-only query suites
- immediate failing-case triage via trace links
- dataset curation from multi-turn sessions
- eval case selection where the interesting target is a specific turn or intermediary step, not just the final answer

That makes it the right place to discover what should become a durable dataset or metric rule.

## Dataset semantics

- exported datasets keep the standard bundle shape: `dataset.jsonl` plus `manifest.json`
- Playground export defaults to an app-scoped eval directory:
  - with `agent_package`: `<project_root>/src/<agent_package>/evals/playground_export/dataset` when `src/` exists, otherwise `<project_root>/<agent_package>/evals/playground_export/dataset`
  - without `agent_package`: `<project_root>/evals/playground_export/dataset`
- if the target export directory already exists, Playground auto-renames to `dataset-2`, `dataset-3`, ... instead of overwriting
- loaded datasets are preview/staging inputs for eval; they do not import traces into the Playground state store
- eval runs store prediction traces in the active Playground process so result rows can open into trace review
- eval runs require at least one `val` case; `test` is optional for diagnostic workflows
- when `min_test_score` is set but no `test` split exists, `passed_threshold` is `null`

Because the formats stay aligned, the same dataset can move between Playground and `penguiflow eval` without conversion.

## Trajectory copy and sharing

Trajectory review includes copy actions with notification feedback:

- `Copy` is contextual and always targets the currently selected view (`actual`, `reference`, or `divergence`)
- trajectory views copy full JSON payloads aligned with metric-facing structure
- divergence view copies structured diff JSON (`path`, `reference`, `actual`) for comparison metrics

This is designed for external triage loops where raw JSON is too noisy.

## Metric guidance for Playground

Playground review works best with structured metrics.

Recommended shape:

- define metrics with a stable name and short summary
- use stable criterion ids
- return per-case structured `checks`
- keep `feedback` concise and focused on failures

Why: the UI can then show rubric context, `Failed: ...` rows for specific criteria, and `✓ All pass` when every structured check succeeds.

## Recommended operating model

- start in Playground to discover trace patterns, route/tool policy expectations, and likely failure clusters
- freeze the useful dataset and metric into committed files
- run `penguiflow eval` for local repeatability and CI gating

In short: author in Playground, operationalize in CLI.

For a complete fresh-agent setup (including multi-turn case design), see **[ReAct planner eval guide](react-planner-evals.md)**.
