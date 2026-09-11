# Planner Enterprise V2 offline evaluation

The Planner Enterprise V2 adapter lives in
`examples/planner_enterprise_agent_v2/learning_control_plane.py`. It creates a
fresh agent and temporary skill store for every baseline/candidate case. It never
uses the serving planner instance or its skill state.

`PlannerEnterpriseV2EvaluationRunner` measures `latency_ms` around each isolated
call and retains the serialised trajectory. `EnterpriseOutcomeScorer` then adds:

- `policy_compliance`, from the existing deterministic Planner policy suite;
- `tool_error_rate`, from failed trajectory steps divided by total steps;
- `cost_usd`, only if the model/provider placed a finite cost in trajectory
  metadata; and
- host-supplied outcomes such as `task_success` and
  `customer_correction_rate` through `EnterpriseOutcomeProvider`.

The host loads real held-out input with `load_real_held_out_dataset`. Its
`case_loader` receives only a safe `TraceLearningRecord`, resolves the approved
source-run input in the host's own system, and returns an `EvaluationCase`.
Lineage fields are set by the adapter. Neither raw input nor customer outcome
data enters candidate mining or LLM skill drafting.

The existing local demo now evaluates policy compliance, latency, and tool-error
rate. A production host can pass an `EnterpriseOutcomeProvider` to add its
approved task and correction outcomes.

Run the demo as a module so its sibling adapter file cannot shadow the
`learning_control_plane` package:

```bash
uv run python -m examples.planner_enterprise_agent_v2.learning_control_plane_demo \
  --db-directory /private/tmp/penguiflow-planner-eval \
  --advisory-skill "Check dependencies before creating the plan."
```
