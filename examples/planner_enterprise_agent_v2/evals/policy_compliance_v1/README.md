# Policy Compliance Eval v1 (From Scratch)

This walkthrough is baseline-first and explicit.

Goal:
1) collect real traces into a pinned dataset,
2) inspect dataset structure,
3) build deterministic metric logic,
4) review and debug cases in Playground,
5) run baseline eval with threshold gating.

## Step 0 - Environment and credentials

Create local env file:

```bash
cp examples/planner_enterprise_agent_v2/.env.example examples/planner_enterprise_agent_v2/.env
```

Then set provider credentials in `examples/planner_enterprise_agent_v2/.env`.

Minimum requirement: one working LLM provider key + model config.

- `OPENAI_API_KEY` (or another provider key)
- `LLM_MODEL`

This folder also uses `examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/.env.eval`:

```dotenv
DSPY_CLIENT=false
REFLECTION_ENABLED=false
```

Keep eval runs deterministic and lightweight.

## Step 1 - Define query suite

Create `query_suite.json` with this structure:

```json
{
  "suite_id": "policy-compliance-v1",
  "workload": "planner_enterprise_agent_v2",
  "queries": [
    {
      "query_id": "pc-001",
      "text": "Analyze the latest architecture document and summarize key constraints",
      "split": "val"
    },
    {
      "query_id": "pc-003",
      "text": "Need quick guidance for team communication norms",
      "split": "test"
    }
  ]
}
```

Key meanings:

- `suite_id`: dataset identity/version label.
- `workload`: logical workload name in metadata.
- `query_id`: stable per-query identifier.
- `text`: user input to collect traces from.
- `split`: `val` for candidate ranking, `test` for holdout gate.

## Step 2 - Create collect spec

Create `collect.spec.json`:

```json
{
  "project_root": "examples",
  "query_suite_path": "planner_enterprise_agent_v2/evals/policy_compliance_v1/query_suite.json",
  "output_dir": "planner_enterprise_agent_v2/evals/policy_compliance_v1/dataset",
  "session_id": "policy-compliance-v1-collect",
  "dataset_tag": "dataset:policy_compliance_v1",
  "agent_package": "planner_enterprise_agent_v2",
  "env_files": ["planner_enterprise_agent_v2/.env", "planner_enterprise_agent_v2/evals/policy_compliance_v1/.env.eval"]
}
```

Key meanings:

- `project_root`: parent directory containing importable agent packages.
- `query_suite_path`: source queries.
- `output_dir`: where `dataset.jsonl` and `manifest.json` are written.
- `session_id`: trace session grouping label.
- `dataset_tag`: trace tag used for provenance.
- `agent_package`: package to discover orchestrator/planner from.
- `env_files`: env files loaded before run.

## Step 3 - Collect real traces

```bash
uv run penguiflow eval collect --spec examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/collect.spec.json
```

Expected summary lines:

- `trace_count: ...`
- `dataset_path: .../dataset/dataset.jsonl`
- `manifest_path: .../dataset/manifest.json`

## Step 4 - Inspect dataset shape before metric edits

Inspect `dataset/dataset.jsonl` directly (editor/AI-assisted review is fine).

What to extract from inspection:

- split distribution (`val` vs `test`)
- recurring tool sequence patterns from `gold_trace.trajectory_full.steps[*].action.next_node`
- deterministic route expectations based on query text

Why: metric rules should be grounded in stable observed traces, not assumptions.

## Step 5 - Implement deterministic metric logic

Edit `examples/planner_enterprise_agent_v2/evals/metrics.py`.

Recommended rule style:

- derive expected route from query text,
- compare expected route/tool policy vs observed tool sequence,
- avoid free-text answer matching as primary signal.

Recommended metric shape:

- define the metric with `@metric(...)`
- keep the docstring short and human-readable because Playground surfaces it as summary context
- use stable criterion ids for rubric items
- return structured `checks` so per-case failures stay specific and scannable
- keep `feedback` concise and failure-oriented

Why: Playground can then show the metric definition, render `Failed: ...` from criterion ids, and collapse clean passes to `✓ All pass`.

This example now includes both:

- `policy_metric` for the main pass-oriented policy baseline
- `fail_metric_demo` for intentionally mixed/failing cases during Playground UI review

The demo spec lives at `examples/planner_enterprise_agent_v2/evals/fail_metric_demo_v1/evaluate.spec.json`.

## Step 6 - Optional Playground review loop

Before freezing the baseline gate, it is often useful to review the dataset and metric in Playground:

```bash
uv run penguiflow dev --project-root examples/planner_enterprise_agent_v2
```

Useful loop:

- load the dataset from `examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/dataset/`
- run `examples.planner_enterprise_agent_v2.evals.metrics:policy_metric`
- inspect low-scoring rows and open prediction traces
- optionally run `examples.planner_enterprise_agent_v2.evals.metrics:fail_metric_demo` to verify failed criteria and divergence rendering

Why: Playground is the fastest way to debug failing rows before you rely on the committed CLI gate.

## Step 7 - Create evaluate spec (baseline mode)

Create `evaluate.spec.json`:

```json
{
  "project_root": "examples",
  "dataset_path": "planner_enterprise_agent_v2/evals/policy_compliance_v1/dataset/dataset.jsonl",
  "metric_spec": "examples.planner_enterprise_agent_v2.evals.metrics:policy_metric",
  "min_test_score": 0.8,
  "output_dir": "planner_enterprise_agent_v2/evals/policy_compliance_v1/artifacts",
  "agent_package": "planner_enterprise_agent_v2",
  "env_files": ["planner_enterprise_agent_v2/.env", "planner_enterprise_agent_v2/evals/policy_compliance_v1/.env.eval"]
}
```

Key meanings:

- `dataset_path`: pinned dataset generated by collect.
- `metric_spec`: metric callable import path.
- `min_test_score`: required minimum test score (threshold gate).
- `output_dir`: eval runtime output directory (no large eval workspace).
- `agent_package`: package used to build `run_one` when `run_one_spec` is omitted.

Note: `candidates_path` is optional. If omitted, eval runs baseline-only mode.

## Step 8 - Run baseline evaluation

```bash
uv run penguiflow eval evaluate --spec examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/evaluate.spec.json
```

Expected key output in baseline mode:

- `mode: baseline`
- `val_score: ...`
- `test_score: ...`
- `min_test_score: 0.8`
- `passed_threshold: True|False`

## Optional - Local optimization follow-up (do not commit)

Create `/tmp/pf-candidates.optimize.json`:

```json
[
  {
    "id": "prompt-route-guard-v1",
    "patches": {
      "planner.system_prompt_extra": "Always call triage_query first, then one route-specific tool, avoid unnecessary calls."
    }
  }
]
```

Create `/tmp/pf-evaluate.optimize.spec.json`:

```json
{
  "project_root": "examples",
  "dataset_path": "planner_enterprise_agent_v2/evals/policy_compliance_v1/dataset/dataset.jsonl",
  "candidates_path": "/tmp/pf-candidates.optimize.json",
  "metric_spec": "examples.planner_enterprise_agent_v2.evals.metrics:policy_metric",
  "min_test_score": 0.8,
  "output_dir": "planner_enterprise_agent_v2/evals/policy_compliance_v1/artifacts",
  "agent_package": "planner_enterprise_agent_v2",
  "env_files": ["planner_enterprise_agent_v2/.env", "planner_enterprise_agent_v2/evals/policy_compliance_v1/.env.eval"]
}
```

Run:

```bash
uv run penguiflow eval evaluate --spec /tmp/pf-evaluate.optimize.spec.json
```

Cleanup:

```bash
rm "/tmp/pf-candidates.optimize.json" "/tmp/pf-evaluate.optimize.spec.json"
```

## Commit policy

Commit:

- `query_suite.json`
- `collect.spec.json`
- `evaluate.spec.json`
- `dataset/dataset.jsonl`
- `dataset/manifest.json`
- metric code

Do not commit:

- `.env`
- `artifacts/`
- `/tmp` optimization files
