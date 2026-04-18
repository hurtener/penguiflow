# `penguiflow eval`

## What it is / when to use it

`penguiflow eval` is the canonical CLI entrypoint for repeatable, spec-driven trace-derived evaluation.

Current default behavior is minimalistic:

- text summary in stdout (pytest-like)
- no verbose JSON blob output
- no forensic workspace artifacts for eval execution

Use it when you want to:

- collect traces from a query suite
- export a reusable dataset bundle
- collect and export traces without running evaluation (metric design/debugging)
- run baseline + patch candidate sweeps with holdout verification on a committed dataset
- rerun evaluation on an already-exported dataset without recollecting traces

Use Playground instead when you want to:

- curate datasets from recently observed traces
- inspect failing cases and open prediction traces directly in the UI
- shape eval cases interactively before you freeze them into committed specs
- work from multi-turn sessions where a useful eval target may come from a specific turn or intermediary step, not only the final answer

The two workflows are complementary: author/debug in Playground, then operationalize in `penguiflow eval`.

## Commands

### `penguiflow eval collect`

Collects traces from a query suite and exports a minimal dataset bundle,
without running evaluation/sweeps:

```bash
uv run penguiflow eval collect --spec examples/my_agent/datasets/eval_v1/collect.spec.json
```

Current output files for `collect`:

- `dataset.jsonl`
- `manifest.json`

### `penguiflow eval evaluate`

Runs evaluation only, using an existing dataset bundle:

```bash
uv run penguiflow eval evaluate --spec examples/my_agent/datasets/eval_v1/evaluate.spec.json
```

This is the fast path for repeated candidate tuning after dataset export is stable.
Evaluation is in-memory by default and prints a text summary.

## Baseline-only mode

Baseline-only mode runs when `evaluate.spec.json` omits `candidates_path`.

- no candidate ranking is computed
- output reports baseline scores directly (`val_score`, `test_score`)
- optional threshold gate via `min_test_score`

## Playground companion workflow

The Playground uses the same dataset/eval formats for an interactive debug loop:

- tag stored traces
- export a dataset bundle or load an existing one
- run eval with a CLI-compatible `metric_spec`
- inspect per-case scores, feedback, and structured failed checks
- open prediction traces for low-scoring or failing rows
- copy the active trajectory view JSON payload (actual, reference, or divergence) for external triage and metric authoring

Playground export defaults are app-scoped and collision-safe:

- with `agent_package`: `<project_root>/src/<agent_package>/evals/playground_export/dataset` when `src/` exists, otherwise `<project_root>/<agent_package>/evals/playground_export/dataset`
- without `agent_package`: `<project_root>/evals/playground_export/dataset`
- existing targets are auto-renamed (`dataset-2`, `dataset-3`, ...) instead of overwritten

When the workflow stabilizes, keep the dataset/specs in version control and rerun them with `penguiflow eval`.

See **[Playground eval workflow](playground-evals.md)**.
For a full step-by-step setup for new ReAct planner agents, see **[ReAct planner eval guide](react-planner-evals.md)**.

## Spec formats

## Path resolution rule (canonical)

All path-like fields in eval specs use one base rule:

- First, resolve `project_root` itself:
  - absolute `project_root` is used as-is
  - relative `project_root` is resolved from the current working directory

- If `project_root` is present, all remaining relative paths resolve from `project_root`.
- If `project_root` is absent (only valid for `evaluate.spec.json`), relative
  paths resolve from the spec file directory.

This applies uniformly to input/output fields in `collect.spec.json`
and `evaluate.spec.json`.

CLI commands now emit concise text summaries instead of JSON blobs.

### Collect spec (`collect.spec.json`)

Required fields:

- `project_root`
- `query_suite_path`
- `output_dir`
- `session_id`
- `dataset_tag`

Optional fields:

- `agent_package`
- `state_store_spec`

Minimal example:

```json
{
  "project_root": ".",
  "query_suite_path": "datasets/eval_v1/query_suite.json",
  "output_dir": "artifacts/eval/collect-local",
  "session_id": "collect-session-1",
  "dataset_tag": "dataset:eval-v1",
  "agent_package": "my_agent"
}
```

### Dataset-eval spec (`evaluate.spec.json`)

Required fields:

- `dataset_path`
- `metric_spec`

At least one execution source is required:

- `run_one_spec`, or
- `project_root` (with optional `agent_package` for discovery)

Optional fields:

- `candidates_path` (candidate ranking mode; omit for baseline mode)
- `report_path` (optional single JSON report output)
- `min_test_score` (threshold gate on test score)

Minimal example:

```json
{
  "dataset_path": "artifacts/eval/collect-local/dataset.jsonl",
  "metric_spec": "my_agent.evals.metrics:policy_metric",
  "min_test_score": 0.8,
  "report_path": "reports/eval-dataset.json",
  "project_root": ".",
  "agent_package": "my_agent"
}
```

Compatibility note:

- `output_dir` in dataset-eval specs is accepted for legacy specs but ignored.

## Output Contract

Default `eval evaluate`:

- stdout text summary only
- no files written

Default `eval collect`:

- writes `dataset.jsonl` and `manifest.json`

Optional report mode (`report_path` in evaluate specs):

- writes exactly one JSON report file
- no extra eval workspace artifacts

`score` semantics:

- metric returns per-example score (`float` or `{ "score": ... }`)
- eval aggregates split scores with arithmetic mean
- baseline mode outputs `val_score` and `test_score` (`test_score` is `null` for val-only diagnostic datasets)
- candidate mode outputs `val_baseline_score`, `val_winner_score`, `test_baseline_score`, `test_winner_score` (test scores are `null` when no test split exists)
- datasets must include at least one `val` example; `test` is optional for diagnostic runs

## Metric design guidance

- Prefer deterministic, trace-aware metrics as your primary CI gate (stable, cheap, reproducible).
- Use LLM-as-judge metrics as a secondary signal for ambiguous quality checks (tone/helpfulness/nuance).
- If you use LLM judging, pin model + prompt + temperature and avoid using judge-only scores as the sole regression gate.
- Prefer structured metrics with a stable definition, criterion ids, and per-case `checks` so Playground can show concise rubric-aware feedback.

## Environment loading behavior

- `eval collect` autoloads `<project_root>/.env` if present.
- `eval evaluate` autoloads `<project_root>/.env` if `project_root` is set.
- Existing process env vars win and are not overwritten.
- `env_files` in specs and CLI `--env-file` overrides are intentionally not part of this command surface.

Why: this matches `penguiflow dev` behavior and keeps secret loading simple and predictable.

## Threshold behavior with val-only datasets

- `min_test_score` is evaluated only when a `test` split exists
- for val-only diagnostic datasets, `passed_threshold` is reported as `null`
- this lets teams iterate on metric/debug loops before holdout gating is available
