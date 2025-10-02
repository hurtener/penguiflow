# PenguiFlow Core Behavior Spec

PenguiFlow's reliability hinges on a handful of invariants that govern
message ordering, backpressure, streaming, cancellation, deadlines, and
aggregation.  This single-page spec captures those guarantees, explains
why they matter for production flows, and links each invariant to the
canonical regression tests that cover it.

## Ordering & Backpressure

* **Invariant** – OpenSea→node queues apply strict FIFO ordering while
  enforcing bounded capacity.  When a floe is full, emitters block until
  downstream capacity frees up, preventing drops or reordering.
* **Operational impact** – Guarantees deterministic replay and protects
  downstream services from overload spikes.
* **Regression coverage** –
  [`tests/test_core.py::test_backpressure_blocks_when_queue_full`](../tests/test_core.py) verifies hop-level blocking semantics and that
  tasks resume once capacity is restored.
  [`tests/test_streaming.py::test_emit_chunk_respects_backpressure_when_rookery_full`](../tests/test_streaming.py)
  exercises the same invariant for streaming chunks routed through the
  Rookery egress.

## Streaming Sequencing

* **Invariant** – `Context.emit_chunk` (and `PenguiFlow.emit_chunk`)
  assigns monotonically increasing `seq` numbers per `stream_id`, resets
  counters after `done=True`, and preserves ordering under arbitrary
  interleavings across traces.
* **Operational impact** – Clients can stitch streamed tokens together
  without gaps or duplicates, even when multiple conversations are
  multiplexed through a single flow.
* **Regression coverage** –
  [`tests/test_streaming.py::test_stream_chunks_emit_in_order_and_reset_after_done`](../tests/test_streaming.py)
  validates deterministic sequencing within a trace, while the new
  property-based suite
  [`tests/test_property_based.py::test_stream_sequences_are_monotonic`](../tests/test_property_based.py)
  fuzzes random interleavings across concurrent traces.

## Cancel Semantics

* **Invariant** – `PenguiFlow.cancel(trace_id)` propagates cooperative
  cancellation to every in-flight node task bound to the trace and emits
  a single lifecycle for `trace_cancel_start` → `trace_cancel_finish`.
* **Operational impact** – Prevents orphaned tasks and resource leaks when
  users abandon conversations or supervisors reclaim budgets.
* **Regression coverage** –
  [`tests/test_cancel.py::test_cancel_trace_stops_inflight_run_without_affecting_others`](../tests/test_cancel.py)
  asserts cooperative cancellation and ensures unrelated traces keep
  running, while
  [`tests/test_cancel.py::test_cancel_propagates_to_subflow`](../tests/test_cancel.py)
  guards the subflow propagation contract.

## Deadlines & Budgets

* **Invariant** – Expired `Message.deadline_s` values short-circuit node
  execution before user code runs, finalizing the trace gracefully and
  emitting `deadline_skip` telemetry.
* **Operational impact** – Upholds latency SLOs and keeps stale work from
  consuming limited concurrency slots.
* **Regression coverage** –
  [`tests/test_budgets.py::test_deadline_prevents_node_execution`](../tests/test_budgets.py)
  ensures nodes never run with expired deadlines, and
  [`tests/test_budgets.py::test_deadline_finalizes_when_first_node_has_successors`](../tests/test_budgets.py)
  covers the multi-hop fan-out case.

## Fan-in with `join_k`

* **Invariant** – `join_k` buffers exactly `k` messages per `trace_id`
  and emits a single aggregated payload preserving arrival order once the
  quota is met.  Buckets are cleared immediately after emission to avoid
  memory growth.
* **Operational impact** – Enables predictable fan-in for workflows such
  as parallel tool calls or distributed retrieval shards.
* **Regression coverage** –
  [`tests/test_patterns.py::test_join_k_emits_after_k_messages`](../tests/test_patterns.py)
  locks the baseline behavior, while the new property test
  [`tests/test_property_based.py::test_join_k_handles_randomized_fanout`](../tests/test_property_based.py)
  fuzzes randomized arrival orders under tight queue backpressure.
