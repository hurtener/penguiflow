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

4. Once the workflow is stable, commit the dataset/specs and rerun with:

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
- loaded datasets are preview/staging inputs for eval; they do not import traces into the Playground state store
- eval runs store prediction traces in the active Playground process so result rows can open into trace review

Because the formats stay aligned, the same dataset can move between Playground and `penguiflow eval` without conversion.

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
