# PenguiFlow v2 → v2.1 — Agents Plan

## Vision

Evolve **penguiflow** into a lightweight, repo-agnostic orchestration library that:

* Keeps v2’s async core, type-safety, reliability (backpressure, retries, timeouts, graceful stop), routing, controller loops, subflows, and **streaming**.
* Adds **opt-in** hooks in v2.1 to support distributed execution and inter-agent calls without bloating the core:

  * **StateStore** (shared brain) — durable run history & correlation
  * **MessageBus** (shared nervous system) — distributed edges between nodes/workers
  * **RemoteTransport** (tiny seam) — optional HTTP/A2A client to call external agents

Remain **asyncio-only** and **Pydantic v2**. No heavy deps. Users bring their own backends.

## Non-Goals

* No built-in broker/DB/scheduler in core.
* No product/domain playbooks in core (keep in `examples/`).
* No breaking changes to existing v2 public APIs.
* No “exactly-once” delivery guarantees (at-least-once is acceptable for distributed adapters).

---

## Scope Recap — v2 (current)

Shipped or in progress per README:

* **Streaming support** (token/partial results with `Context.emit_chunk`)
* **Per-trace cancellation**
* **Deadlines & budgets**
* **Message metadata propagation**
* **Observability hooks** (FlowEvent)
* **Flow visualizer** (Mermaid/DOT)
* **Dynamic routing by policy**
* **Traceable exceptions** (FlowError)
* **Testing harness (FlowTestKit)**

These are the foundation for v2.1’s distributed & remote features.

---

## v2.1 Overview — Distributed & A2A-ready (Opt-in)

### Why now

* Client pressure to use a **main agent** that delegates to specialized agents.
* v2 already has **streaming, cancel, budgets** → perfect for remote tasks & partial delivery.
* We want distribution **without** forcing a broker or DB on everyone.

### What we add (opt-in)

1. **StateStore** protocol — durable events/history and remote correlation (trace_id ↔ remote context/task).
2. **MessageBus** protocol — pluggable queue for **Floe** edges across processes.
3. **RemoteTransport** protocol — tiny seam to call external agents (e.g., **A2A** JSON-RPC + SSE), shipped as an optional extra.

> Backwards-compat: If these are not provided, the runtime behaves exactly as today (in-proc queues, no persistence, no remote calls).

---

## Target Repo Layout (new files ⭐ for v2.1)

```
penguiflow/
  __init__.py
  core.py
  node.py
  types.py
  registry.py
  patterns.py
  middlewares.py
  viz.py
  errors.py
  testkit.py
  # v2.1 additions (opt-in protocols)
  ⭐ state.py         # StateStore Protocol + StoredEvent types
  ⭐ bus.py           # MessageBus Protocol
  ⭐ remote.py        # RemoteTransport Protocol + RemoteNode helper
examples/
  ...
  ⭐ distributed_redis_pg/   # MessageBus + StateStore adapters (example only)
  ⭐ a2a_orchestrator/       # PF main agent → specialized A2A agents (client only)
  ⭐ a2a_server/ (optional)  # Expose a PF flow as an A2A server (adapter extra)
```

---

## Backwards-Compatibility Strategy

* **Additive constructor params**:

  * `PenguiFlow(..., state_store: StateStore | None = None, message_bus: MessageBus | None = None)`
* Floe edges:

  * Use `message_bus` **only if provided**, else in-proc `asyncio.Queue` (identical behavior).
* Event persistence:

  * Call `StateStore.save_event(...)` **only if provided**.
* Remote:

  * New `RemoteTransport` + `RemoteNode` live in `remote.py`; not used unless imported.
* Optional extras:

  * A2A adapters live under `penguiflow_a2a` (extra): no new heavy deps in core.

---

## API Sketches (minimal surfaces)

```py
# penguiflow/state.py
from typing import Protocol

class StateStore(Protocol):
    async def save_event(self, trace_id: str, ts: float, kind: str, data: dict) -> None: ...
    async def load_history(self, trace_id: str) -> list[dict]: ...
    async def save_remote_binding(self, trace_id: str, context_id: str, task_id: str, agent_url: str) -> None: ...
```

```py
# penguiflow/bus.py
from typing import Protocol, Any

class MessageBus(Protocol):
    async def put(self, queue: str, message: Any) -> None: ...
    async def get(self, queue: str, timeout_s: float | None = None) -> Any: ...
    # optional: async def ack(self, delivery_token: str) -> None: ...
```

```py
# penguiflow/remote.py
from typing import Protocol, AsyncIterator, Any
from penguiflow.node import Node

class RemoteTransport(Protocol):
    async def send(self, agent_url: str, agent_card: dict, message: dict) -> dict: ...
    async def stream(self, agent_url: str, agent_card: dict, message: dict) -> AsyncIterator[dict]: ...
    async def cancel(self, agent_url: str, task_id: str) -> None: ...

def RemoteNode(name: str, *, transport: RemoteTransport, skill: str, agent_url: str, agent_card: dict | None = None) -> Node:
    """Factory: returns a Node that delegates to `transport` (stream or send)."""
```

---

## Phased Delivery (each phase shippable)

### **Phase A — Core Hooks for Distribution**

**Goal**
Enable distributed execution without changing in-proc behavior.

**Deliverables**

* `StateStore` + `MessageBus` protocols and wiring in `core.py`/Floe edges.
* Durable event emission (guarded by `if state_store`):

  * `node_start|end`, `message_emit|fetch`, `stream_chunk`, `trace_cancel_*`, `deadline_exceeded`.
* Edge naming for queues (stable `source→target` identifiers).

**Acceptance / Tests**

* Existing examples/tests pass unchanged (no hooks).
* **Unit**: in-memory `StateStore`/`MessageBus` stubs; ordering/backpressure preserved.
* **Integration (examples only)**: Redis `MessageBus`, Postgres `StateStore`; fan-out/fan-in; `join_k` across processes.
* **Fault-injection**: kill a worker mid-trace → restart → resume from `load_history`.

**Non-Goals**

* No built-in Redis/Postgres deps (adapters live in examples).

---

### **Phase B — Remote Calls (Agnostic) + A2A Client (Optional Extra)**

**Goal**
Let a flow call external agents (HTTP) and stream partials (SSE).

**Deliverables**

* `RemoteTransport` protocol + `RemoteNode` helper.
* `penguiflow_a2a` (optional extra):

  * A2A JSON-RPC client (`message/send`, `message/stream`) with SSE→chunk mapping.
  * Cancel propagation (`tasks/cancel`).
  * Minimal Agent Card parsing (decide stream vs send).

**Acceptance / Tests**

* **Unit**: RemoteNode happy path, backoff/timeouts; chunk ordering/backpressure.
* **Integration**: fake A2A server emitting SSE; stream→chunks; cancel unwinds remote & PF trace.
* **Resubscribe**: use `StateStore.save_remote_binding` to recover a stream by task/context IDs.

**Non-Goals**

* No A2A SDK in core; keep as an extra.
* No push-notification integration (SSE is enough in v2.1).

---

### **Phase C — PF as A2A Server (Optional)**

**Goal**
Make a PF flow callable by 3rd-party agents via A2A.

**Deliverables**

* `penguiflow_a2a.server` adapter (FastAPI):

  * Expose `message/send`, `message/stream`, `tasks/*` for a selected PF flow.
  * Generate a minimal **Agent Card** (skills, streaming support).
* Auth hooks (header injection) and simple rate limit options.

**Acceptance / Tests**

* Contract/compat tests for send/stream/cancel.
* Example: `examples/a2a_server/` with a simple flow.

**Non-Goals**

* No opinionated auth stack; leave OIDC/SSO to integrators.

---

### **Phase D — Observability & Ops Polish**

**Goal**
End-to-end traceability across remote and distributed paths.

**Deliverables**

* Structured logs include PF `trace_id` and remote `contextId`/`taskId`.
* Counters for remote latency, bytes, error rates, cancel latency.
* Dev CLI: `pf inspect <trace_id>` dumps event history (using `StateStore`).

**Acceptance / Tests**

* Logs/metrics show correlated IDs across a remote call.

**Non-Goals**

* No full observability stack in core; keep sinks as examples/middleware.

---

## Examples (to ship with v2.1)

1. `examples/distributed_redis_pg/`

   * Two workers, Redis `MessageBus`, Postgres `StateStore`, fan-out → `join_k` across processes.

2. `examples/a2a_orchestrator/`

   * PF main agent calls two specialized A2A agents via `RemoteTransport(A2A)`; SSE streaming to client; cancel support.

3. `examples/a2a_server/` *(optional)*

   * Wrap a PF flow as an A2A server with a generated Agent Card.

Each includes a short README and runnable script.

---

## Risks & Mitigations

| Risk                    | Mitigation                                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| Hidden behavior change  | Hooks are **opt-in**; default path remains in-proc queues & no persistence.                      |
| Heavy dependencies      | A2A & backend adapters are extras; core deps unchanged.                                          |
| Serialization surprises | Only distributed edges serialize; document “payload must be JSON-serializable” when using a bus. |
| Backpressure under SSE  | Reuse v2 ordered chunking & bounded queues; test slow-consumer scenarios.                        |
| ID drift across systems | Persist `trace_id ↔ contextId/taskId` in `StateStore`; include in logs/metrics.                  |

---

## Definition of Done (per phase)

* **Code**: protocols + wiring + guarded calls (no behavioral drift for in-proc).
* **Tests**: unit + integration + fault injection where relevant.
* **Docs**: README sections (“Going Distributed”, “Calling Remote Agents”), API docstrings.
* **Examples**: runnable end-to-end.
* **CI**: no new mandatory services; example adapters exercised in an optional job.

---

## Developer Notes

* Python ≥ 3.11, asyncio-only; Pydantic v2.
* Keep new modules **small and independent** (no cross-imports that drag extras into core).
* Prefer **protocols** over abstract base classes; keep signatures minimal.
* Ensure existing examples/tests run untouched on v2.1.

---

## Migration Guide (Zero-touch Upgrade)

* Upgrade to **v2.1** → no changes required; all existing flows continue to run in-proc.
* **Going distributed?** Pass `message_bus` (+ optionally `state_store`) to `PenguiFlow(...)`.
* **Calling remote agents (A2A)?** Use `RemoteNode` and install `penguiflow[a2a]`.

**Examples:**

*In-proc (unchanged):*

```py
flow = create(triage.to(retrieve), retrieve.to(pack))
await flow.run(registry=registry)
```

*Distributed:*

```py
flow = create(triage.to(retrieve), retrieve.to(pack))
flow.run(registry=registry, message_bus=RedisMessageBus(...), state_store=PostgresStateStore(...))
```

*Remote call (A2A extra):*

```py
from penguiflow.remote import RemoteNode
from penguiflow_a2a import A2AClientTransport

search = RemoteNode(
  name="search",
  transport=A2AClientTransport(base_url="https://search-agent"),
  skill="SearchAgent.find",
  agent_url="https://search-agent",
)

flow = create(triage.to(search), search.to(pack))
```

---

## Open Questions (to track)

* Do we provide a tiny `InMemoryStateStore` and `InProcBus` for tests/examples?
* Should we add an optional `ack()` to `MessageBus` now or later?
* Do we need a first-party “retry policy” for remote calls distinct from `NodePolicy`?

---

*End of AGENTS.md*


### Acceptance

* All phases green in CI; examples runnable.
* No breaking changes to v1 public API (only additive).
* README updated with what’s new + quickstart links.

## Developer Workflow

### Setup

uv sync
uv run ruff check penguiflow
uv run mypy penguiflow
uv run pytest --cov=penguiflow --cov-report=term-missing

## Local Testing Tips

Run a single test: uv run pytest tests/test_core.py -k "test_name"
Stop on first failure: uv run pytest -x
Async tests: handled automatically by pytest-asyncio
Lint fix: uv run ruff check penguiflow --fix

### Coverage Policy
Target: ≥85% line coverage (hard minimum in CI).
Every new feature must include at least one negative/error-path test.
Blind spots to prioritize:
- middlewares.py → add direct hook tests
- viz.py → cover DOT/Mermaid outputs
- types.py → expand beyond StreamChunk

Coverage reports generated in CI (--cov-report=xml) and uploaded to Codecov/Coveralls.
Badges in README track trends over time.

### CI/CD Policy
Matrix:
- Python: 3.11, 3.12, 3.13
- OS: Ubuntu 

Checks enforced before merge:
- Ruff (lint)
- Mypy (types)
- Pytest with coverage (≥85%)

Artifacts:
- Store .coverage.xml
- Badges: Add CI status + coverage badge in README.

Optional:
- Performance benchmarks (pytest-benchmark)
- Upload coverage to Codecov/Coveralls

## Examples Policy

- Each example must be runnable directly:

    uv run python examples/<name>/flow.py

- Include a short README.md inside the example folder.
- Example must cover at least one integration test scenario.
- Examples should demonstrate real usage but remain domain-agnostic.


