# Learning Control Plane local demo

This runs the complete MVP loop with local SQLite files and deterministic mock
evaluation—no MLflow account, model API key, or PenguiFlow serving process needed.

```bash
uv run python examples/learning_control_plane_local_demo/flow.py \
  --db-directory /private/tmp/penguiflow-lcp-demo
```

The directory must not already contain `control-plane.db` or `skills.db`. The demo
mines a candidate from earlier safe summaries, reserves later summaries as held-out
cases, evaluates and gates the candidate, records human approval, delivers a
tenant-scoped learned skill, and saves its activation receipt.
