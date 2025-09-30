# PenguiFlow v2.1 — Distributed & A2A-ready (Lightweight)

*A phased plan for hooks, features, tests, and examples*

## Why now (context)

* `dev-v2` already delivers **streaming chunks**, **per-trace cancellation**, and **deadlines/budgets** — perfect primitives for remote work, partial results, and abortable long tasks. ([GitHub][1])
* The A2A protocol standardizes **JSON-RPC over HTTP**, **SSE streaming**, and **Agent Cards** for capability discovery — exactly what we need to interoperate with specialized agents from a main orchestrator. ([GitHub][2])

## Goals (v2.1)

* Add two **pluggable hooks** to enable distribution with zero heavy deps in core:

  1. `StateStore` (durable run history & correlation)
  2. `MessageBus` (distributed edges between nodes/workers)
* Add a **tiny optional seam** to call external agents:
  3) `RemoteTransport` (e.g., A2A over HTTP/SSE)
* Ship **examples + tests** so teams can either:
  (a) scale flows across workers, (b) federate with remote A2A agents, or (c) do both.

## Non-Goals (v2.1)

* Not a scheduler, broker, or DB.
* No “exactly-once” guarantees.
* No hard dependency on A2A or any backend. (All integrations are optional extras.)

---

## Phase 1 — Core Hooks for Distribution (State & Bus)

### Deliverables

1. **StateStore Protocol** (`penguiflow/state.py`)

   * `save_event(trace_id, ts, kind, data)`
   * `load_history(trace_id)`
   * (A2A-prep) `save_remote_binding(trace_id, context_id, task_id, agent_url)` for correlation
     *Purpose:* durable event journal for recovery/resume; bind PF `trace_id` to remote **A2A context/task**. (A2A’s task lifecycle and resubscribe rely on these IDs.) ([a2a-protocol.org][3])

2. **MessageBus Protocol** (`penguiflow/bus.py`)

   * `put(queue_name, message)`
   * `get(queue_name, timeout_s: float|None=None)`
   * (Nice-to-have) `ack(delivery_token)` for at-least-once backends
     *Purpose:* replace process-local `asyncio.Queue` edges (Floe) with named distributed queues.

3. **Runtime wiring**

   * `PenguiFlow(..., state_store=None, message_bus=None)`
   * Floes use `message_bus` if provided; otherwise in-proc queues.
   * Emit durable events for: `node_start|end`, `message_emit|fetch`, `stream_chunk`, `trace_cancel_*`, `deadline_exceeded`.

### Success criteria

* A single flow can run across 2+ processes with **fan-out/fan-in** and **join_k** behavior preserved.
* Crash during a trace → restart a worker → **resume** based on `load_history`.

### Tests

* **Unit**: In-memory `StateStore` & `MessageBus` stubs; ordering, backpressure, timeout behavior.
* **Integration**: Redis `MessageBus`, Postgres `StateStore` (example adapters), fan-out/fan-in, join_k across processes.
* **Faults**: kill a worker mid-trace; ensure no message loss (at-least-once), resume from history.

### Examples

* `examples/distributed_redis_pg/`:

  * RedisBus + PostgresStore; two workers each hosting a subset of nodes; CLI args `--nodes=a,b`.

### Non-goals

* No baked-in Redis/Postgres deps; adapters live in example project(s).

---

## Phase 2 — Remote Calls (Agnostic) + A2A Client (Optional Extra)

### Deliverables

1. **RemoteTransport Protocol** (`penguiflow/remote.py`)

   * `send(agent_url, agent_card, message) -> Message|Artifact`
   * `stream(agent_url, agent_card, message) -> AsyncIterator[Event]` (map SSE → PF chunks)
   * `cancel(agent_url, task_id)`
     *Purpose:* a tiny seam for **HTTP-based agent calls** (A2A, or any RPC).

2. **RemoteNode helper**

   * Looks like any `Node`, but delegates via `RemoteTransport`.
   * Streams map to `Context.emit_chunk(...)` (v2 capability). ([GitHub][1])

3. **A2A client adapter** (`penguiflow[a2a]`)

   * **JSON-RPC** `message/send` + `message/stream` (SSE) with **Agent Card** parsing. ([a2a-protocol.org][3])
   * Map **tasks/cancel** to `PenguiFlow.cancel(trace_id)` propagation. ([a2a-protocol.org][3])

### Success criteria

* From a PF flow, call a remote A2A agent, **receive streaming partials** via SSE, and forward them downstream / to Rookery. (A2A mandates SSE for `message/stream`.) ([a2a-protocol.org][3])
* Cancelling a PF trace cancels the remote A2A task.

### Tests

* **Unit**: RemoteNode happy path, backoff, timeouts; chunk-ordering/backpressure.
* **Integration**: fake A2A server (SSE), stream → chunks, cancel → `tasks/cancel`, resubscribe using stored `task_id`.

### Examples

* `examples/a2a_orchestrator/`: main PF agent delegates to two specialized A2A agents; streams partials; supports cancel.

### Non-goals

* No A2A SDK in core; adapter shipped as optional extra.
* No push-notification setup yet (SSE is enough for v2.1).

---

## Phase 3 — A2A Server Adapter (Optional, but powerful)

### Deliverables

* **FastAPI A2A Server adapter** (`penguiflow[a2a-server]`)

  * Expose `message/send`, `message/stream`, `tasks/*` for a PF “main agent”.
  * **Generate Agent Card** from flow skills/capabilities (names, streaming support). ([GitHub][2])
* Map PF outputs to A2A **Artifact** (final result) and status events.
* Auth hooks (bearer/API key) pass-through.

### Success criteria

* Other A2A clients can discover the main PF agent via the **Agent Card** and call it over JSON-RPC, stream results, and cancel tasks. ([GitHub][2])

### Tests

* Contract tests: `message/send`, `message/stream`, `tasks/get|cancel`.
* (Optional) Run A2A **compliance tests** against the adapter.

### Examples

* `examples/a2a_server/`: wrap a PF flow as an A2A agent with a generated Agent Card.

### Non-goals

* Not a universal auth story; leave SSO/OIDC to integrators.

---

## Phase 4 — Observability & Ops polish

### Deliverables

* Structured logs & metrics for remote calls (latency, bytes, error rates, cancel latency).
* Correlate PF `trace_id` ↔ A2A `contextId`/`taskId` in logs & events (via `StateStore`). ([a2a-protocol.org][3])
* Minimal admin CLI: inspect trace history, replay last N events (dev-only).

### Success criteria

* SRE can follow a cross-agent trace end-to-end with IDs.

---

## API Sketches (minimal surfaces)

```py
# penguiflow/state.py
class StateStore(Protocol):
    async def save_event(self, trace_id: str, ts: float, kind: str, data: dict): ...
    async def load_history(self, trace_id: str) -> list[dict]: ...
    async def save_remote_binding(self, trace_id: str, context_id: str, task_id: str, agent_url: str): ...

# penguiflow/bus.py
class MessageBus(Protocol):
    async def put(self, queue: str, message: Any) -> None: ...
    async def get(self, queue: str, timeout_s: float | None = None) -> Any: ...
    # optional: async def ack(self, token: str) -> None: ...

# penguiflow/remote.py
class RemoteTransport(Protocol):
    async def send(self, agent_url: str, agent_card: dict, message: dict) -> dict: ...
    async def stream(self, agent_url: str, agent_card: dict, message: dict) -> AsyncIterator[dict]: ...
    async def cancel(self, agent_url: str, task_id: str) -> None: ...
```

---

## Testing Matrix

| Area              | Unit                          | Integration                 | Fault injection               |
| ----------------- | ----------------------------- | --------------------------- | ----------------------------- |
| StateStore        | event shape, history order    | Postgres adapter            | restart mid-trace; resume     |
| MessageBus        | put/get ordering              | Redis adapter, multi-worker | network delay; dup deliveries |
| Streaming         | chunk seq/order, backpressure | SSE end-to-end              | slow consumer; drop chunk     |
| Cancel            | raise in-flight, unwind       | A2A `tasks/cancel`          | cancel storms; lost SSE       |
| Budgets/Deadlines | token/hop/wall limits         | long remote tasks           | deadline races                |

(Streaming/cancel/budgets are already documented & tested in `dev-v2`, so we extend coverage across distributed/remote paths.) ([GitHub][1])

---

## Example Apps (expected)

1. **Distributed Flow** (`examples/distributed_redis_pg/`):

   * Redis `MessageBus`, Postgres `StateStore`, two workers via CLI; join_k across machines.

2. **Orchestrator → A2A Agents** (`examples/a2a_orchestrator/`):

   * Main PF agent calls 2 specialty A2A agents via `RemoteTransport(A2A)`, streams partials, supports cancel/resubscribe.
   * Uses A2A **message/stream** (SSE) and **tasks/cancel**. ([a2a-protocol.org][3])

3. **PF as A2A Server** (`examples/a2a_server/`) *(Phase 3)*:

   * FastAPI wrapper, **Agent Card** generation, JSON-RPC endpoints. ([GitHub][2])

---

## Risks & Mitigations

* **Adapter sprawl** → keep core pure; adapters/examples live outside core; publish `extras` (`penguiflow[a2a]`).
* **Backpressure under SSE** → reuse v2 per-trace capacity & ordered chunking in `emit_chunk`. ([GitHub][1])
* **Inconsistent IDs** → persist `trace_id` ↔ `contextId/taskId` in `StateStore`. ([a2a-protocol.org][3])
* **Security** → treat remote data as untrusted; validate with Pydantic; let users wire auth headers per A2A guidance. ([GitHub][2])

---

## Documentation ToC (what to add)

* **“Going Distributed”**: how to plug `MessageBus` & `StateStore`; sample Redis/Postgres adapters.
* **“Calling Remote Agents”**: `RemoteTransport` concepts; A2A client adapter setup; streaming & cancel.
* **“Exposing a PF Agent via A2A”** *(Phase 3)*: server adapter, Agent Card, auth notes.
* **Troubleshooting**: chunk ordering, retries, cancel races, ID correlation.

---

## Adoption Path

* Start with Phase 1 to distribute current flows (no external agents).
* Add Phase 2 to federate with existing A2A agents (no broker required).
* Add Phase 3 if you need your PF orchestrator to be discoverable/callable by other A2A clients.

---

### References (key protocol & repo facts)

* PenguiFlow `dev-v2` features: streaming, cancel, budgets (README “Implemented v2 Features”). ([GitHub][1])
* A2A **key features**: JSON-RPC over HTTP, Agent Cards, SSE streaming. ([GitHub][2])
* A2A **JSON-RPC transport** & method naming (e.g., `message/send`, `tasks/get`). ([a2a-protocol.org][3])
* A2A **SSE streaming** (`message/stream`, `tasks/resubscribe`). ([a2a-protocol.org][3])
* A2A **message/stream** event payloads & updates. ([a2a-protocol.org][3])
* A2A **tasks/cancel** semantics. ([a2a-protocol.org][3])
* A2A **Agent Card** (discovery & capabilities). ([a2a-protocol.org][3])

