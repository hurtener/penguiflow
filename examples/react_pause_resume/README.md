# React Planner — Pause & Resume Demo

This example showcases the Phase B features of the PenguiFlow ReactPlanner:

- **Trajectory summarisation** keeps prompts within a small token budget.
- **Pause/Resume** uses the planner's `PlannerPause` contract to wait for a human approval step.
- **Developer hints** constrain the planner with ordering rules and disallowed tools.

The LLM interactions are driven by a deterministic stub so the example is runnable offline.

## Running

```bash
uv run python examples/react_pause_resume/main.py
```

The script prints the pause payload, simulates an approval, and resumes execution to completion.
