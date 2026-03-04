# `penguiflow eval`

## What it is / when to use it

`penguiflow eval` is the canonical CLI entrypoint for trace-derived evaluation.

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

Use an empty candidates file (`[]`) when you want to score the current codebase
without proposing patches yet.

- winner is reported as `baseline`
- holdout runs once for baseline and reuses that score as winner score

## Spec formats

## Path resolution rule (canonical)

All path-like fields in eval specs use one base rule:

- First, resolve `project_root` itself:
  - absolute `project_root` is used as-is
  - relative `project_root` is resolved from the current working directory

- If `project_root` is present, all remaining relative paths resolve from `project_root`.
- If `project_root` is absent (only valid for `evaluate.spec.json`), relative
  paths resolve from the spec file directory.

This applies uniformly to input/output/env-file fields in `collect.spec.json`
and `evaluate.spec.json`.

CLI `--env-file` follows the same base as the loaded spec:

- `eval collect`: relative to `project_root`
- `eval evaluate`: relative to `project_root` when provided, otherwise spec dir

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
- `env_files`

Minimal example:

```json
{
  "project_root": ".",
  "query_suite_path": "datasets/eval_v1/query_suite.json",
  "output_dir": "artifacts/eval/collect-local",
  "session_id": "collect-session-1",
  "dataset_tag": "dataset:eval-v1",
  "agent_package": "my_agent",
  "env_files": [".env-values"]
}
```

### Dataset-eval spec (`evaluate.spec.json`)

Required fields:

- `dataset_path`
- `candidates_path`
- `metric_spec`
- `output_dir`

At least one execution source is required:

- `run_one_spec`, or
- `project_root` (with optional `agent_package` for discovery)

Optional fields:

- `report_path` (optional single JSON report output)
- `env_files`

Minimal example:

```json
{
  "dataset_path": "artifacts/eval/collect-local/dataset.jsonl",
  "candidates_path": "datasets/eval_v1/candidates.json",
  "metric_spec": "my_agent.evals.metrics:policy_metric",
  "output_dir": "artifacts/eval/rerun",
  "report_path": "reports/eval-dataset.json",
  "project_root": ".",
  "agent_package": "my_agent"
}
```

## Output Contract

Default `eval evaluate`:

- stdout text summary only
- no files written

Default `eval collect`:

- writes `dataset.jsonl` and `manifest.json`

Optional report mode (`report_path` in evaluate specs):

- writes exactly one JSON report file
- no extra eval workspace artifacts

## Environment loading behavior

- `env_files` from spec are loaded first.
- Repeated `--env-file` flags are loaded after spec files.
- Existing process env vars win and are not overwritten.

Why: this lets local and CI runners inject secrets safely while keeping specs reproducible.
