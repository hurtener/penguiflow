# Distribution hook tests

This folder documents the coverage goals for `tests/test_distribution_hooks.py`.
The suite verifies the first phase of PenguiFlow's distributed architecture:

* `StateStore` integration persists every runtime `FlowEvent` and exposes
  `PenguiFlow.load_history()`.
* Failures while persisting state are logged but do not crash the flow.
* `MessageBus` envelopes are published for both `emit` and `emit_nowait`
  pathways so remote workers can subscribe to edges.
* Publish failures are surfaced via structured log events instead of raising.

Each scenario runs an actual flow to exercise the async runtime end-to-end.
