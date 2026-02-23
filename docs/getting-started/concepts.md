# Concepts

## What it is / when to use it

This page defines the *major moving parts* of PenguiFlow and how they fit together.

Use it when you want to:

- pick the right “message style” (payload-only vs envelopes),
- understand where reliability and backpressure come from,
- decide when you need the planner vs the core runtime.

If you want runnable code first, start with **[Quickstart](quickstart.md)**.

## Non-goals / boundaries

- This page is not a full API reference. It focuses on the mental model and the contracts that matter.
- It does not cover external tool configuration and auth (see **[Tooling](../planner/tooling.md)** and **[Tools & integrations](../tools/configuration.md)**).
- It does not prescribe an application architecture; “planner-only”, “runtime-only”, and “mixed” are all valid.

## Contract surface (the pieces you compose)

### Flow (runtime)

A **flow** is a directed graph of async nodes. It provides:

- `flow.run(...)` / `await flow.stop()` lifecycle
- `await flow.emit(...)` ingress (OpenSea)
- `await flow.fetch(...)` egress (Rookery)
- optional per-trace cancellation: `await flow.cancel(trace_id)`

Canonical: **[Flows & nodes](../core/flows-and-nodes.md)**.

### Node (runtime)

A **node** is an async function wrapped with metadata and policy:

- signature: `async def fn(message, ctx) -> Any`
- `NodePolicy`: timeouts, retries, validation
- edges: `a.to(b, c)` creates bounded queues (backpressure)

Canonical: **[Errors, retries, timeouts](../core/errors-retries-timeouts.md)** and **[Concurrency](../core/concurrency.md)**.

### Message (data + metadata)

PenguiFlow supports two message styles:

1. **Payload-only** (fastest start): nodes receive and return plain Pydantic models.
2. **Envelope-based** (recommended for production): nodes pass a `Message(payload=..., headers=..., trace_id=...)`, enabling:
   - per-trace correlation (`trace_id`),
   - per-trace cancellation,
   - deadlines,
   - streaming chunks that inherit routing metadata,
   - multi-tenant isolation via `Headers.tenant`.

Canonical: **[Messages & envelopes](../core/messages-and-envelopes.md)**.

### Context (in-node capabilities)

Every node receives a `ctx` that can:

- emit follow-up work (`await ctx.emit(...)`),
- emit streaming chunks (`await ctx.emit_chunk(...)`),
- access trace-scoped metadata used for observability and control (cancellation / deadlines).

Canonical: **[Streaming](../core/streaming.md)** and **[Cancellation](../core/cancellation.md)**.

### Planner (ReactPlanner)

The **planner** is an LLM-driven loop that selects tools and orchestrates their execution (including parallel calls) with:

- typed action schema and repair attempts,
- pause/resume (HITL) and session semantics,
- optional short-term memory,
- trajectory logging/observability hooks.

Canonical: **[ReactPlanner overview](../planner/overview.md)**.

## Operational defaults (safe starting points)

- Prefer **bounded queues** (`queue_maxsize` > 0) and treat queue depth as a first-class signal.
- Prefer **envelopes** (`Message`) when you need streaming, cancellation, deadlines, multi-tenant boundaries, or deterministic correlation.
- Keep `trace_id` **unique per request/session**; treat it as part of your authorization story (don’t let a user fetch/cancel another user’s trace).
- In production, add:
  - middlewares for structured `FlowEvent` logging, and/or
  - a `StateStore` for durability and event persistence.

## Failure modes & recovery

- **`fetch()` hangs**: nothing reached the Rookery sink (no egress node, egress returns `None`, or you didn’t call `run()`).
- **Cross-trace mixups**: you reused `trace_id` across concurrent requests (use trace-scoped fetch or unique trace ids).
- **Streaming “does nothing”**: you’re using payload-only messages; switch to envelope style and call `ctx.emit_chunk(parent=Message(...), ...)`.
- **Retries amplify side effects**: the node is not idempotent (use idempotency keys, or emit side effects only once).

Canonical runbooks live in the core pages:

- **[Flows & nodes](../core/flows-and-nodes.md)**
- **[Messages & envelopes](../core/messages-and-envelopes.md)**
- **[Errors, retries, timeouts](../core/errors-retries-timeouts.md)**

## Observability

The runtime emits structured `FlowEvent` for:

- node lifecycle (`node_start`, `node_success`, `node_error`, `node_timeout`, …),
- queue depth and pending/inflight counts (critical for backpressure debugging),
- trace cancellation and deadline skips.

Operationally:

- attach a middleware (e.g. `penguiflow.middlewares.log_flow_events`) early, and
- decide where you persist events (often via `StateStore`) before production rollout.

See **[Logging](../observability/logging.md)** and **[Telemetry patterns](../observability/telemetry-patterns.md)**.

## Security / multi-tenancy notes

- Always set `Headers.tenant` when you use envelopes, and keep tenant boundaries consistent across a trace.
- Don’t put secrets in payloads or message `meta` if you persist events/logs; prefer secret managers + redaction.
- Treat `trace_id` + `fetch(trace_id=...)` + `cancel(trace_id)` as sensitive control surfaces in applications.

## Runnable examples

Run a minimal flow:

```bash
uv run python examples/quickstart/flow.py
```

Run a streaming example (chunks + final answer):

```bash
uv run python examples/roadmap_status_updates/flow.py
```

If you’re building an LLM agent, start with the planner template:

```bash
uv run penguiflow new my-agent --template react
uv run penguiflow dev --project-root my-agent
```

## Troubleshooting checklist

- **Need cancellation/deadlines/streaming**: switch to envelopes (`Message`) and use `trace_id` per request.
- **Need parallel fan-out + join**: use `join_k` and ensure you pass `trace_id` (see **[Concurrency](../core/concurrency.md)**).
- **Need pause/resume / HITL**: use the planner (see **[Pause/resume](../planner/pause-resume-hitl.md)**).
- **Need tool integrations**: use ToolNode and configure auth (see **[Tools configuration](../tools/configuration.md)**).
