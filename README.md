# PenguiFlow 🐧❄️

<p align="center">
  <img src="asset/Penguiflow.png" alt="PenguiFlow logo" width="220">
</p>

<p align="center">
  <a href="https://github.com/penguiflow/penguiflow/actions/workflows/ci.yml">
    <img src="https://github.com/penguiflow/penguiflow/actions/workflows/ci.yml/badge.svg" alt="CI Status">
  </a>
  <a href="https://github.com/penguiflow/penguiflow">
    <img src="https://img.shields.io/badge/coverage-85%25-brightgreen" alt="Coverage">
  </a>
  <a href="https://pypi.org/project/penguiflow/">
    <img src="https://img.shields.io/pypi/v/penguiflow.svg" alt="PyPI version">
  </a>
  <a href="https://github.com/penguiflow/penguiflow/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License">
  </a>
</p>

**Async-first orchestration library for multi-agent and data pipelines**

PenguiFlow is a **lightweight Python library** to orchestrate agent flows.
It provides:

* **Typed, async message passing** (Pydantic v2)
* **Concurrent fan-out / fan-in patterns**
* **Routing & decision points**
* **Retries, timeouts, backpressure**
* **Streaming chunks** (LLM-style token emission with `Context.emit_chunk`)
* **Dynamic loops** (controller nodes)
* **Runtime playbooks** (callable subflows with shared metadata)
* **Per-trace cancellation** (`PenguiFlow.cancel` with `TraceCancelled` surfacing in nodes)
* **Deadlines & budgets** (`Message.deadline_s`, `WM.budget_hops`, and `WM.budget_tokens` guardrails that you can leave unset/`None`)
* **Observability hooks** (`FlowEvent` callbacks for logging, MLflow, or custom metrics sinks)

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
msg.meta["request_id"] = "abc123"
```

### Node

A node is an async function wrapped with a `Node`.
It validates inputs/outputs (via `ModelRegistry`) and applies `NodePolicy` (timeout, retries, etc.).

```python
from penguiflow.node import Node

class QueryOut(BaseModel):
    topic: str

async def triage(msg: QueryIn, ctx) -> QueryOut:
    return QueryOut(topic="metrics")

triage_node = Node(triage, name="triage")
```

Node functions must always accept **two positional parameters**: the incoming payload and
the `Context` object. If a node does not use the context, name it `_` or `_ctx`, but keep
the parameter so the runtime can still inject it. Registering the node with
`ModelRegistry` ensures the payload is validated/cast to the expected Pydantic model;
setting `NodePolicy(validate="none")` skips that validation for hot paths.

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
   * v1 intentionally stays in-process; scaling out or persisting state will arrive with future pluggable backends.

2. **Typed contracts.**

   * In/out models per node are defined with Pydantic.
   * Validated at runtime via cached `TypeAdapter`s.
   * `flow.run(registry=...)` verifies every validating node is registered so misconfigurations fail fast.

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

Requires **Python 3.11+**.

---

## 🆕 Implemented v2 Features

### Streaming chunks

Phase 1 delivered token-level streaming without sacrificing backpressure or ordering guarantees.
Use `Context.emit_chunk` to fan streaming tokens to downstream nodes. The helper automatically:

* Builds `StreamChunk` payloads with the parent message's routing headers and trace metadata.
* Manages monotonically increasing sequence numbers per `stream_id` (defaulting to the parent trace).
* Awaits per-trace capacity via the runtime before delivering follow-up chunks so slow consumers don't starve other traces.

See `tests/test_streaming.py` for coverage and `examples/streaming_llm/` for an end-to-end mock that feeds an SSE/WebSocket sink.

### Per-trace cancellation

Phase 2 introduces cancellation scoped to a single trace:

* Call `PenguiFlow.cancel(trace_id)` to request cancellation; the method is idempotent and resolves immediately when nothing is running.
* In-flight node invocations for that trace raise `TraceCancelled`, letting node authors clean up resources or abort retries.
* Streaming emitters and regular messages honor per-trace queue capacity, so cancellation drains outstanding work and unblocks producers safely.
* Lifecycle hooks emit `trace_cancel_start` and `trace_cancel_finish` events so observability backends can track latency to abort.

The behaviour is enforced by `tests/test_cancel.py`, ensuring other traces continue unaffected while the cancelled trace unwinds cleanly.

### Deadlines & budgets

Phase 3 adds wall-clock and logical guardrails so long-running traces shut down gracefully:

* Set `Message.deadline_s` to cap wall-clock time for a trace. The runtime now checks deadlines before invoking each node and short-circuits to Rookery with `FinalAnswer("Deadline exceeded")` when the clock has expired.
* Controllers can increment `WM.tokens_used` alongside `WM.hops`. When `WM.budget_tokens` is configured, PenguiFlow automatically emits `FinalAnswer("Token budget exhausted")` once the total meets or exceeds the budget, similar to the existing hop budget.
* Leave `deadline_s` unset or configure `WM.budget_hops=None` / `WM.budget_tokens=None` to keep the behaviour unbounded—guardrails are entirely opt-in.
* Tests in `tests/test_budgets.py` cover both deadline short-circuiting and token budget enforcement, complementing the hop and deadline checks exercised in `tests/test_controller.py`.
* Example: `examples/controller_multihop/` demonstrates configuring deadlines, hop limits, and token budgets together in a looping controller.

### Message metadata propagation

Phase 4 introduces a `meta: dict[str, Any]` bag on every `Message` so nodes can attach
debugging breadcrumbs, pricing data, routing hints, or attribution without polluting
the primary payload:

* The runtime preserves `message.meta` across retries, controller loops, and
  subflows. Helpers such as `Context.emit_chunk` and `PenguiFlow.emit_chunk` now clone
  the parent's metadata when wrapping `StreamChunk` payloads so streaming sinks stay in
  sync with upstream context.
* Nodes can safely mutate `message.meta` in-place or return a new message via
  `message.model_copy(update={"meta": {...}})` when they need to add or redact keys.
* `tests/test_metadata.py` exercises round-tripping metadata through multiple nodes and
  streaming helpers, while `examples/metadata_propagation/` provides a runnable demo of
  enriching messages with retrieval costs and summarizer details.

## 🧭 Repo Structure

penguiflow/
  __init__.py
  core.py          # runtime orchestrator, retries, controller helpers, playbooks
  node.py
  types.py
  registry.py
  patterns.py
  middlewares.py
  viz.py
  README.md
pyproject.toml      # build metadata
tests/              # pytest suite
examples/           # runnable flows (fan-out, routing, controller, playbooks)

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

### Patterns Toolkit

PenguiFlow ships a handful of **composable patterns** to keep orchestration code tidy
without forcing you into a one-size-fits-all DSL. Each helper is opt-in and can be
stitched directly into a flow adjacency list:

- `map_concurrent(items, worker, max_concurrency=8)` — fan a single message out into
  many in-memory tasks (e.g., batch document enrichment) while respecting a semaphore.
- `predicate_router(name, mapping)` — route messages to successor nodes based on simple
  boolean functions over payload or headers. Perfect for guardrails or conditional
  tool invocation without building a full controller.
- `union_router(name, discriminated_model)` — accept a Pydantic discriminated union and
  forward each variant to the matching typed successor node. Keeps type-safety even when
  multiple schema branches exist.
- `join_k(name, k)` — aggregate `k` messages per `trace_id` before resuming downstream
  work. Useful for fan-out/fan-in batching, map-reduce style summarization, or consensus.

All helpers are regular `Node` instances under the hood, so they inherit retries,
timeouts, and validation just like hand-written nodes.

### Streaming Responses

PenguiFlow now supports **LLM-style streaming** with the `StreamChunk` model. Each
chunk carries `stream_id`, `seq`, `text`, optional `meta`, and a `done` flag. Use
`Context.emit_chunk(parent=message, text=..., done=...)` inside a node (or the
convenience wrapper `await flow.emit_chunk(...)` from outside a node) to push
chunks downstream without manually crafting `Message` envelopes:

```python
await ctx.emit_chunk(parent=msg, text=token, done=done)
```

- Sequence numbers auto-increment per `stream_id` (defaults to the parent trace).
- Backpressure is preserved; if the downstream queue is full the helper awaits just
  like `Context.emit`.
- When `done=True`, the sequence counter resets so a new stream can reuse the same id.

Pair the producer with a sink node that consumes `StreamChunk` payloads and assembles
the final result when `done` is observed. See `examples/streaming_llm/` for a complete
mock LLM → SSE pipeline. For presentation layers, utilities like
`format_sse_event(chunk)` and `chunk_to_ws_json(chunk)` (both exported from the
package) will convert a `StreamChunk` into SSE-compatible text or WebSocket JSON payloads
without boilerplate.

### Dynamic Controller Loops

Long-running agents often need to **think, plan, and act over multiple hops**. PenguiFlow
models this with a controller node that loops on itself:

1. Define a controller `Node` with `allow_cycle=True` and wire `controller.to(controller)`.
2. Emit a `Message` whose payload is a `WM` (working memory). PenguiFlow increments the
   `hops` counter automatically and enforces `budget_hops` + `deadline_s` so controllers
   cannot loop forever.
3. The controller can attach intermediate `Thought` artifacts or emit `PlanStep`s for
   transparency/debugging. When it is ready to finish, it returns a `FinalAnswer` which
   is immediately forwarded to Rookery.

Deadlines and hop budgets turn into automated `FinalAnswer` error messages, making it
easy to surface guardrails to downstream consumers.

---

### Playbooks & Subflows

Sometimes a controller or router needs to execute a **mini flow** — for example,
retrieval → rerank → compress — without polluting the global topology.
`Context.call_playbook` spawns a brand-new `PenguiFlow` on demand and wires it into
the parent message context:

- Trace IDs and headers are reused so observability stays intact.
- The helper respects optional timeouts, mirrors cancellation to the subflow, and always
  stops it (even on cancel).
- The first payload emitted to the playbook's Rookery is returned to the caller,
  allowing you to treat subflows as normal async functions.

```python
from penguiflow.types import Message

async def controller(msg: Message, ctx) -> Message:
    playbook_result = await ctx.call_playbook(build_retrieval_playbook, msg)
    return msg.model_copy(update={"payload": playbook_result})
```

Playbooks are ideal for deploying frequently reused toolchains while keeping the main
flow focused on high-level orchestration logic.

---

### Visualization

Need a quick view of the flow topology? Call `flow_to_mermaid(flow)` to render the graph
as a Mermaid diagram ready for Markdown or docs tools, or `flow_to_dot(flow)` for a
Graphviz-friendly definition. Both outputs annotate controller loops and the synthetic
OpenSea/Rookery boundaries so you can spot ingress/egress paths at a glance:

```python
from penguiflow import flow_to_dot, flow_to_mermaid

print(flow_to_mermaid(flow, direction="LR"))
print(flow_to_dot(flow, rankdir="LR"))
```

See `examples/visualizer/` for a runnable script that exports Markdown and DOT files for
docs or diagramming pipelines.

---

## 🛡️ Reliability & Observability

* **NodePolicy**: set validation scope plus per-node timeout, retries, and backoff curves.
* **Per-trace metrics**: cancellation events include `trace_pending`, `trace_inflight`,
  `q_depth_in`, `q_depth_out`, and node fan-out counts for richer observability.
* **Structured `FlowEvent`s**: every node event carries `{ts, trace_id, node_name, event,
  latency_ms, q_depth_in, q_depth_out, attempt}` plus a mutable `extra` map for custom
  annotations.
* **Middleware hooks**: subscribe observers (e.g., MLflow) to the structured `FlowEvent`
  stream. See `examples/mlflow_metrics/` for an MLflow integration and
  `examples/reliability_middleware/` for a concrete timeout + retry walkthrough.

---

## ⚠️ Current Constraints

- **In-process runtime**: there is no built-in distribution layer yet. Long-running CPU work should be delegated to your own pools or services.
- **Registry-driven typing**: nodes default to validation. Provide a `ModelRegistry` when calling `flow.run(...)` or set `validate="none"` explicitly for untyped hops.
- **Observability**: structured `FlowEvent` callbacks power logs/metrics; integrations with
  third-party stacks (OTel, Prometheus, Datadog) remain DIY. See the MLflow middleware
  example for a lightweight pattern.
- **Roadmap**: v2 targets streaming, distributed backends, richer observability, and test harnesses. Contributions and proposals are welcome!

---

## 📊 Benchmarks

Lightweight benchmarks live under `benchmarks/`. Run them via `uv run python benchmarks/<name>.py`
to capture baselines for fan-out throughput, retry/timeout overhead, and controller
playbook latency. Copy them into product repos to watch for regressions over time.

---

## 🔮 Roadmap

* **v1 (current)**: safe core runtime, type-safety, retries, timeouts, routing, controller loops, playbooks via examples.
* **v2 (in progress)**: streaming support (Phase 1 shipped), upcoming per-trace cancel,
  deadlines/budgets, observability hooks, visualizer, and testing harness.

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

## 📖 Examples

* `examples/quickstart/`: hello world pipeline.
* `examples/routing_predicate/`: branching with predicates.
* `examples/routing_union/`: discriminated unions with typed branches.
* `examples/fanout_join/`: split work and join with `join_k`.
* `examples/map_concurrent/`: bounded fan-out work inside a node.
* `examples/controller_multihop/`: dynamic multi-hop agent loop.
* `examples/reliability_middleware/`: retries, timeouts, and middleware hooks.
* `examples/mlflow_metrics/`: structured `FlowEvent` export to MLflow (stdout fallback).
* `examples/playbook_retrieval/`: retrieval → rerank → compress playbook.
* `examples/trace_cancel/`: per-trace cancellation propagating into a playbook.
* `examples/streaming_llm/`: mock LLM emitting streaming chunks to an SSE sink.
* `examples/metadata_propagation/`: attaching and consuming `Message.meta` context.
* `examples/visualizer/`: exports Mermaid + DOT diagrams with loop/subflow annotations.

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
