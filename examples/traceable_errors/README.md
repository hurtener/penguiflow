# Traceable errors example

This example demonstrates Phase 8 of the PenguiFlow roadmap: **traceable exceptions**.

Running the script will:

1. Build a single-node flow whose node always raises a `RuntimeError`.
2. Enable `emit_errors_to_rookery=True` so terminal failures are surfaced as `FlowError`
   objects instead of timing out at fetch time.
3. Emit a message and print the resulting error payload. The output includes the
   trace id, node name, error code, and metadata so you can correlate the failure
   with metrics or downstream systems.

```bash
uv run python examples/traceable_errors/flow.py
```

You should see a log similar to:

```
flow error captured: NODE_EXCEPTION Node 'flaky' raised RuntimeError: external service unavailable trace= 3f6e3f...
```

The example intentionally keeps the flow tiny so the focus stays on how `FlowError`
objects can be inspected programmatically or forwarded to observability tooling.
