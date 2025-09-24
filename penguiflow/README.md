# penguiflow

Phase 1 runtime is now available:

* `Context` with `emit`, `fetch`, and `fetch_any` helpers backed by `asyncio` queues.
* `Floe` edges enforce backpressure via configurable `maxsize`.
* `PenguiFlow` orchestrates nodes, detects cycles, and gracefully stops workers.
* `ModelRegistry` caches Pydantic adapters for in/out validation.
* Structured logging + middleware hooks capture retries, timeouts, and queue depth stats.
* `patterns.py` bundles `map_concurrent`, `predicate_router`, `union_router`, and `join_k` helpers.
* `types.py` now exposes `PlanStep`, `Thought`, `WM`, and `FinalAnswer` for controller loops.
* `Node` wrappers carry stable ids and provide the `(message, ctx)` execution contract.

Run `examples/quickstart/flow.py` for a minimal pass-through demonstration.
