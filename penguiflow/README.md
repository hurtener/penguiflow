# penguiflow

Phase 1 runtime is now available:

* `Context` with `emit`, `fetch`, and `fetch_any` helpers backed by `asyncio` queues.
* `Floe` edges enforce backpressure via configurable `maxsize`.
* `PenguiFlow` orchestrates nodes, detects cycles, and gracefully stops workers.
* `ModelRegistry` caches Pydantic adapters for in/out validation.
* `Node` wrappers carry stable ids and provide the `(message, ctx)` execution contract.

Run `examples/quickstart/flow.py` for a minimal pass-through demonstration.
