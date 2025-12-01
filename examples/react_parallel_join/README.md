# React Planner — Parallel Join Demo

Shows how to:

- Fan out a planner action across multiple providers
- Use **explicit join injection** (`join.inject`) instead of magic fields
- Surface branch failures while still returning partial results
- Keep tool-only callbacks in `tool_context`

## Run

```bash
uv run python examples/react_parallel_join/main.py
```

Expected: one branch succeeds, one fails; the join receives injected counts and failures, and the planner finishes with the merged payload.
