# PenguiFlow package overview

PenguiFlow is a lightweight asyncio runtime for orchestrating typed agent pipelines.
This document focuses on the internals that live inside the `penguiflow/` package so
contributors understand how the pieces fit together.

## Module tour

| Module | Purpose |
| --- | --- |
| `core.py` | Runtime graph builder, execution engine, retries/timeouts, controller loop semantics, and playbook helper. |
| `node.py` | `Node` wrapper and `NodePolicy` configuration (validation scope, timeout, retry/backoff). |
| `types.py` | Pydantic models for headers, messages, and controller/state artifacts (`WM`, `Thought`, `FinalAnswer`). |
| `registry.py` | `ModelRegistry` that caches `TypeAdapter`s for per-node validation. |
| `patterns.py` | Batteries-included helpers: `map_concurrent`, routers, and `join_k` aggregator. |
| `middlewares.py` | Async middleware hook contract for structured logging/observability sinks. |
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
* **Registry guardrails**: when `flow.run(registry=...)` is used, the runtime asserts that
  every validating node has a corresponding entry in the registry so configuration issues
  surface immediately.
* **Controller loops**: when a node emits a `Message` whose payload is a `WM`, the runtime
  increments hop counters, enforces budgets, and re-enqueues the message back to the
  controller. Returning a `FinalAnswer` short-circuits to Rookery.
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

## Middleware & logging

`_emit_event` emits structured dictionaries with fields such as
`{ts, event, node_name, node_id, trace_id, latency_ms, q_depth_in, q_depth_out, outgoing,
trace_pending, trace_inflight, ...}`. Any middleware added via `flow.add_middleware`
receives these events and can fan them out to logging frameworks, observability tools, or
metrics backends.

## Testing & examples

* Unit tests live under `tests/` and exercise every primitive (core runtime, registry,
  types, patterns, controller behavior, playbooks).
* Every major feature has a runnable example under `examples/`. Use `uv run python <path>`
  or `.venv/bin/python <path>` to execute them locally.

For a conceptual overview and getting-started guide, see the repository root `README.md`.
