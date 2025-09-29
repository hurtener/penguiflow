# Trace Cancellation with Subflows

Demonstrates cancelling a single trace while it is executing a nested playbook.
The controller uses `Context.call_playbook` so the subflow receives the same
cancellation signal, drops in-flight messages, and emits richer metrics.

## Run it

```bash
uv run python examples/trace_cancel/flow.py
```

Expected output (timestamps omitted):

```
subflow: started for trace <id>
subflow: received cancellation for trace <id>
trace_cancel_start ... pending=1 inflight=1
trace_cancel_finish ... pending=0 inflight=0
safe result: safe
```

The middleware prints the cancellation metrics payload so you can inspect
`trace_pending`, `trace_inflight`, and queue depth readings.
