# Reliability & Middleware

Exercises PenguiFlow's retry, timeout, and middleware capabilities.

## Flow behaviour

- The `flaky` node has a `NodePolicy` with `timeout_s=0.05` and `max_retries=2`.
- First attempt sleeps for 0.2s, exceeding the timeout. PenguiFlow cancels the task,
  records a timeout event, and retries.
- Second attempt raises a transient exception, triggering another retry with backoff.
- Third attempt succeeds, producing the final payload.

A middleware is attached via `flow.add_middleware`, printing structured event metadata for
each step of the retry lifecycle.

## Run it

```bash
uv run python examples/reliability_middleware/flow.py
```

Sample output (timestamps trimmed):

```
mw:node_start:attempt=0 latency=0.0
mw:node_timeout:attempt=0 latency=50.3
mw:node_retry:attempt=1 latency=None
mw:node_start:attempt=1 latency=0.0
mw:node_error:attempt=1 latency=0.1
mw:node_retry:attempt=2 latency=None
mw:node_start:attempt=2 latency=0.0
mw:node_success:attempt=2 latency=0.0
result payload: success on attempt 3
```

Use this as a template to plug in real logging/MLflow middleware or to tune retry/backoff
policies per node.
