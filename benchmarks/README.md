# Benchmarks

Microbenchmarks for common PenguiFlow patterns. Each script prints a simple throughput or
latency summary when executed.

Run them with `uv run` (or an active virtualenv):

```bash
uv run python benchmarks/fanout_join.py
uv run python benchmarks/retry_timeout.py
uv run python benchmarks/controller_playbook.py
```

Benchmarks:

- `fanout_join.py` — measures message throughput through a fan-out → join → summarize
  pipeline.
- `retry_timeout.py` — simulates per-message retries with timeouts to understand retry
  overhead.
- `controller_playbook.py` — evaluates controller nodes that invoke playbook subflows.

Tweak the script parameters or copy them into your service repo to track regressions over time.
