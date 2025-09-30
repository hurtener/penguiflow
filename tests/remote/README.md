# Remote call tests

This suite documents `tests/test_remote.py`, which exercises PenguiFlow's
phase 2 remote-call surface:

* `RemoteNode` delegates unary requests through a `RemoteTransport` and
  persists remote bindings via the configured `StateStore`.
* Streaming transports feed partial output through `Context.emit_chunk`
  while still returning a terminal payload to downstream nodes.
* Cancelling a trace mirrors to the remote transport via the
  `RemoteTransport.cancel` hook so remote agents can unwind their work.

The tests rely on in-memory fakes and run entirely within the async runtime
used by the rest of the test suite.

See also `tests/observability_ops/test_remote_observability.py` for the Phase 4
metrics coverage that inspects the structured `FlowEvent` telemetry emitted by
remote nodes.
