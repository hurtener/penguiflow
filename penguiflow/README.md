# PenguiFlow package overview

PenguiFlow is a lightweight asyncio runtime for orchestrating typed agent pipelines.
This document focuses on the internals that live inside the `penguiflow/` package so
contributors understand how the pieces fit together.

## Module tour

| Module | Purpose |
| --- | --- |
| `core.py` | Runtime graph builder, execution engine, retries/timeouts, controller loop semantics, and playbook helper. |
| `node.py` | `Node` wrapper and `NodePolicy` configuration (validation scope, timeout, retry/backoff). |
| `types.py` | Pydantic models for headers, messages (with `Message.meta` bag), and controller/state artifacts (`WM`, `Thought`, `FinalAnswer`). |
| `registry.py` | `ModelRegistry` that caches `TypeAdapter`s for per-node validation. |
| `patterns.py` | Batteries-included helpers: `map_concurrent`, routers, and `join_k` aggregator. |
| `middlewares.py` | Async middleware hook contract consuming structured `FlowEvent` objects. |
| `metrics.py` | `FlowEvent` model plus helpers for deriving metrics/tags. |
| `viz.py` | Mermaid and DOT exporters with loop/subflow annotations. |
| `__init__.py` | Public surface that re-exports the main primitives for consumers. |

## Key runtime behaviors

* **Graph construction**: `PenguiFlow` builds contexts for every node plus the synthetic
  `OPEN_SEA`/`ROOKERY` endpoints. `Floe` edges expose bounded `asyncio.Queue`s to enforce
  backpressure.
* **Cycle detection**: flows are validated via topological sort; controller nodes can opt
  into self-cycles with `allow_cycle=True`, or the entire flow can set
  `allow_cycles=True` to bypass checks.
* **Worker lifecycle**: `run()` spins up one task per node. `stop()` cancels and awaits
  every worker to guarantee a clean shutdown.
* **Reliability envelope**: each message dispatch goes through `_execute_with_reliability`
  which applies validation, timeout, retry with exponential backoff, structured logging,
  and middleware hooks.
* **Metadata propagation**: every `Message` includes a mutable `meta` dictionary. The
  runtime clones it when emitting streaming chunks, preserving debugging or billing
  breadcrumbs across retries, controller loops, and playbook subflows.
* **Deadline enforcement**: nodes never start executing stale work; `Message.deadline_s`
  is checked prior to invocation and expired traces are converted to
  `FinalAnswer("Deadline exceeded")` without running the user coroutine.
* **Registry guardrails**: when `flow.run(registry=...)` is used, the runtime asserts that
  every validating node has a corresponding entry in the registry so configuration issues
  surface immediately.
* **Controller loops**: when a node emits a `Message` whose payload is a `WM`, the runtime
  increments hop counters, enforces hop/token budgets (`budget_hops` / `budget_tokens`) when
  they are set, checks deadlines, and re-enqueues the message back to the controller.
  Returning a `FinalAnswer` short-circuits to Rookery or, if guardrails fire, the runtime
  creates a `FinalAnswer` with the appropriate exhaustion message.
* **Playbooks**: `Context.call_playbook` accepts a factory that returns a `(PenguiFlow,
  registry)` pair, runs it, emits the parent message (preserving headers + trace ID),
  mirrors cancellation signals to the subflow, and returns the first payload produced by
  the subflow.

## Validation & typing

1. Register your Pydantic models with `ModelRegistry.register(node_name, in_model, out_model)`.
2. Provide the registry when starting the flow (`flow.run(registry=registry)`).
3. `Node.invoke()` uses cached `TypeAdapter`s to validate inputs/outputs according to the
   node's `policy.validate` setting (`both`, `in`, `out`, or `none`).

## Patterns cheat sheet

* `map_concurrent` — run an async worker over a list of inputs with bounded concurrency.
* `predicate_router` — route to successor nodes based on simple boolean predicates.
* `union_router` — enforce discriminated unions and send each variant to its matching node.
* `join_k` — buffer `k` messages per trace id, then emit a combined batch downstream.

Each helper is a regular node and can be combined with hand-authored nodes seamlessly.

## Visualization helpers

`viz.flow_to_mermaid(flow, direction="TD")` and `viz.flow_to_dot(flow, rankdir="TB")`
inspect the runtime graph and emit Mermaid or Graphviz definitions. Nodes tagged with
`allow_cycle=True` (controller loops) are highlighted automatically, and edges touching
`OPEN_SEA`/`ROOKERY` are annotated as ingress/egress so subflow boundaries stand out in
diagrams. The `examples/visualizer/` folder contains a runnable script that exports both
formats for docs.

## Middleware & logging

`_emit_event` now materialises a `FlowEvent` dataclass containing fields such as
`{ts, event_type, node_name, node_id, trace_id, latency_ms, q_depth_in, q_depth_out,
outgoing, trace_pending, trace_inflight, ...}`. Middleware added via
`flow.add_middleware` receives these objects, can inspect `.to_payload()` for logging,
and `.metric_samples()` / `.tag_values()` for metrics sinks like MLflow.

## Testing & examples

* Unit tests live under `tests/` and exercise every primitive (core runtime, registry,
  types, patterns, controller behavior, playbooks).
* Every major feature has a runnable example under `examples/`. Use `uv run python <path>`
  or `.venv/bin/python <path>` to execute them locally.

For a conceptual overview and getting-started guide, see the repository root `README.md`.
