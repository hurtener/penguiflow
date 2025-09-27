# ✅ Approved V2 Features

1. **Streaming support**

   * `StreamChunk` model + `emit_chunk()` helper.
   * Examples: LLM token stream, SSE/WebSocket sink.

2. **Per-trace cancellation API**

   * Cancel all tasks for a given `trace_id`.
   * Propagates cancel to running nodes/subflows.

3. **Deadlines & budgets**

   * Hop count, wall-clock deadlines, optional token budget.
   * Enforced in controller loop + runtime.

4. **Metadata propagation**

   * `meta: Dict[str, Any]` inside `Message`.
   * Pass debug, cost, decision info without polluting payload.

5. **Metrics / observability hooks**

   * `on_event(event: FlowEvent)` callback system.
   * Default logs, pluggable to MLflow/Prometheus.

6. **Flow visualizer**

   * Export flow structure to Mermaid or DOT.
   * Show nodes, floes, loops.

7. **Dynamic routing by policy**

   * Routers that consult runtime configs/policies (JSON/YAML/env).
   * Core provides mechanism; product repos provide policies.

8. **Traceable exceptions**

   * Wrap node errors as `FlowError(trace_id, node_name, original_exc)`.
   * Optionally emit as a message into the Rookery.

9. **Testing harness (FlowTestKit)**

   * Helpers to run flows synchronously in tests.
   * Assert node sequence, output payloads, retries, etc.

---

# 📦 PenguiFlow V2 — Phased Implementation Plan

Each phase is iterative, testable, and builds on v1.

---

## Phase 1 — Streaming Support

**Deliverables**

* `StreamChunk` Pydantic model (`stream_id`, `seq`, `text`, `done`, `meta`).
* `emit_chunk()` helper in `Context`.
* Example: `examples/streaming_llm/` (LLM token stream → SSE/WebSocket sink).

**Acceptance**

* Tests: ordered chunks, done flag, backpressure respected.
* SSE sink delivers tokens in correct order.

---

## Phase 2 — Per-Trace Cancellation

**Deliverables**

* Cancel API: `flow.cancel(trace_id)`
* Cancels tasks + queues associated with that trace.
* Propagates `CancelledError` upstream.

**Acceptance**

* Tests: flow stops mid-run when canceled, no stray tasks.
* Subflows also canceled.

_Status:_ Cancel API and runtime plumbing implemented with unit coverage; follow-up work will flesh out subflow propagation and richer metrics once Phase 2 expands.

---

## Phase 3 — Deadlines & Budgets

**Deliverables**

* Enforce `deadline_s` at runtime before scheduling work; extend controller hop budget handling and add optional token budgets.

**Acceptance**

* Tests: flow halts when hops exceeded or deadline passed.
* Messages carry deadline info end-to-end.

---

## Phase 4 — Metadata Propagation

**Deliverables**

* Add `meta: Dict[str, Any]` field to `Message`.
* Flows/nodes can read/write without affecting payload.

**Acceptance**

* Tests: metadata round-trips through multiple nodes.
* Example: add cost info in retriever, read in summarizer.

---

## Phase 5 — Metrics / Observability Hooks

**Deliverables**

* `on_event(event: FlowEvent)` callbacks built atop the existing middleware hook.
* `FlowEvent` includes: `trace_id, node_name, event_type, ts, latency_ms, queue_depth`.
* Default logs; pluggable to MLflow/Prometheus.

**Acceptance**

* Tests: callback fired on node start/finish/error/retry.
* Example: simple Prometheus counter updated.

---

## Phase 6 — Flow Visualizer

**Deliverables**

* Extend `viz.py` beyond the existing Mermaid exporter: add DOT output and annotate controller loops/subflows.

**Acceptance**

* Example: `examples/visualizer/` exporting Mermaid/DOT strings.
* Visual output matches adjacency (loops and subflows annotated).

---

## Phase 7 — Dynamic Routing by Policy

**Deliverables**

* Extend Router nodes to accept a `Policy` object.
* Policy can be config-driven (JSON/YAML/env).
* Flow doesn’t change; routing adapts at runtime.

**Acceptance**

* Tests: same flow routes differently under two policies.
* Example: `examples/routing_policy/`.

---

## Phase 8 — Traceable Exceptions

**Deliverables**

* `FlowError(trace_id, node_name, original_exc)` wrapper.
* Configurable: log-only or emit to Rookery.

**Acceptance**

* Tests: errors preserved with trace_id.
* Example: error flows through to Rookery for UI handling.

---

## Phase 9 — Testing Harness (FlowTestKit)

**Deliverables**

* Helpers:

  * `run_one(flow, input_msg) -> output_msg`
  * `assert_node_sequence(flow, trace_id, expected_nodes)`
  * `simulate_error(node)` for retry testing.
* Built-in into `tests/utils/flowtestkit.py`.

**Acceptance**

* Example: `examples/testkit_demo/`.
* Tests: developers can assert simple flows with <10 lines of code.


📌 **Outcome:**
By the end of v2, PenguiFlow will support **streaming, cancellation, guardrails, observability, dynamic routing, and easy testing** — while staying **tiny and async-only**.
