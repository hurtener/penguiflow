# PenguiFlow 🐧❄️

**Async-first orchestration library for multi-agent and data pipelines**

PenguiFlow is a **lightweight Python library** to orchestrate agent flows.
It provides:

* **Typed, async message passing** (Pydantic v2)
* **Concurrent fan-out / fan-in patterns**
* **Routing & decision points**
* **Retries, timeouts, backpressure**
* **Dynamic loops** (controller nodes)

Built on pure `asyncio` (no threads), PenguiFlow is small, predictable, and repo-agnostic.
Product repos only define **their models + node functions** — the core stays dependency-light.

---

## ✨ Why PenguiFlow?

* **Orchestration is everywhere.** Every Pengui service needs to connect LLMs, retrievers, SQL, or external APIs.
* **Stop rewriting glue.** This library gives you reusable primitives (nodes, flows, contexts) so you can focus on business logic.
* **Typed & safe.** Every hop validated with Pydantic.
* **Lightweight.** Only depends on asyncio + pydantic. No broker, no server, no threads.

---

## 🏗️ Core Concepts

### Message

Every payload is wrapped in a `Message` with headers and metadata.

```python
from pydantic import BaseModel
from penguiflow.types import Message, Headers

class QueryIn(BaseModel):
    text: str

msg = Message(
    payload=QueryIn(text="unique reach last 30 days"),
    headers=Headers(tenant="acme")
)
```

### Node

A node is an async function wrapped with a `Node`.
It validates inputs/outputs (via `ModelRegistry`) and applies `NodePolicy` (timeout, retries, etc.).

```python
from penguiflow.node import Node

class QueryOut(BaseModel):
    topic: str

async def triage(m: QueryIn) -> QueryOut:
    return QueryOut(topic="metrics")

triage_node = Node(triage, name="triage")
```

### Flow

A flow wires nodes together in a directed graph.
Edges are called **Floe**s, and flows have two invisible contexts:

* **OpenSea** 🌊 — ingress (start of the flow)
* **Rookery** 🐧 — egress (end of the flow)

```python
from penguiflow.core import create

flow = create(
    triage_node.to(packer_node)
)
```

### Running a Flow

```python
from penguiflow.registry import ModelRegistry

registry = ModelRegistry()
registry.register("triage", QueryIn, QueryOut)
registry.register("packer", QueryOut, PackOut)

flow.run(registry=registry)

await flow.emit(msg)          # emit into OpenSea
out = await flow.fetch()      # fetch from Rookery
print(out.payload)            # PackOut(...)
await flow.stop()
```

---

## 🧭 Design Principles

1. **Async-only (`asyncio`).**

   * Flows are orchestrators, mostly I/O-bound.
   * Async tasks are cheap, predictable, and cancellable.
   * Heavy CPU work should be offloaded inside a node (process pool, Ray, etc.), not in PenguiFlow itself.

2. **Typed contracts.**

   * In/out models per node are defined with Pydantic.
   * Validated at runtime via cached `TypeAdapter`s.

3. **Reliability first.**

   * Timeouts, retries with backoff, backpressure on queues.
   * Nodes run inside error boundaries.

4. **Minimal dependencies.**

   * Only asyncio + pydantic.
   * No broker, no server. Everything in-process.

5. **Repo-agnostic.**

   * Product repos declare their models + node funcs, register them, and run.
   * No product-specific code in the library.

---

## 📦 Installation

```bash
pip install -e ./penguiflow
```

Requires **Python 3.12+**.

## 🧭 Repo Structure

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

---

## 🚀 Quickstart Example

```python
from pydantic import BaseModel
from penguiflow import Headers, Message, ModelRegistry, Node, NodePolicy, create


class TriageIn(BaseModel):
    text: str


class TriageOut(BaseModel):
    text: str
    topic: str


class RetrieveOut(BaseModel):
    topic: str
    docs: list[str]


class PackOut(BaseModel):
    prompt: str


async def triage(msg: TriageIn, ctx) -> TriageOut:
    topic = "metrics" if "metric" in msg.text else "general"
    return TriageOut(text=msg.text, topic=topic)


async def retrieve(msg: TriageOut, ctx) -> RetrieveOut:
    docs = [f"doc_{i}_{msg.topic}" for i in range(2)]
    return RetrieveOut(topic=msg.topic, docs=docs)


async def pack(msg: RetrieveOut, ctx) -> PackOut:
    prompt = f"[{msg.topic}] summarize {len(msg.docs)} docs"
    return PackOut(prompt=prompt)


triage_node = Node(triage, name="triage", policy=NodePolicy(validate="both"))
retrieve_node = Node(retrieve, name="retrieve", policy=NodePolicy(validate="both"))
pack_node = Node(pack, name="pack", policy=NodePolicy(validate="both"))

registry = ModelRegistry()
registry.register("triage", TriageIn, TriageOut)
registry.register("retrieve", TriageOut, RetrieveOut)
registry.register("pack", RetrieveOut, PackOut)

flow = create(
    triage_node.to(retrieve_node),
    retrieve_node.to(pack_node),
)
flow.run(registry=registry)

message = Message(
    payload=TriageIn(text="show marketing metrics"),
    headers=Headers(tenant="acme"),
)

await flow.emit(message)
out = await flow.fetch()
print(out.prompt)  # PackOut(prompt='[metrics] summarize 2 docs')

await flow.stop()
```

---

## 🛡️ Reliability & Observability

* **NodePolicy**: set validation scope plus per-node timeout, retries, and backoff curves.
* **Structured logs**: enrich every node event with `{ts, trace_id, node_name, event, latency_ms, q_depth_in, attempt}`.
* **Middleware hooks**: subscribe observers (e.g., MLflow) to the structured event stream.

---

## 🔮 Roadmap

* **v1 (current)**: safe core runtime, type-safety, retries, timeouts, routing, controller loops, playbooks via examples.
* **v2 (future)**: streaming support, per-trace cancel, deadlines/budgets, observability hooks, visualizer, testing harness.

---

## 🧪 Testing

```bash
pytest -q
```

* Unit tests cover core runtime, type safety, routing, retries.
* Example flows under `examples/` are runnable end-to-end.

---

## 🐧 Naming Glossary

* **Node**: an async function + metadata wrapper.
* **Floe**: an edge (queue) between nodes.
* **Context**: context passed into each node to fetch/emit.
* **OpenSea** 🌊: ingress context.
* **Rookery** 🐧: egress context.

---

## 📖 Examples (To be developed)

* `examples/quickstart/`: hello world pipeline.
* `examples/routing_predicate/`: branching with predicates.
* `examples/controller_multihop/`: dynamic multi-hop agent loop.
* `examples/playbook_retrieval/`: retrieval → rerank → compress (demo).

---

## 🤝 Contributing

* Keep the library **lightweight and generic**.
* Product-specific playbooks go into `examples/`, not core.
* Every new primitive requires:

  * Unit tests in `tests/`
  * Runnable example in `examples/`
  * Docs update in README

---

## License

MIT


