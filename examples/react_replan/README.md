# ReactPlanner — Adaptive Re-Planning

This example demonstrates **Phase C** of the PenguiFlow ReactPlanner: when a tool
fails, the planner receives structured failure feedback and proposes a fallback
plan that still honours hop budgets.

```bash
uv run python examples/react_replan/main.py
```

The script runs entirely offline with a deterministic stubbed LLM.
