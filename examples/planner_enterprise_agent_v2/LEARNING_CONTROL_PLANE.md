# Enterprise agent learning-control-plane evaluation

This evaluates the real `planner_enterprise_agent_v2` twice for each held-out
policy-compliance request: once unchanged, and once with one temporary advisory
skill. The only deliberate difference is the skill context. The candidate is
kept in a temporary SQLite skill store and is never delivered to a customer.

The suite's `test` split is the promotion gate: `pc-003` (team communication)
and `pc-004` (deployment-report analysis). The `val` split remains available
for candidate drafting and iteration; do not use it as promotion evidence.

Set the same provider credentials and model settings that the enterprise agent
normally uses, then run this from the repository root:

```bash
uv run python examples/planner_enterprise_agent_v2/learning_control_plane_demo.py \
  --db-directory /private/tmp/penguiflow-enterprise-lcp \
  --advisory-skill "For document-analysis requests, verify the route and use the document workflow before answering."
```

The database directory must not already contain `control-plane.db`. This is an
offline evaluation and will make real model calls, so it may incur your normal
provider costs. It stops after the automatic gate. A passing job becomes
`ready_for_review`; a human must still approve it and authorize a specific
delivery scope before the existing activation adapter can create a customer
skill receipt.
