## Vision

Build **penguiflow**: a small, repo-agnostic Python library that orchestrates async agent pipelines with:

* **Typed messages** (Pydantic v2),
* **Concurrent fan-out / fan-in**,
* **Routing/decision points**, and
* **Dynamic multi-hop controller loops** + **runtime subflows (playbooks)**,

…with strong reliability (backpressure, retries, timeouts, graceful stop) and basic observability (structured logs, hooks for MLflow).

## Non-Goals

* No external brokers (Kafka/Redis) in v1; in-process asyncio only (hooks to add backends later).
* No UI/console beyond logs & examples.
* No heavy scheduling/cron features (out of scope).

## Repo Layout (target)

```
penguiflow/
  __init__.py
  core.py          # Context, Floe, PenguiFlow (runtime)
  node.py          # Node, NodePolicy
  types.py         # Message, Headers (Pydantic models)
  registry.py      # ModelRegistry (TypeAdapters per node)
  patterns.py      # router(s), join_k, map_concurrent helpers
  middlewares.py   # optional hooks: logging, mlflow
  viz.py           # (optional) DOT/Mermaid exporter
  README.md        # developer guide + quickstart
examples/
  quickstart/
  routing_predicate/
  routing_union/
  fanout_join/
  controller_multihop/
  playbook_retrieval/
tests/
  test_core.py
  test_types.py
  test_registry.py
  test_patterns.py
  test_controller.py
pyproject.toml
```

## Phased Delivery (each phase is shippable)

### Phase 0 — Skeleton

**Goal:** Create package structure + tooling.

* Scaffolding per layout above.
* `pyproject.toml` (pydantic>=2, python>=3.12).
* CI: run `pytest -q`, `ruff`, and `mypy` (loose mode).

**Acceptance:** installable `pip install -e .`; CI green with empty tests.

---

### Phase 1 — Safe Core Runtime (OoFlow → PenguiFlow)

**Renames:** Context→**Context**, Edge→**Floe**, OoFlow→**PenguiFlow**, Yang→**OpenSea**, Yin→**Rookery**.

**Features**

* `Floe.queue = asyncio.Queue(maxsize=DEFAULT_MAXSIZE)` (configurable).
* `Context.fetch_any()` uses `asyncio.wait` (no busy-wait).
* `PenguiFlow.stop()` cancels **and awaits** tasks (graceful).
* Error boundaries around each node task (structured logs).
* Stable `node_id` (uuid) + human `name`.
* **Cycle detection** (toposort). Allow opt-in cycles later via a flag.
* `emit(msg, to=[...])` selective emission (keep existing semantics).

**Acceptance**

* Examples: simple pass-through; fan-out to two nodes.
* Tests: backpressure (queue fills), graceful stop, cycle rejection.

---

### Phase 2 — Pydantic Type-Safety & Registry

**Models**

```python
# types.py
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

**Registry**

```python
# registry.py
class ModelRegistry:
    def register(self, node_name: str, in_model: type[BaseModel], out_model: type[BaseModel]): ...
    def adapters(self, node_name: str) -> tuple[TypeAdapter, TypeAdapter]: ...
```

**Node**

* `NodePolicy(validate="both"|"in"|"out"|"none", timeout_s, max_retries, backoff_base, backoff_mult, max_backoff)`
* `Node.__call__(ctx, registry)` validates IN/OUT with cached `TypeAdapter`s.

**Acceptance**

* Example: typed triage→retriever→packer.
* Tests: valid/invalid payloads; discriminated union basic check.

---

### Phase 3 — Reliability & Observability

**Add**

* Retries with exponential backoff per `NodePolicy`.
* Timeouts per node (`asyncio.wait_for`).
* Structured logs with fields: `{ts, level, node_name, node_id, event, trace_id, latency_ms, q_depth_in, attempt}`.
* Middleware hook points (optional) for MLflow.

**Acceptance**

* Tests induce failure to confirm retry/timeout behavior; logs visible.

---

### Phase 4 — Concurrency, Routing & Patterns

**Helpers**

* `map_concurrent(items, worker, max_concurrency=8)` (semaphore-bounded).
* `join_k(name, k)` Node: aggregate K messages per `trace_id`.
* **Routers**:

  * Predicate router: emits to selected successors based on payload/headers.
  * Union router: use discriminated unions; successor nodes typed to each variant.

**Acceptance**

* Examples: `routing_predicate`, `routing_union`, `fanout_join`.
* Tests: correct successor selection; bounded concurrency; join emits once.

---

### Phase 5 — Dynamic Multi-Hop Controller Loop

**Models**

```python
class PlanStep(BaseModel):
    kind: Literal["retrieve","web","sql","summarize","route","stop"]
    args: dict = {}
    max_concurrency: int = 1

class Thought(BaseModel):
    steps: list[PlanStep]
    rationale: str
    done: bool = False

class WM(BaseModel):
    query: str
    facts: list[Any] = []
    hops: int = 0
    budget_hops: int = 8
    confidence: float = 0.0

class FinalAnswer(BaseModel):
    text: str
    citations: list[str] = []
```

**Behavior**

* Allow **opt-in cycles** on the controller node (skip topo error).
* Controller returns `WM` → loops; `FinalAnswer` → goes to Rookery.
* Enforce budgets: hops, deadline.

**Acceptance**

* Example: `controller_multihop` running 2–5 hops based on budget.
* Tests: loop terminates by `done=True` or budget.

---

### Phase 6 — Subflows (“Playbooks”)

**API**

```python
# core.py (helper)
async def call_playbook(playbook: Callable[[], tuple[PenguiFlow, ModelRegistry]],
                        parent_msg: Message,
                        timeout: float | None = None) -> Any:
    # runs subflow, inherits trace_id/headers, returns first Rookery payload
```

**Acceptance**

* Example: `playbook_retrieval` (Retrieve→Rerank→Compress) callable from controller.
* Tests: subflow completion; cancellation propagates.

---

## Coding Standards

* Python ≥ 3.12, Pydantic ≥ 2.0.
* Async only (`asyncio`); no threads.
* Tests with `pytest`; aim for \~85–90% core coverage.
* Lint with `ruff`; type-check with `mypy` (loose/`Any` allowed where necessary).
* Start env and handle env with `uv`. Keep pyproject.toml updated
* Every new primitive must have: unit tests + a runnable example.

## Performance Notes

* Cache `TypeAdapter`s (registry).
* Allow `validate="in"` or `"none"` in hot nodes.
* Use `maxsize` queues; expose config in `PenguiFlow` ctor or `RuntimeConfig`.

