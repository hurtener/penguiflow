# Enterprise agent learning-control-plane evaluation

This evaluates the real `planner_enterprise_agent_v2` twice for each held-out
policy-compliance request: once unchanged, and once with one temporary advisory
skill. The only deliberate difference is the skill context. The candidate is
kept in a temporary SQLite skill store and is never delivered to a customer.

The suite's `test` split is the promotion gate: `pc-003` (team communication)
and `pc-004` (deployment-report analysis). The `val` split remains available
for candidate drafting and iteration; do not use it as promotion evidence.

Set the same provider credentials and model settings that the enterprise agent
normally uses, then run this from the repository root. Use module mode so the
sibling adapter file does not shadow the `learning_control_plane` package:

```bash
uv run python -m examples.planner_enterprise_agent_v2.learning_control_plane_demo \
  --db-directory /private/tmp/penguiflow-enterprise-lcp \
  --advisory-skill "For document-analysis requests, verify the route and use the document workflow before answering."
```

The database directory must not already contain `control-plane.db`. This is an
offline evaluation and will make real model calls, so it may incur your normal
provider costs. It stops after the automatic gate. A passing job becomes
`ready_for_review`; a human must still approve it and authorize a specific
delivery scope before the existing activation adapter can create a customer
skill receipt.

## Publish real investigations to MLflow

Before the full loop can mine real evidence, configure the Planner to publish a
redacted investigation after terminal runs:

```bash
LCP_INVESTIGATION_PUBLISHING_ENABLED=true
LCP_MLFLOW_EXPERIMENT_ID="<EXPERIMENT_ID>"
LCP_MLFLOW_TRACKING_STORE_REF="databricks"
LCP_SCOPE_REF="tenant:<TENANT_ID>"
```

Add those values to the untracked Planner `.env`, together with the MLflow
tracking/authentication settings for the Databricks workspace that owns the
experiment. Run normal Planner requests to create evidence. Publication is
best-effort and redaction-first: a failed MLflow upload logs a warning but cannot
change the Planner result.

Verify one publication with a normal Planner request:

```bash
uv run python -m examples.planner_enterprise_agent_v2.main \
  --query "Create a concise plan for reviewing a deployment report."
```

Open the configured MLflow experiment afterward. A successful publication creates
a `learning.investigation.publish` trace with `learning.investigation.id` and
`learning.investigation.digest` tags, plus a
`learning.investigation_trajectory` attachment. The attachment is a redacted
investigation document; it is not the native Planner trajectory.

## Full governed local loop

`learning_control_plane_end_to_end.py` is the complete offline path for verified
MLflow investigations. It reads earlier investigations for mining, reserves newer
ones for held-out evaluation, runs Planner V2 baseline/candidate comparisons,
requires a review decision, and records a scoped local delivery receipt when the
reviewer approves.

The command requires a host integration module with three explicit members:

- `draft_skill(pattern)` receives only a safe `TracePattern`;
- `build_case(record)` looks up approved replay input and returns an
  `EvaluationCase`; and
- `outcome_provider.metrics_for(case, output)` returns approved real outcomes.

The included `learning_control_plane_local_host` is only a deterministic local
shape-checker. Replace it with a team-owned host module before reading real
customer source data or feedback.

```bash
uv run python -m examples.planner_enterprise_agent_v2.learning_control_plane_end_to_end \
  --experiment-id "<MLFLOW_EXPERIMENT_ID>" \
  --scope-ref "tenant:<TENANT_ID>" \
  --db-directory "/private/tmp/penguiflow-real-loop-$(date +%s)" \
  --host-integration examples.planner_enterprise_agent_v2.learning_control_plane_local_host \
  --reviewer-id "local-reviewer" \
  --review-decision approve
```

An approval flag does not bypass the gate: if the candidate fails offline
evaluation, no delivery authorization or receipt is created.
