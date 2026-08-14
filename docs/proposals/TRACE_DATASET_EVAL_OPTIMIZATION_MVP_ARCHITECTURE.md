# MVP Architecture: Trace Curation, Eval Diagnosis, and Planner Optimization

Status: Draft
Owner: PenguiFlow core
Related RFC: `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md`

## Purpose

Define the MVP architecture for a complete quality loop with explicit outputs and no hidden steps:

1. curate traces in Playground,
2. export versioned datasets,
3. run analyze-only diagnostics,
4. run harness eval + manual patch sweeps,
5. emit one deployable patch bundle and verify holdout behavior.

This MVP architecture inherits normative contracts from the RFC and narrows them to one mandatory workload for fast validation.

## Implemented Commands (Current)

```bash
uv run penguiflow eval collect --spec path/to/collect.spec.json
uv run penguiflow eval evaluate --spec path/to/evaluate.spec.json
```

Current default behavior note:

- `eval evaluate`: in-memory execution with text summary output
- no default workspace artifact emission for dataset evaluation
- optional single JSON report file via `report_path`
- `eval collect`: minimal dataset export (`dataset.jsonl` + `manifest.json`)

## Objectives

- Prove trace-to-dataset conversion using RFC schemas.
- Prove GEPA-compatible metrics in both analyze-only and harness modes.
- Prove patch-point optimization improves validation metrics.
- Prove winner is deployable as a single `PatchBundleV1` artifact.

## Key Clarifications

- Policy-quality metrics should rely on execution behavior, not only final answer text.
- Pinned datasets are required for meaningful evaluation and comparison over time.
- Default runtime behavior remains minimal: collect writes dataset artifacts, evaluate runs in memory.
- Richer optimizer and artifact workflows are future phases in this proposal, not current usage.

## Terminology (Avoid Confusion)

- `trace.jsonl` contains raw trace evidence (`trajectory`, planner events, flow events). This is the canonical source.
- `gold_trace` in `view.*.jsonl` carries the unfiltered raw trace row used as gold/reference.
- `__pf.trace_id` links a view row back to the full raw trace in `trace.jsonl`.
- Canonical naming for MVP: `gold_trace`.

## Context Stability Requirements (Critical)

To make optimization comparisons valid, baseline and candidate runs must hold execution context constant.

Required in `trace.jsonl`:

- `inputs.llm_context` (full context used by the planner run)
- `inputs.tool_context` (full tool/runtime context used by the planner run)

Required run discipline:

- Baseline and candidate evaluations reuse the same `gold_trace.inputs.llm_context` and `gold_trace.inputs.tool_context`.
- The only intended variable across candidates is the patch surface (for example planner prompt/config patch points).

Validation rule:

- Evaluation reports should include or derive a context-stability check (for example context hash equality) to ensure fairness.

## Dataset-Metric Coupling (Explicit)

Dataset collections are not metric-neutral in practice. A collection/view is valid only relative to a metric contract and evaluation unit.

Required manifest linkage:

- `dataset_view.unit` (`trace`, `react_step`, `node_fragment`)
- `metric.id`
- `metric.version`
- `metric.requirements` (signals expected in `gold_trace` and `pred_trace`)

Runner behavior:

- Validate metric requirements before execution.
- Fail fast if required signals are missing (for example no `pred_trace.steps` for a policy metric).

## DSPy Session Parity Model (Critical)

`dspy-session` linearizes into `dspy.Example` and marks explicit model inputs via `.with_inputs(...)`. Non-input fields remain available to metric as gold/reference data.

For PenguiFlow, MVP should follow the same pattern:

- model inputs: only fields the planner should see (for example `question`),
- gold/reference fields: `gold_trace`, optional labels, and provenance fields,
- runtime evidence: `pred_trace` captured from execution for deterministic scoring.

This means `gold_trace` is not a parallel dataset; it is a non-input gold field in the same example row, equivalent to how `answer` is a non-input label in standard DSPy usage.

## API Consumer Model (Metric Definition)

For real project usage, metrics must be provided by API consumers, not hardcoded in PenguiFlow internals.

MVP contract:

- project defines metric callable in project code (for example `examples/<project>/evals/metrics.py`),
- CLI/eval runner loads metric by import path,
- metric must support GEPA-compatible signature:

```python
def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    ...
```

This keeps core dependency-light while preserving optimizer compatibility.
Core may provide metric templates in docs/scaffolding, but should not embed workload metric logic.

## Non-goals

- Full experimentation platform.
- Mandatory DSPy or MLflow dependencies in core.
- Optimization beyond explicit patch points.
- Fully automated global search in v1.

## Mandatory Reference Workload

Use `examples/planner_enterprise_agent_v2` for MVP architecture validation.

Execution modes:

```bash
uv run penguiflow dev --project-root examples/planner_enterprise_agent_v2
uv run python -m examples.planner_enterprise_agent_v2.main --query "Analyze the latest deployment logs"
```

Generalization to other examples is follow-up after this MVP slice is green.

## RFC Alignment Rules

- Canonical schemas: `TraceExampleV1`, `DatasetViewV1`, `PatchBundleV1`.
- Curation mode priority: Playground-first tagging (`dataset:*`, `split:*`).
- Export behavior: capability-detected, profile-driven redaction, deterministic truncation.
- Eval modes: analyze-only and harness eval are distinct and both required.
- Optional sinks (MLflow) do not replace canonical JSON artifacts.

## Explicit Constraints (Current Codebase)

- `penguiflow dev` uses in-memory store by default and has no `--state-store` option.
- `penguiflow-admin` currently has `history` and `replay` only.
- `Trajectory.metadata` supports tags, but no tag-index capability exists yet.
- MVP flow must work with fallback curation by explicit trace IDs when UI tagging is unavailable.

## Patch Points in Scope (MVP)

- `planner.system_prompt_extra`
- `planner.planning_hints`
- `tool.<tool_name>.desc`

## MVP Architecture Scope

### Phase 1A: Curation + Export + Analyze-only

- Playground tagging workflow for dataset/split labels.
- Export `trace.jsonl` + `manifest.json` with safe profiles.
- Run deterministic diagnostics from exported traces.

### Phase 1B: Dataset Views

- Define and persist `DatasetViewV1` config in manifest.
- Export `view.jsonl` with provenance linkage to trace rows.
- Add policy-aware view rows and explicit input selection (`input_fields`).
- Add configurable linearization units:
  - `trace` (full-run example),
  - `react_step` (decision-step examples),
  - `node_fragment` (selected-node history fragments).

### Phase 2: Harness Eval + Manual Sweep + Bundle

- Run examples end-to-end for dataset rows.
- Score with GEPA-compatible metric function.
- Sweep patch candidates and emit best `PatchBundleV1`.

## Export Source Priority and Selection Rules

### Capability/source priority (per trace)

1. `SupportsTrajectories.get_trajectory(trace_id, session_id)`
2. `SupportsPlannerEvents.list_planner_events(trace_id)`
3. `StateStore.load_history(trace_id)`

If `session_id` is missing for trajectory retrieval, exporter must either:

- require `--session-id`, or
- emit partial `TraceExampleV1` row and record omission in provenance.

### Selection mode priority

1. by tags (`--tag dataset:... --tag split:...`) as primary path,
2. by `session_id` + scan/filter fallback,
3. by explicit trace IDs.

## Safe Export Profiles (Required)

- `internal_safe` (current implementation): includes `inputs.llm_context` and `inputs.tool_context` for optimization fairness; stricter shareable profile remains follow-up.
- `debug`: include bounded/truncated debug payloads.
- `shareable`: hash/remove sensitive text fields; keep structural metrics.
- `poc_full_context` (local-only): include `inputs.llm_context` and `inputs.tool_context` for context-stable optimization experiments.

Redaction hook contract:

```python
def redact_trace_example(row: dict) -> dict:
    ...
```

## End-to-End Procedure (No Tacit Steps)

### Step 0 - Freeze benchmark inputs

Objective: lock workload config and query suite before baseline.

Inputs: workload config, fixed query list.

Required output: `query_suite.json`.

Done criteria: no query/split mutation after baseline run starts.

### Step 1 - Curate traces

Objective: select representative traces and assign split labels.

Inputs: Playground traces and/or explicit trace-id list.

Required output: `curation.json`.

Done criteria: minimum 30 traces with `train`/`val`/`test` assignment.

### Step 2 - Export dataset

Objective: create reproducible RFC-aligned dataset artifacts.

Inputs: curation selection + StateStore.

Required outputs: `trace.jsonl`, `manifest.json`, `view.val.jsonl`, `view.test.jsonl`, and `bundle/dataset.jsonl`.

Done criteria: schema/version checks pass and provenance is complete.

### Step 3 - Analyze-only diagnosis

Objective: quantify baseline from exported traces without reruns.

Inputs: `trace.jsonl`.

Required outputs: `metrics.jsonl`, `report.analyze.json`.

Done criteria: report includes success rate, tool failure rate, p50/p95 latency, cost summary when available.

### Step 4 - Harness eval baseline + candidates

Objective: evaluate baseline and patch candidates on val split.

Inputs: `view.val.jsonl`, metric function, candidate patches.

Required outputs: `predictions.baseline.jsonl`, `results.baseline.jsonl`, `predictions.candidates.jsonl`, `results.candidates.jsonl`, `report.harness.json`.

Done criteria: at least one candidate improves primary metric on val split.

### Step 5 - Holdout verification + bundle

Objective: verify no regression on test split and lock deployable winner.

Inputs: winning candidate from Step 4.

Required outputs: `results.test.jsonl`, `report.test.json`, `best.patchbundle.json`.

Done criteria: winner meets holdout gate and bundle passes schema/compat checks.

## Artifact Contracts (Objective and Required Content)

Current enterprise PoC artifacts live under `examples/planner_enterprise_agent_v2/artifacts/eval/<run_id>/`.

### `query_suite.json`

Objective: freeze benchmark workload and split plan.

Required content:

- `suite_id`
- `workload` (`planner_enterprise_agent_v2`)
- `queries[]` with `query_id`, `text`, `split`
- `created_at`

### `collect.spec.json`

Objective: define reproducible trace collection and dataset export.

Required content:

- `project_root`
- `query_suite_path`
- `session_id`
- `dataset_tag`
- `output_dir`

Optional content:

- `agent_package`
- `env_files`
- `state_store_spec`

### `evaluate.spec.json`

Objective: define reproducible evaluation over a pinned dataset.

Required content:

- `dataset_path`
- `metric_spec`
- `output_dir`

Optional content:

- `candidates_path` (omit for baseline-only mode)
- `project_root`
- `agent_package`
- `run_one_spec`
- `env_files`
- `report_path`
- `min_test_score`

### `trace.jsonl` (`TraceExampleV1`)

Objective: canonical, safe-by-default trace record dataset.

Required row content (minimum):

- `schema_version`
- `trace_id`
- `inputs.llm_context`
- `inputs.tool_context`
- `outputs.status`
- `redaction.profile`
- `provenance`

Recommended row content:

- `session_id`, `query`, `trajectory`, `trajectory_full`, `events`, `derived`, `artifacts`

Important implementation note:

- `trajectory.steps` is a summary count.
- Step-level records for offline metric introspection live under `trajectory_full.steps`.

### `manifest.json`

Objective: reproducibility, provenance, and view configuration.

Required content:

- `dataset_id`
- `schema_versions`
- `workload`
- `source` (selection criteria and capability/source priority used)
- `counts` by split
- `redaction_policy`
- `export_command`
- `dataset_view` config (when `view.*.jsonl` exists)

### `view.val.jsonl` and `view.test.jsonl` (`DatasetViewV1` rows)

Objective: flat signature-shaped rows for eval/optimization.

Required row content:

- mapped signature fields
- non-input gold/reference fields required by metric (`gold_trace`)
- `__pf.trace_id`
- `__pf.session_id` (when known)
- `__pf.schema_version`

Guideline:

- `gold_trace` must include `inputs.llm_context` and `inputs.tool_context` so reruns can preserve context while varying only patch points.
- `gold_trace.trajectory_full.steps` should be treated as the canonical source for step-dependent metrics in bundle/offline evaluation.

Optional row content for linearized views:

- `__pf.unit` (`trace`, `react_step`, `node_fragment`)
- `__pf.step_index` (for step/fragment units)
- `__pf.node_name` (for fragment units)

### `metrics.jsonl` (analyze-only)

Objective: per-trace deterministic diagnostics from exports.

Required row content:

- `trace_id`
- `split`
- metric fields (success/tool failures/latency/cost where available)

### `report.analyze.json`

Objective: aggregate analyze-only baseline.

Required content:

- aggregate success rate
- tool failure rate
- p50/p95 latency summaries
- cost summary when available

### `predictions.*.jsonl` (harness)

Objective: persist run outputs prior to scoring.

Required row content:

- `run_id`
- `mode` (`baseline` or `candidate`)
- `example_id`
- `pred`
- optional `pred_trace`

### `results.*.jsonl` (harness)

Objective: per-example scoring evidence.

Required row content:

- `run_id`
- `mode`
- `example_id`
- `split`
- `score`
- optional `feedback`
- failure diagnostics when scoring fails

### `report.harness.json` and `report.test.json`

Objective: candidate ranking and go/no-go decision.

Required content:

- `primary_metric`
- aggregate scores by split
- candidate ranking (for harness runs)
- winner selection and tie-break explanation

Required context-stability fields (per mode in harness report):

- `context_match_rate`
- `context_stability_pass`
- `context_comparable_count`

### `best.patchbundle.json` (`PatchBundleV1`)

Objective: single deployable tuning artifact.

Required content:

- `schema_version` (`PatchBundleV1`)
- `patches`
- `compat.planner`
- `compat.tool_catalog_hash`
- `provenance` (`optimizer`, `dataset`, `metric`, `score`, `created_at`)

## Final Output Extraction Rules (v1)

Priority:

1. from trajectory final-response step payload,
2. else from known finish metadata fields,
3. else `outputs.final = null` and set `outputs.status` to `unknown` unless error signals indicate `error`.

Pause/resume handling:

- v1 treats resumed traces as separate records.
- link to prior trace with optional linkage metadata when available.

## Metric Contract (GEPA-Compatible)

```python
def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    ...
```

Allowed returns:

- `float`
- `{"score": float, "feedback": str}`

Same metric function must run unchanged in PenguiFlow harness and optional DSPy/GEPA templates.

### Policy Metric Guidance (MVP)

For policy optimization, the default metric should be deterministic and trajectory-aware:

- input: `gold_trace` from dataset row,
- evidence: `pred_trace` (steps/events),
- output: aggregate score + check-level feedback.

Recommended deterministic checks:

- `route_policy_pass`
- `triage_first`
- `workflow_order_pass`
- `allowed_tools_only`
- `budget_pass`
- `terminal_pass`

Check-level outputs should be persisted in `feedback` to make optimization decisions auditable.

## Linearization Strategy (Least Resistance)

Use one canonical source and progressive projections:

1. Keep `trace.jsonl` as canonical raw source (no lossy transforms).
2. Build `view.*.jsonl` as projection artifacts for each optimization objective.
3. Start with `unit=trace` + unfiltered `gold_trace` (already enough for policy-compliance optimization).
4. Add `unit=react_step` next, because it provides denser optimization signal with minimal additional plumbing.
5. Add `unit=node_fragment` last, scoped to a small allowlist of nodes to avoid combinatorial complexity.

This path keeps compatibility with DSPy patterns without introducing mandatory DSPy runtime dependencies in core.

## Lowest-Resistance Implementation Path

### Step A (now): stabilize policy-aware trace-level optimization

- Keep current Slice 0 flow and metric contract.
- Require `view.*.jsonl` rows to include `gold_trace` + `__pf.trace_id`.
- Require `gold_trace.inputs.llm_context` and `gold_trace.inputs.tool_context` for fair candidate comparisons.
- Require `report.harness.json` context-stability fields to pass before candidate comparison is accepted.
- Add harder benchmark prompts to avoid val-score saturation.
- Add explicit acceptance thresholds per check (not only mean score), prioritizing `allowed_tools_only` and `route_policy_pass`.

### Step B: add linearization config in exporter/view builder

- Add `linearization.unit` (`trace|react_step|node_fragment`) in manifest.
- Add `input_fields` declaration in manifest to mirror DSPy `.with_inputs(...)` behavior.

### Step C: add `react_step` view projection

- One row per planner decision step with compact local history window.
- Gold fields include expected next-node policy constraints.

### Step D: add optional DSPy adapter layer (template-only)

- Convert `view.*.jsonl` rows to `dspy.Example`.
- Apply `.with_inputs(*input_fields)`.
- Reuse the exact same metric function.

## Optimization Protocol

- Baseline first.
- Candidate sweeps on `val` split.
- Winner by highest primary metric.
- Tie-breakers: lower tool failure rate, then lower p95 latency, then smaller patch surface.
- Holdout gate on `test` split before sign-off.

## Optional Integrations

### MLflow (optional sink)

MLflow can log metrics/params/artifacts, but canonical artifacts remain local JSON files (`trace.jsonl`, `view.jsonl`, `results`, `reports`, `PatchBundleV1`).

## Success Criteria (Hard)

- >=30 curated traces from `planner_enterprise_agent_v2`.
- RFC-aligned dataset artifacts emitted with complete manifest provenance.
- Reproducible analyze-only baseline.
- At least one val-improving candidate in harness eval.
- No holdout regression for winner.
- Valid `PatchBundleV1` with compatibility hash.

## First-Step Quick PoC (Execution Slice 0)

This section defines the first practical execution step for rapid validation before full MVP coverage.

### Goal

Prove that we can improve planner quality with a minimal loop in days, not weeks.

### Scope (intentionally small)

- Workload: `examples/planner_enterprise_agent_v2` only.
- Curation: query-list collection and/or tag-scoped selection from StateStore.
- Export: `trace.jsonl` plus minimal `manifest.json`.
- View: `view.val.jsonl`/`view.test.jsonl` with `gold_trace` as non-input gold field.
- Metric: deterministic policy-compliance metric (GEPA-compatible signature, trajectory-aware scoring).
- Optimization surface: `planner.system_prompt_extra` only.
- Sweep size: baseline + up to 3 candidates.
- Validation: one holdout check before declaring winner.

### Required outputs

- `query_suite.json` (10-15 fixed prompts with `val` and `test` splits)
- `collect.spec.json`
- `evaluate.spec.json`
- `dataset.jsonl`
- `manifest.json`
- optional single report JSON via `report_path`

### Deferred from Slice 0

- Playground tagging UX (API tag selection exists; UI polish deferred)
- `react_step` and `node_fragment` linearization modes
- multi-surface patch optimization (`planning_hints`, `tool.<tool_name>.desc`)
- MLflow sink integration
- strict compatibility-hash enforcement policies

### Exit criteria for Slice 0

- Reproducible baseline from fixed inputs.
- `results.*.jsonl` feedback includes deterministic check-level policy evidence.
- At least one candidate improves validation metric over baseline on non-saturated prompts.
- Winner does not regress on holdout split.
- Winner is persisted as `best.patchbundle.json` and is reviewable.

## Open Decisions

- CLI placement (`penguiflow-admin` extension vs `penguiflow eval ...`).
- Canonical gold extraction details for mixed workflow outputs in this workload.
- Initial fixed query suite size for stable signal.
- Failure policy on compatibility mismatch (`warn` vs `fail`).
- Default history window for `react_step` and `node_fragment` linearization.
- Whether to persist full `pred_trace` in `predictions.*.jsonl` by default or behind a debug profile.

## Roadmap Follow-ups (Standalone Enhancements)

These enhancements are designed as independent increments after the current
collect/evaluate MVP path is stable.

1. **Dataset projection contract parity with DSPy examples**
   - Add explicit `input_fields` in manifest and/or row-level input/label partitioning.
   - Preserve `gold_trace` as non-input reference evidence.

2. **Spec-level metric failure policy**
   - Add `on_metric_error: "zero" | "raise"` in `evaluate.spec.json`.
   - Optional extension: `max_metric_errors` for bounded tolerance.

3. **Strict trajectory semantics for linearized units**
   - Add `strict_trajectory` option for `react_step` / `node_fragment` exports.
   - Stop projection at first invalid/missing row when strict mode is enabled.

4. **Canonical/source artifact split**
   - Keep `trace.jsonl` canonical.
   - Keep `view.*.jsonl` / `dataset.jsonl` as projection layers with stable
     `trace_id` links and optional trace embedding.

5. **Context fairness surfaced in evaluate summaries**
   - Standardize `context_match_rate`, `context_stability_pass`, and
     `context_comparable_count` across harness and minimal evaluate outputs.

6. **Metric compatibility metadata enforcement**
   - Persist and validate `metric.id`, `metric.version`, and
     `metric.requirements` at dataset/report boundaries.

## Implementation Order

1. Freeze `query_suite.json` contract.
2. Add curation contract + export contract (`TraceExampleV1`, `manifest`).
3. Add analyze-only runner outputs (`metrics.jsonl`, `report.analyze.json`).
4. Add view projection and harness eval outputs.
5. Add patch sweep + winner selection + `PatchBundleV1` emission.
6. Add optional sinks/templates (MLflow, DSPy) without changing core contracts.
