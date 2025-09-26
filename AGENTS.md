# PenguiFlow v2 — Agents Plan

## Vision

Evolve **penguiflow** into a lightweight, repo-agnostic library that adds:

* **Streaming support** (token/partial results),
* **Per-trace cancellation** (stop a single run cleanly),
* **Deadlines & budgets** (bounded loops),
* **Message metadata propagation** (debug/cost/context),
* **Metrics/observability hooks** (pluggable events),
* **Flow visualizer** (Mermaid/DOT export),
* **Dynamic routing by policy** (config-driven decisions),
* **Traceable exceptions** (uniform error surface),
* **Testing harness (FlowTestKit)** (easy flow unit tests),

…while keeping v1’s async core, type-safety, reliability (backpressure, retries, timeouts, graceful stop), routing, controller loops, and subflows.
No heavy deps; remain **asyncio-only** and **Pydantic v2**.

## Non-Goals

* No external brokers (Kafka/Redis) in v2 (adapters may be explored in v2.1+).
* No UI/console beyond logs and example sinks (e.g., SSE/WebSocket).
* No job scheduler, DB, or persistence features in core.
* No domain playbooks in library (keep in `examples/`).

## Repo Layout (target; additions for v2 in ⭐)

```
penguiflow/
  __init__.py
  core.py              # Context, Floe, PenguiFlow (runtime)
  node.py              # Node, NodePolicy
  types.py             # Message, Headers, ⭐ StreamChunk
  registry.py          # ModelRegistry (TypeAdapters per node)
  patterns.py          # router(s), join_k, map_concurrent
  middlewares.py       # hooks: logging, mlflow (pluggable)
  viz.py               # Mermaid exporter (existing) + ⭐ DOT/annotations
  metrics.py           # ⭐ FlowEvent + on_event callbacks
  errors.py            # ⭐ FlowError (traceable exceptions)
  testkit.py           # ⭐ FlowTestKit (helpers for tests)
  README.md
examples/
  quickstart/
  routing_predicate/
  routing_union/
  fanout_join/
  controller_multihop/
  playbook_retrieval/          # example only
  ⭐ streaming_llm/             # StreamChunk + SSE/WebSocket sink
  ⭐ routing_policy/            # config-driven router
  ⭐ visualizer/                # export Mermaid/DOT demo
  ⭐ testkit_demo/              # FlowTestKit usage
tests/
  test_core.py
  test_types.py
  test_registry.py
  test_patterns.py
  test_controller.py
  ⭐ test_streaming.py
  ⭐ test_cancel.py
  ⭐ test_budgets.py
  ⭐ test_metadata.py
  ⭐ test_metrics.py
  ⭐ test_viz.py
  ⭐ test_routing_policy.py
  ⭐ test_errors.py
  ⭐ test_testkit.py
pyproject.toml
```

---

## Phased Delivery (each phase is shippable)

### Phase 1 — Streaming Support

**Goal:** First-class token/partial streaming via messages.

**Deliverables**

* `types.StreamChunk` (`stream_id`, `seq`, `text`, `done`, `meta: dict`).
* `Context.emit_chunk(stream_id, seq, text, done=False, meta=None)` sugar.
* Example: `examples/streaming_llm/` with an LLM mock → SSE/WebSocket sink.

**Acceptance**

* `test_streaming.py`: preserves order by `seq`, handles `done=True`, respects queue backpressure.
* Example streams to a client without blocking other nodes.

---

### Phase 2 — Per-Trace Cancellation

**Goal:** Cancel a single in-flight run (identified by `trace_id`) without stopping the flow.

**Deliverables**

* `PenguiFlow.cancel(trace_id: str) -> bool` (idempotent).
* Propagate `CancelledError` through affected tasks, drain/selectively drop per-trace queues.
* Emit metric events for cancel start/finish.

**Acceptance**

* `test_cancel.py`: running run is canceled mid-stream; no orphan tasks; other runs unaffected.

---

### Phase 3 — Deadlines & Budgets

**Goal:** Enforce hop and wall-clock limits across loops and subflows.

**Deliverables**

* Extend `Message`/`Headers` with `deadline_s` (wall clock) and continue v1’s `budget_hops` in WM/controller.
* Runtime checks deadline before scheduling next work; controller enforces hop budget.

**Acceptance**

* `test_budgets.py`: run halts when deadline exceeded or hop budget reached; emits final event/log.

---

### Phase 4 — Metadata Propagation

**Goal:** Pass debug/cost/context safely across nodes.

**Deliverables**

* Add `meta: Dict[str, Any]` to `Message`.
* Utilities to set/merge meta (non-PII, deterministic where possible).

**Acceptance**

* `test_metadata.py`: meta survives fan-out/fan-in and subflow boundaries; merge semantics defined (last-write-wins or merge keys).

---

### Phase 5 — Metrics / Observability Hooks

**Goal:** Pluggable, low-overhead events for logs/MLflow/Prometheus.

**Deliverables**

* `metrics.FlowEvent` (node_start, node_end, node_error, retry, emit, fetch, stream_chunk, cancel_begin, cancel_end).
* `on_event: Callable[[FlowEvent], None]` layered on the existing middleware hook to ease migration.
* Default logger sink; example Prometheus counter.

**Acceptance**

* `test_metrics.py`: events fire with `{trace_id, node_name, ts, latency_ms, queue_depth}`; example sink increments counters.

---

### Phase 6 — Flow Visualizer

**Goal:** Human-readable graph export for docs/debugging.

**Deliverables**

* `viz.to_mermaid(flow)` (existing) and new `viz.to_dot(flow)` with loop/subflow annotations.
* Example outputs saved as `.md`/`.dot`.

**Acceptance**

* `test_viz.py`: topology matches adjacency; node names, loops, and subflows represented consistently across formats.

---

### Phase 7 — Dynamic Routing by Policy

**Goal:** Config-driven decisions without code changes.

**Deliverables**

* Router wrapper that accepts a `Policy` (dict/config object) at runtime.
* Policy access via env/JSON/YAML injection (loader not in core; pass object).
* Example: tenant A → FAISS path, tenant B → hybrid path.

**Acceptance**

* `test_routing_policy.py`: same flow routes differently under two policies; deterministic selection.

---

### Phase 8 — Traceable Exceptions

**Goal:** Uniform, debuggable errors with run context.

**Deliverables**

* `errors.FlowError(trace_id, node_name, code, message, original_exc)` and mapping from node failures/timeouts.
* Option: emit terminal error message to Rookery in addition to logs.

**Acceptance**

* `test_errors.py`: errors carry `trace_id`/`node_name` and stable codes; retries map to final error if exhausted.

---

### Phase 9 — Testing Harness (FlowTestKit)

**Goal:** Make flow unit tests tiny and readable.

**Deliverables**

* `testkit.run_one(flow, registry, input_msg) -> output_msg`
* `testkit.assert_node_sequence(trace_id, expected_nodes)`
* `testkit.simulate_error(node_name, code)` for retry tests.

**Acceptance**

* `test_testkit.py`: developers write <10 lines to test linear & branching flows.

---

## Coding Standards

* Python ≥ 3.12, **Pydantic ≥ 2**.
* **Async-only** (`asyncio`); no threads in core.
* `pytest` for tests; target ~95% coverage.
* `ruff` for lint; `mypy` in loose mode where dynamic typing is needed.
* Every feature ships with **unit tests + example** and README updates.

## Performance Notes

* Reuse `TypeAdapter`s (registry cache).
* For hot token paths set `NodePolicy(validate="in")` or `"none"` on output.
* Use bounded queues (`maxsize`) and size them via config.
* Keep metrics hooks non-blocking (queue or lightweight logging).

## Examples to Add

* `streaming_llm`: token streaming → SSE sink.
* `routing_policy`: same flow, different policy routes.
* `visualizer`: dump Mermaid/DOT into README snippets.
* `testkit_demo`: how to write compact flow tests.

---

### Acceptance (v2 overall)

* All phases green in CI; examples runnable.
* No breaking changes to v1 public API (only additive).
* README updated with what’s new + quickstart links.
