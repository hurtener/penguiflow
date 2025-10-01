# React planner parallel fan-out

This example demonstrates Phase D of the React planner: a single planner action
returns a `plan` array with two shard fetches plus a `join` descriptor. Each
branch is validated and executed concurrently, then the join node merges their
outputs using the auto-populated `expect`, `results`, and `parallel_*` metadata.

## Run it

```bash
uv run python examples/react_parallel/main.py
```

The script prints the final `PlannerFinish` payload along with the recorded
steps. The first step shows the parallel observation structure, including the
join output and branch metadata, while the final payload surfaces the merged
shard documents.
