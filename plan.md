# PenguiFlow: From OoFlow to a Type-Safe, Concurrent, Agent Orchestrator

## North Star (what “done” means)

* A tiny, dependency-light Python library (**penguiflow**) that lets us define **typed, concurrent agent pipelines**.
* Repo-agnostic core. Product repos only define **Pydantic models + node funcs** and register them at startup.
* Supports **dynamic multi-hop agent loops**, **runtime-instantiated subflows (playbooks)**, **routing (A/B/C)**, and **bounded concurrency**.
* First-class **observability** (structured logs, optional MLflow hooks), **backpressure**, **graceful shutdown**, and **retries**.

---

# Delivery Roadmap (6 Phases)

Each phase ends with working code, docs, and tests. If needed, phases can be split across PRs.

## Phase 0 — Repo Bootstrap (Day 0.5)

**Goals**

* Create the `penguiflow` library skeleton. No behavior yet.

**Deliverables**

```
penguiflow/
  __init__.py
  core.py          # runtime types + barebones Flow class (temporary placeholder)
  node.py
  types.py
  registry.py
  patterns.py
  middlewares.py
  viz.py
  README.md
pyproject.toml      # build metadata
tests/              # pytest scaffolding
examples/           # minimal “hello flow”
```

**Acceptance**

* Library installs locally (`pip install -e .`), `pytest -q` runs (empty tests pass).
* README shows vision & module map.

---

## Phase 1 — Safe Core Runtime (OoFlow → PenguiFlow) (1–2 days)

**Goals**

* Rename, harden, and keep API minimal.

**Spec**

* **Renames**:

  * `Edge` → `Floe`
  * `OoFlow` → `PenguiFlow`
  * Ingress (`Yang`) → **OpenSea**
  * Egress (`Yin`) → **Rookery**
* **Queues/Backpressure**: `Floe.queue = asyncio.Queue(maxsize=DEFAULT_MAXSIZE)`
* **CPU-friendly fetch**: `Context.fetch_any()` uses `asyncio.wait` over multiple `get()`s (no polling).
* **Graceful stop**: `stop()` cancels and `await`s tasks (`gather(..., return_exceptions=True)`).
* **Error boundaries**: every node task wrapped; exceptions logged; task doesn’t silently die.
* **Stable IDs**: each Node has `node_id` (uuid4) and `name`.
* **Cycle check**: topological validation; fail if cycles unless explicitly allowed by node flag (used later for controller loop).
* **Selective emit**: `ctx.emit(msg, to=[nodeA, nodeC])` preserved.

**Public API (minimal)**

```python
class Context:
    async def emit(self, msg, to: list["Node"]| "Node"| None = None): ...
    def emit_nowait(self, msg, to=...): ...
    async def fetch(self, from_: list["Node"]| "Node"| None = None): ...
    async def fetch_any(self, from_: list["Node"]| "Node"| None = None): ...

class Node:
    def __init__(self, func, name: str | None = None, policy: "NodePolicy" | None = None): ...
    def to(self, *nodes: "Node") -> tuple["Node", tuple["Node", ...]]: ...

class PenguiFlow:
    def __init__(self, *adjacencies: tuple[Node, tuple[Node, ...]]): ...
    def run(self, *, registry=None): ...
    async def stop(self): ...
    async def emit(self, msg, to=None): ...
    async def fetch(self, from_=None): ...
```

**Tests**

* Unit: single edge pass-through, fan-out to two nodes, backpressure (queue fills).
* Concurrency: two successor nodes receive same message concurrently.
* Stop: tasks cancelled & awaited; no resource leak.

**Acceptance**

* All tests pass. Example demonstrating OpenSea→node→Rookery works.

---

## Phase 2 — Pydantic Type-Safety & Registry (1–2 days)

**Goals**

* Make message envelopes & node I/O **runtime type-safe** without coupling core to product models.

**Spec**

* `types.py`:

```python
class Headers(BaseModel):
    tenant: str
    topic: str | None = None
    priority: int = 0

class Message(BaseModel):
    payload: Any
    headers: Headers
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)
    deadline_s: float | None = None
```

* `registry.py`:

```python
class ModelRegistry:
    def register(self, node_name: str, in_model: type[BaseModel], out_model: type[BaseModel]): ...
    def adapters(self, node_name: str) -> tuple[TypeAdapter, TypeAdapter]: ...
```

* `node.py`:

  * `NodePolicy(validate="both"|"in"|"out"|"none", timeout_s, max_retries, backoff_base, backoff_mult, max_backoff)`
  * `Node.__call__(ctx, registry)` pulls adapters and validates in/out.

**Developer Flow (service code)**

* Service defines Pydantic models and async node funcs.
* Service registers `(node_name, InModel, OutModel)` at startup.
* Core validates automatically on fetch/emit.

**Tests**

* Validation passes on correct shapes; raises on wrong payload.
* Discriminated union example validates & routes (see Phase 4).

**Acceptance**

* Example in `examples/type_safe/` shows typed triage→retriever→packer pipeline.

---

## Phase 3 — Reliability & Observability (1–2 days)

**Goals**

* Retries, timeouts, structured logs, queue metrics.

**Spec**

* **Retries** in `Node.__call__` based on `NodePolicy` with exponential backoff.
* **Timeouts**: `asyncio.wait_for` per node policy.
* **Structured logs** (JSON or key=value): fields `{ts, level, node_name, node_id, event, trace_id, latency_ms, q_depth_in, q_depth_out, attempt}`.
* **Middlewares (optional)**: hooks on `emit`/`fetch` to record to MLflow or other sinks.

**Tests**

* Induce errors to verify retry & backoff, timeout handling, and logging.

**Acceptance**

* Logs visible and informative; retry behavior configurable per node.

---

## Phase 4 — Concurrency, Routing, and Patterns (1–2 days)

**Goals**

* Provide first-class primitives to make concurrent flows and decision points trivial.

**Spec**

* `patterns.py` includes:

  * `map_concurrent(items, worker, max_concurrency=8)` helper.
  * `make_join_k(name, k)` Node that aggregates K messages per `trace_id`.
  * **Router options**:

    * **Predicate router**: Node subclass that inspects payload/headers and emits selectively to a chosen subset of successors (`to=[...]`).
    * **Schema-driven routing**: use **discriminated unions**; connect router → node\_brand / node\_genre; registry enforces types.

**Examples**

* `examples/routing_predicate/`
* `examples/routing_union/`
* `examples/fanout_join/`

**Tests**

* Router selects correct successors.
* Fan-out + `join_k` merges correctly and once per `trace_id`.
* Bounded concurrency honored by semaphore.

**Acceptance**

* Examples run and demonstrate A/B/C routing and parallel branches.

---

## Phase 5 — Dynamic Multi-Hop Controller Loop (1–2 days)

**Goals**

* Support unknown number of hops (agent planning).

**Spec**

* Allow **opt-in cycles**: node can declare `allow_cycle=True`; topological checker skips that edge.
* Define **controller loop** pattern:

  * Pydantic models for `WM` (working memory), `PlanStep`, `Thought`, `FinalAnswer`.
  * Controller node returns either `WM` (loop again) or `FinalAnswer` (terminate).
  * Wire `controller.to(controller)` and `controller.to(answer_node)`.
* **Budgets/Guardrails**: hops counter, deadline, token/latency budget fields in `WM` or headers; controller enforces.

**Example**

* `examples/controller_multihop/`: triage → controller (loop) → answer.

**Tests**

* Loop executes N hops then stops by budget.
* Mixed parallel steps (map\_concurrent) inside controller.
* Terminal condition triggers Rookery output.

**Acceptance**

* Example returns a final answer with variable hops; tests assert hop counts & stop conditions.

---

## Phase 6 — Subflows (“Playbooks”) (1–2 days)

**Goals**

* Run mini-DAGs at runtime as reusable blocks.

**Spec**

* `PenguiFlow.spawn(playbook, parent_msg, timeout=None, budget=None)` or helper `call_playbook(playbook, parent_msg)`.
* A `Playbook` is a factory returning `(subflow, local_registry)`.
* Subflow **inherits** parent `trace_id` and `headers`; result is the first Rookery message payload.
* Cancellation propagation: stopping parent flow cancels child flows.

**Example**

* `examples/playbook_retrieval/`: Controller calls a **RetrievalPlaybook** (Retrieve → Rerank → Compress) and receives the compressed context.

**Tests**

* Subflow runs to completion and returns payload; parent cancellation cancels child.

**Acceptance**

* Playbook demo works; headers/trace\_id preserved across boundaries.

---

# Developer Guide (README content snapshot. Expected to complete after full development)

## Installation

```bash
pip install -e ./penguiflow
```

## Quickstart

```python
from pydantic import BaseModel
from penguiflow.node import Node, NodePolicy
from penguiflow.registry import ModelRegistry
from penguiflow.core import PenguiFlow, create
from penguiflow.types import Message, Headers

class TriageIn(BaseModel): text: str
class TriageOut(BaseModel): text: str; topic: str
class RetrieveOut(BaseModel): topic: str; docs: list[str]
class PackOut(BaseModel): prompt: str

async def triage(m: TriageIn) -> TriageOut:
    return TriageOut(text=m.text, topic="metrics")

async def retriever(m: TriageOut) -> RetrieveOut:
    return RetrieveOut(topic=m.topic, docs=["d1","d2"])

async def packer(m: RetrieveOut) -> PackOut:
    return PackOut(prompt=f"[{m.topic}] using {len(m.docs)} docs")

triage_node    = Node("triage", triage)
retriever_node = Node("retriever", retriever)
packer_node    = Node("packer", packer)

registry = ModelRegistry()
registry.register("triage",    TriageIn,    TriageOut)
registry.register("retriever", TriageOut,   RetrieveOut)
registry.register("packer",    RetrieveOut, PackOut)

flow = create(triage_node.to(retriever_node),
              retriever_node.to(packer_node))

flow.run(registry=registry)
await flow.emit(Message(payload=TriageIn(text="unique reach"), headers=Headers(tenant="acme")))
out = await flow.fetch()
print(out.payload)  # PackOut(...)
await flow.stop()
```

## Routing (two styles)

**Predicate router**

```python
class RouteOut(BaseModel): key: str

async def route_fn(m: TriageOut) -> RouteOut:
    key = "fast" if m.topic == "metrics" else "fallback"
    return RouteOut(key=key)

router = Node("router", route_fn)

# In RouterNode.__call__, emit selectively based on key -> successor names.
```

**Schema-driven**

```python
class QBrand(BaseModel): kind: Literal["by_brand"]; brand: str
class QGenre(BaseModel): kind: Literal["by_genre"]; genre: str
Query = Annotated[QBrand | QGenre, Field(discriminator="kind")]

router = Node("router", lambda q: q)  # passes through; successor nodes typed per variant
```

## Controller loop (dynamic hops)

* Controller returns `WM` to continue, `FinalAnswer` to end.
* Wire `controller.to(controller)` and `controller.to(answer_node)`.

## Playbooks

* Define a `build()` that returns `(subflow, local_registry)`.
* Call via `await call_playbook(play, parent_msg)`.

## Observability

* JSON logs; fields include `node_name`, `trace_id`, `latency_ms`, `attempt`, `queue_depth`.
* Optional MLflow middleware (log per-node timings & retries).

---

# Implementation Milestones & Outcomes (for Codex)

| Phase | Outcome           | Artifacts                                                                                       | Tests / DoD                                             |
| ----- | ----------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 0     | Repo skeleton     | package layout, pyproject, README outline                                                       | `pytest` runs, CI scaffolding                           |
| 1     | Safe core runtime | Renames, backpressure, fetch\_any, graceful stop, error boundaries, cycle check, selective emit | Unit tests for pass-through, fan-out, stop, cycle error |
| 2     | Type-safety       | Pydantic `Message/Headers`, `ModelRegistry`, Node in/out validation                             | Type checks, union validation                           |
| 3     | Reliability & Obs | Retries, timeouts, structured logs, q-metrics, middleware hooks                                 | Retry+timeout test; verify logs                         |
| 4     | Patterns          | Router(s), `join_k`, `map_concurrent`                                                           | Routing correctness; fan-out/ join; bounded concurrency |
| 5     | Controller loop   | Opt-in cycles, `WM/PlanStep/FinalAnswer`, budget guardrails                                     | Variable hops example; stop conditions                  |
| 6     | Subflows          | `spawn()`/`call_playbook()`; header/trace inheritance; cancel propagation                       | Playbook example; cancellation test                     |

**General DoD**

* 90%+ unit test coverage for core modules.
* Examples runnable from README.
* No flake8/mypy errors (basic typing; we tolerate dynamic where needed).
* Benchmarks (simple): throughput with 1k messages, memory growth bounded.



# Risk & Mitigation

* **Risk**: Validation overhead on hot paths.
  **Mitigation**: `NodePolicy(validate="in")` or `"none"` for hot nodes; reuse `TypeAdapter`s (we do).
* **Risk**: Developer friction with registry.
  **Mitigation**: sugar `register_nodes([ (node, In, Out), ... ])` and clear examples.
* **Risk**: Loop misuse → infinite hops.
  **Mitigation**: default hop budget & deadline in `WM`; enforce in controller helper.


