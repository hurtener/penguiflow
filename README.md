# PenguiFlow 🐧❄️

<p align="center">
  <img src="asset/Penguiflow.png" alt="PenguiFlow logo" width="220">
</p>

<p align="center">
  <a href="https://github.com/hurtener/penguiflow/actions/workflows/ci.yml"><img src="https://github.com/hurtener/penguiflow/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <a href="https://github.com/hurtener/penguiflow"><img src="https://img.shields.io/badge/coverage-85%25-brightgreen" alt="Coverage"></a>
  <a href="https://nightly.link/hurtener/penguiflow/workflows/benchmarks/main/benchmarks.json.zip"><img src="https://img.shields.io/badge/benchmarks-latest-orange" alt="Benchmarks"></a>
  <a href="https://pypi.org/project/penguiflow/"><img src="https://img.shields.io/pypi/v/penguiflow.svg" alt="PyPI version"></a>
  <a href="https://github.com/hurtener/penguiflow/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
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
* **LLM-driven orchestration** (`ReactPlanner` for autonomous multi-step workflows with tool selection, parallel execution, and pause/resume)
* **Short-term memory (opt-in)** — per-session conversation continuity for `ReactPlanner` with truncation/rolling-summary strategies, fail-closed isolation by `MemoryKey`, and optional persistence via `state_store` (see `docs/MEMORY_GUIDE.md`).
* **Runtime playbooks** (callable subflows with shared metadata)
* **Per-trace cancellation** (`PenguiFlow.cancel` with `TraceCancelled` surfacing in nodes)
* **Deadlines & budgets** (`Message.deadline_s`, `WM.budget_hops`, and `WM.budget_tokens` guardrails that you can leave unset/`None`)
* **Observability hooks** (`FlowEvent` callbacks for logging, MLflow, or custom metrics sinks)
* **Policy-driven routing** (optional policies steer routers without breaking existing flows)
* **Traceable exceptions** (`FlowError` captures node/trace metadata and optionally emits to Rookery)
* **Distribution hooks (opt-in)** — plug a `StateStore` to persist trace history and a
  `MessageBus` to publish floe traffic for remote workers without changing existing flows.
* **Remote calls (opt-in)** — `RemoteNode` bridges the runtime to external agents through a
  pluggable `RemoteTransport` interface (A2A-ready) while propagating streaming chunks and
  cancellation.
* **A2A server adapter (opt-in)** — wrap a PenguiFlow graph in a FastAPI surface using
  `penguiflow_a2a.A2AServerAdapter` so other agents can call `message/send`,
  `message/stream`, and `tasks/cancel` while reusing the runtime's backpressure and
  cancellation semantics.
* **Observability & ops polish** — remote calls emit structured metrics (latency, payload
  sizes, cancel reasons) and the `penguiflow-admin` CLI replays trace history from any
  configured `StateStore` for debugging.
* **Built-in CLI** — `penguiflow init` generates VS Code snippets/launch/tasks/settings for planner development (travels with the pip package).

### v2.7 (current)

**New in v2.7:**
- **Interactive Playground** — browser-based development environment with real-time chat, trajectory visualization, and event inspection (`penguiflow dev`)
- **External Tool Integration (ToolNode)** — unified MCP/UTCP/HTTP tool connections with auth, retry, and streaming
- **Short-Term Memory** — per-session conversation continuity with truncation/rolling-summary strategies and multi-tenant isolation

**v2.6 Streaming (included):**
- `JSONLLMClient` protocol supports `stream` and `on_stream_chunk` parameters
- All templates updated to support streaming callbacks
- Improved token-level streaming for real-time responses

**v2.5 CLI Scaffolding (included):**
- Full `penguiflow new` command with 9 project templates
- **Tier 1 (Core):** `minimal`, `react`, `parallel` — foundational patterns
- **Tier 2 (Service):** `lighthouse`, `wayfinder`, `analyst` — domain-ready agents
- **Tier 3 (Enterprise):** `enterprise` — multi-tenant with RBAC, quotas, audit trails
- **Additional:** `flow`, `controller` — traditional PenguiFlow patterns
- **Enhancement flags:** `--with-streaming`, `--with-hitl`, `--with-a2a`, `--no-memory`
- See [TEMPLATING_QUICKGUIDE.md](TEMPLATING_QUICKGUIDE.md) for complete documentation

**v2.4 Planner Refinements (included):**
- Explicit `llm_context` vs `tool_context` split; fail-fast on non-JSON `llm_context`
- `ToolContext` protocol for typed tools (`ctx.pause`, `ctx.emit_chunk`, `ctx.tool_context`)
- Explicit join injection for parallel plans; examples in `examples/react_parallel_join`
- Fresh docs: `REACT_PLANNER_INTEGRATION_GUIDE.md`, `docs/MIGRATION_V24.md`

### CLI Quickstart

```bash
# Project scaffolding
uv run penguiflow new my-agent --template react        # ReactPlanner template (supports built-in short-term memory)
uv run penguiflow new my-agent --template enterprise   # Multi-tenant enterprise setup
uv run penguiflow new my-agent --template parallel --with-streaming  # Parallel + SSE

# VS Code configuration
uv run penguiflow init             # create .vscode snippets/launch/tasks/settings
uv run penguiflow init --dry-run   # preview without writing files
uv run penguiflow init --force     # overwrite existing files

# Launch the interactive playground
uv run penguiflow dev              # opens browser at http://127.0.0.1:8001
```

### Interactive Playground

PenguiFlow includes a **browser-based development environment** for testing and debugging agents in real-time:

```bash
penguiflow dev --project-root .
```

The playground automatically discovers your agent (orchestrator class or `build_planner` function) and provides:

* **Real-time chat** with streaming responses and LLM token display
* **Trajectory visualization** showing step-by-step execution with thoughts, tool calls, arguments, and results
* **Event inspector** for debugging planner decisions and timing
* **Context editors** for configuring `llm_context` and `tool_context` at runtime
* **Spec validation** for YAML agent definitions with inline error reporting
* **Multi-session support** with isolated state per session

The UI streams events via SSE, displaying:
- `llm_stream_chunk` — real-time LLM token streaming (thinking, action, answer phases)
- `step` — step boundaries with node name, latency, and thought summaries
- `artifact_chunk` — structured artifacts as they're generated
- `done` — final answer with metadata, pause state, and cost breakdown

See `docs/PLAYGROUND_DEV.md` for backend contracts and customization options.

Built on pure `asyncio` (no threads), PenguiFlow is small, predictable, and repo-agnostic.
Product repos only define **their models + node functions** — the core stays dependency-light.

## Gold Standard Scorecard

| Area | Metric | Target | Current |
| --- | --- | --- | --- |
| Hop overhead | µs per hop | ≤ 500 | 398 |
| Streaming order | gaps/dupes | 0 | 0 |
| Cancel leakage | orphan tasks | 0 | 0 |
| Coverage | lines | ≥85% | 86% |
| Deps | count | ≤2 | 2 |
| Import time | ms | ≤220 | 203 |

## 📑 Core Behavior Spec

* [Core Behavior Spec](docs/core_behavior_spec.md) — single-page rundown of ordering,
  streaming, cancellation, deadline, and fan-in invariants with pointers to regression
  tests.

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

> **Opt-in distribution:** pass `state_store=` and/or `message_bus=` when calling
> `penguiflow.core.create(...)` to persist trace history and publish floe traffic
> without changing node logic.

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

## 🛠️ Key capabilities

### Streaming & incremental delivery

`Context.emit_chunk` (and `PenguiFlow.emit_chunk`) provide token-level streaming without
sacrificing backpressure or ordering guarantees.  The helper wraps the payload in a
`StreamChunk`, mirrors routing metadata from the parent message, and automatically
increments per-stream sequence numbers.  See `tests/test_streaming.py` and
`examples/streaming_llm/` for an end-to-end walk-through.

### Remote orchestration

Phase 2 introduces `RemoteNode` and the `RemoteTransport` protocol so flows can delegate
work to remote agents (e.g., the A2A JSON-RPC/SSE ecosystem) without changing existing
nodes.  The helper records remote bindings via the `StateStore`, mirrors streaming
partials back into the graph, and propagates per-trace cancellation to remote tasks via
`RemoteTransport.cancel`.  See `tests/test_remote.py` for reference in-memory transports.

### Exposing a flow over A2A

Install the optional extra to expose PenguiFlow as an A2A-compatible FastAPI service:

```bash
pip install "penguiflow[a2a-server]"
```

Create the adapter and mount the routes:

```python
from penguiflow import Message, Node, create
from penguiflow_a2a import A2AAgentCard, A2AServerAdapter, A2ASkill, create_a2a_app

async def orchestrate(message: Message, ctx):
    await ctx.emit_chunk(parent=message, text="thinking...")
    return {"result": "done"}

node = Node(orchestrate, name="main")
flow = create(node.to())

card = A2AAgentCard(
    name="Main Agent",
    description="Primary entrypoint for orchestration",
    version="2.1.0",
    skills=[A2ASkill(name="orchestrate", description="Handles orchestration")],
)

adapter = A2AServerAdapter(
    flow,
    agent_card=card,
    agent_url="https://agent.example",
)
app = create_a2a_app(adapter)
```

The generated FastAPI app implements:

* `GET /agent` for discovery (Agent Card)
* `POST /message/send` for unary execution
* `POST /message/stream` for SSE streaming
* `POST /tasks/cancel` to mirror cancellation into PenguiFlow traces

`A2AServerAdapter` reuses the runtime's `StateStore` hooks, so bindings between trace IDs
and external `taskId`/`contextId` pairs are persisted automatically.

### Reliability & guardrails

PenguiFlow enforces reliability boundaries out of the box:

* **Per-trace cancellation** (`PenguiFlow.cancel(trace_id)`) unwinds a single run while
  other traces keep executing.  Worker tasks observe `TraceCancelled` and clean up
  resources; `tests/test_cancel.py` covers the behaviour.
* **Deadlines & budgets** let you keep loops honest.  `Message.deadline_s` guards
  wall-clock execution, while controller payloads (`WM`) track hop and token budgets.
  Exhaustion short-circuits into terminal `FinalAnswer` messages as demonstrated in
  `tests/test_budgets.py` and `examples/controller_multihop/`.
* **Retries & timeouts** live in `NodePolicy`.  Exponential backoff, timeout enforcement,
  and structured retry events are exercised heavily in the core test suite.

### Metadata & observability

Every `Message` carries a mutable `meta` dictionary so nodes can propagate debugging
breadcrumbs, billing information, or routing hints without touching the payload.  The
runtime clones metadata during streaming and playbook calls (`tests/test_metadata.py`).
Structured runtime events surface through `FlowEvent` objects; attach middlewares for
custom logging or metrics ingestion (`examples/mlflow_metrics/`).

### Routing & dynamic policies

Branching flows stay flexible thanks to routers and optional policies.  The
`predicate_router` and `union_router` helpers can consult a `RoutingPolicy` at runtime to
override or drop successors, while `DictRoutingPolicy` provides a config-driven
implementation ready for JSON/YAML/env inputs (`tests/test_routing_policy.py`,
`examples/routing_policy/`).

### Traceable exceptions

When retries are exhausted or timeouts fire, PenguiFlow wraps the failure in a
`FlowError` that preserves the trace id, node metadata, and a stable error code.
Opt into `emit_errors_to_rookery=True` to receive these objects directly from
`flow.fetch()`—see `tests/test_errors.py` and `examples/traceable_errors/` for usage.

### FlowTestKit

The new `penguiflow.testkit` module keeps unit tests tiny:

* `await testkit.run_one(flow, message)` boots a flow, emits a message, captures runtime
  events, and returns the first Rookery payload.
* `testkit.assert_node_sequence(trace_id, [...])` asserts the order in which nodes ran.
* `testkit.simulate_error(...)` builds coroutine helpers that fail a configurable number
  of times—perfect for retry scenarios.

The harness is covered by `tests/test_testkit.py` and demonstrated in
`examples/testkit_demo/`.

### External Tool Integration (ToolNode)

Connect ReactPlanner to external services via **MCP** (Model Context Protocol), **UTCP**, or **HTTP** with unified authentication and resilience:

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType, AuthType

config = ExternalToolConfig(
    name="github",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-github",
    auth_type=AuthType.OAUTH2_USER,
    timeout_s=30,
    max_concurrency=10,
)

tool_node = ToolNode(config=config, registry=registry)
await tool_node.connect()

# Discovered tools are namespaced: github.create_issue, github.search_repos, etc.
specs = tool_node.get_tool_specs()

# Add external tools to planner catalog alongside local tools
planner = ReactPlanner(llm="gpt-4o", catalog=specs + local_tools)
```

**Supported transports:**
- **MCP** — FastMCP servers (stdio or HTTP)
- **UTCP** — Universal Tool Calling Protocol endpoints
- **HTTP** — REST APIs with JSON schema discovery

**Authentication types:**
- `NONE` — No authentication
- `API_KEY` — Header injection (configurable header name)
- `BEARER` — Authorization header with Bearer token
- `OAUTH2_USER` — User-level OAuth with HITL pause/resume for consent

**Built-in resilience:**
- Exponential backoff retries with tenacity (configurable min/max)
- Timeout protection via `asyncio.timeout()`
- Semaphore-based concurrency limiting (default 10 concurrent calls)
- Smart retry classification: 429/5xx = retry, 4xx = no retry
- Event loop awareness for automatic reconnection

**Error hierarchy:**
- `ToolNodeError` (base) with `is_retryable` classification
- `ToolAuthError` (401, 403), `ToolServerError` (5xx), `ToolRateLimitError` (429)
- `ToolClientError` (4xx), `ToolConnectionError`, `ToolTimeoutError`

CLI helpers for testing tool connections:
```bash
penguiflow tools list                      # List available presets
penguiflow tools connect github --discover # Test connection and discover tools
```

### React Planner - LLM-Driven Orchestration

Build autonomous agents that select and execute tools dynamically using the ReAct (Reasoning + Acting) pattern:

```python
from penguiflow import ReactPlanner, tool, build_catalog

@tool(desc="Search documentation")
async def search_docs(args: Query, ctx) -> Documents:
    return Documents(results=await search(args.text))

@tool(desc="Summarize results")
async def summarize(args: Documents, ctx) -> Summary:
    return Summary(text=await llm_summarize(args.results))

planner = ReactPlanner(
    llm="gpt-4",
    catalog=build_catalog([search_docs, summarize], registry),
    max_iters=10
)

result = await planner.run("Explain PenguiFlow routing")
print(result.payload)  # LLM orchestrated search → summarize automatically
```

**Key capabilities:**

* **Autonomous tool selection** — LLM decides which tools to call and in what order based on your query
* **Type-safe execution** — All tool inputs/outputs validated with Pydantic, JSON schemas auto-generated from models
* **Parallel execution** — LLM can fan out to multiple tools concurrently with automatic result joining
* **Pause/resume workflows** — Add approval gates with `await ctx.pause()`, resume later with user input
* **Adaptive replanning** — Tool failures feed structured error suggestions back to LLM for recovery
* **Constraint enforcement** — Set hop budgets, deadlines, and token limits to prevent runaway execution
* **Planning hints** — Guide LLM behavior with ordering preferences, parallel groups, and tool filters
* **Policy-based tool filtering** — Restrict catalog visibility per tenant, role, or safety requirement with `ToolPolicy`

### Policy-Based Tool Filtering

Apply runtime guardrails to the planner's tool catalog using `ToolPolicy`. This
lets you tailor availability by tenant tier, user permissions, or safety
tags without modifying the underlying nodes.

```python
from penguiflow.planner import ReactPlanner, ToolPolicy

policy_free = ToolPolicy(allowed_tools={"search_public", "summarise"})
policy_premium = ToolPolicy(denied_tools={"delete_user"}, require_tags={"safe"})

planner_free = ReactPlanner(..., tool_policy=policy_free)
planner_premium = ReactPlanner(..., tool_policy=policy_premium)

print(planner_free._spec_by_name.keys())   # {'search_public', 'summarise'}
print(planner_premium._spec_by_name.keys())  # filtered catalog
```

Policies evaluate in the following order:

1. `denied_tools`
2. `allowed_tools` (if provided)
3. `require_tags` (must be present on the tool)

Any tool failing these checks is removed before prompt construction, and the
planner logs the filtered names for observability. Combine this with stored
tenant settings or role metadata to enforce enterprise-grade boundaries.

### Reflection Loop (Quality Assurance)

PenguiFlow's ReactPlanner now includes an optional **reflection loop** that critiques candidate answers before finishing. This
prevents the LLM from prematurely declaring success when critical requirements remain unsatisfied.

Enable the loop with a `ReflectionConfig`:

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig, ReflectionCriteria

planner = ReactPlanner(
    llm="gpt-4",
    catalog=build_catalog([search_docs, summarize], registry),
    reflection_config=ReflectionConfig(
        enabled=True,
        criteria=ReflectionCriteria(
            completeness="Addresses all aspects of the user's query",
            accuracy="Grounds statements in verified observations",
            clarity="Explains reasoning clearly",
        ),
        quality_threshold=0.85,
        max_revisions=2,
        use_separate_llm=True,
    ),
    reflection_llm="gpt-4o-mini",
)

result = await planner.run("Explain how PenguiFlow handles errors in parallel execution")
print(result.metadata["reflection"])  # => {'score': 0.92, 'revisions': 1, 'passed': True, 'feedback': '...'}
```

**Benefits:**

* ✅ Prevents incomplete answers — planner loops until the critique score meets your threshold or max revisions are reached
* ✅ Observable — every critique emits a `PlannerEvent` with score, pass flag, and truncated feedback
* ✅ Cost-aware — reuse the main LLM or provide a cheaper `reflection_llm` for critiques
* ✅ Budget-safe — revisions respect hop and deadline budgets; no runaway loops

### Cost Tracking

ReactPlanner automatically records LLM spend for every planning session. Costs are split across planner actions, reflection calls, and trajectory summarisation so you can monitor budgets in production.

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=build_catalog([search_docs, summarize], registry),
    reflection_config=ReflectionConfig(enabled=True, max_revisions=2),
    reflection_llm="gpt-4o-mini",  # cheaper critique model
)

result = await planner.run("Analyse onboarding friction across regions")

cost = result.metadata["cost"]
print(f"Total cost: ${cost['total_cost_usd']:.4f}")
print(f"Planner calls: {cost['main_llm_calls']}")
print(f"Reflections: {cost['reflection_llm_calls']}")
print(f"Summaries: {cost['summarizer_llm_calls']}")
```

Hook into planner events to emit metrics or alerts when sessions exceed your budget:

```python
from penguiflow.planner.react import PlannerEvent

def track_costs(event: PlannerEvent) -> None:
    if event.event_type != "finish":
        return
    session_cost = event.extra.get("cost", {}).get("total_cost_usd", 0.0)
    if session_cost > 0.10:
        logger.warning("high_cost_session", extra={"cost_usd": session_cost})

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=build_catalog([search_docs, summarize], registry),
    event_callback=track_costs,
)
```

### Short-Term Memory

Enable conversation continuity across turns with opt-in session memory. Memory is isolated per session using composite `MemoryKey` (tenant + user + session):

```python
from penguiflow.planner import ReactPlanner, ShortTermMemoryConfig, MemoryBudget, MemoryKey

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",  # or "truncation", "none"
        budget=MemoryBudget(
            full_zone_turns=5,        # Recent turns kept in full
            summary_max_tokens=1000,  # Max summary size
            total_max_tokens=8000,    # Overall cap
            overflow_policy="truncate_oldest",  # or "truncate_summary", "error"
        ),
    ),
)

# Session-scoped memory with tenant isolation
key = MemoryKey(tenant_id="acme", user_id="user123", session_id="sess-abc")
result = await planner.run("What did we discuss earlier?", memory_key=key)
```

**Strategies:**
- **`truncation`** — Keep last N turns only (deterministic, low-latency, cost-effective)
- **`rolling_summary`** — Compress older turns into summaries via background summarization (maintains long context)
- **`none`** — Stateless operation (default)

**Safety features:**
- **Fail-closed isolation** — `require_explicit_key=True` prevents accidental cross-session leakage
- **Background summarization** — Non-blocking; doesn't delay responses
- **Graceful degradation** — Summarizer failures fall back to truncation mode
- **Health states** — `HEALTHY`, `RETRY`, `DEGRADED`, `RECOVERING` for observability

**Memory context injection:**
```python
# Memory is injected as a separate system message with safety preamble
{
  "conversation_memory": {
    "recent_turns": [...],      # Full turns in the "full zone"
    "pending_turns": [...],     # Turns awaiting summarization
    "summary": "..."            # Compressed history
  }
}
```

**Persistence:**
```python
# Persist across process restarts via duck-typed store
await memory.persist(state_store, key.composite())
await memory.hydrate(state_store, key.composite())
```

**Observability callbacks:**
```python
ShortTermMemoryConfig(
    on_turn_added=lambda turn: log(turn),
    on_summary_updated=lambda summary: log(summary),
    on_health_changed=lambda old, new: alert(old, new),
)
```

See `docs/MEMORY_GUIDE.md` for complete configuration and `examples/memory_basic/` through `examples/memory_custom/` for usage patterns.

### Streaming Planner Responses

ReactPlanner tools can emit **streaming chunks** mid-execution. Each call to
`ctx.emit_chunk` is persisted on the trajectory and surfaced through
`PlannerEvent(event_type="stream_chunk")`, so downstream UIs can render partial
progress as soon as it is available.

```python
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerEvent, ReactPlanner
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    question: str


class Answer(BaseModel):
    answer: str


@tool(desc="Stream answer token-by-token")
async def stream_answer(args: Query, ctx) -> Answer:
    tokens = ["PenguiFlow", "is", "a", "typed", "async", "planner"]
    for index, token in enumerate(tokens):
        await ctx.emit_chunk("answer_stream", index, f"{token} ", done=False)

    await ctx.emit_chunk("answer_stream", len(tokens), "", done=True)
    return Answer(answer=" ".join(tokens))


def handle_stream(event: PlannerEvent) -> None:
    if event.event_type == "stream_chunk":
        print(event.extra["text"], end="", flush=True)
        if event.extra["done"]:
            print()


registry = ModelRegistry()
registry.register("stream_answer", Query, Answer)


planner = ReactPlanner(
    llm="gpt-4o-mini",
    catalog=build_catalog([stream_answer], registry),
    event_callback=handle_stream,
)

result = await planner.run("Tell me about PenguiFlow")
print(result.metadata["steps"][0]["streams"]["answer_stream"])  # structured chunks
```

**Model support:**
* Install `penguiflow[planner]` for LiteLLM integration (100+ models: OpenAI, Anthropic, Azure, etc.)
* Or inject a custom `llm_client` for deterministic/offline testing

**Examples:**
* `examples/react_minimal/` — Basic sequential flow with stub LLM
* `examples/react_parallel/` — Parallel shard fan-out with join node
* `examples/react_pause_resume/` — Approval workflow with planning hints
* `examples/react_replan/` — Adaptive recovery from tool failures

See **manual.md Section 19** for complete documentation.


## 🧭 Repo Structure

penguiflow/
  __init__.py
  core.py          # runtime orchestrator, retries, controller helpers, playbooks
  errors.py        # FlowError / FlowErrorCode definitions
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
- `predicate_router(name, predicate, policy=None)` — route messages to successor nodes
  based on simple boolean functions over payload or headers, optionally consulting a
  runtime `policy` to override or filter the computed targets. Perfect for guardrails or
  conditional tool invocation without rebuilding the flow.
- `union_router(name, discriminated_model)` — accept a Pydantic discriminated union and
  forward each variant to the matching typed successor node. Keeps type-safety even when
  multiple schema branches exist.
- `join_k(name, k)` — aggregate `k` messages per `trace_id` before resuming downstream
  work. Useful for fan-out/fan-in batching, map-reduce style summarization, or consensus.
- `DictRoutingPolicy(mapping, key_getter=None)` — load routing overrides from
  configuration and pair it with the router helpers via `policy=...` to switch routing at
  runtime without modifying the flow graph.

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
* **Remote call telemetry**: `RemoteNode` executions emit extra metrics (latency, request
  and response bytes, context/task identifiers, cancel reasons) so remote hops can be
  traced end-to-end.
* **Middleware hooks**: subscribe observers (e.g., MLflow) to the structured `FlowEvent`
  stream. See `examples/mlflow_metrics/` for an MLflow integration and
  `examples/reliability_middleware/` for a concrete timeout + retry walkthrough.
* **`penguiflow-admin` CLI**: inspect or replay stored trace history from any configured
  `StateStore` (`penguiflow-admin history <trace>` or `penguiflow-admin replay <trace>`)
  when debugging distributed runs.

---

## ⚠️ Current Constraints

- **In-process runtime**: there is no built-in distribution layer yet. Long-running CPU work should be delegated to your own pools or services.
- **Registry-driven typing**: nodes default to validation. Provide a `ModelRegistry` when calling `flow.run(...)` or set `validate="none"` explicitly for untyped hops.
- **Observability**: structured `FlowEvent` callbacks and the `penguiflow-admin` CLI power
  local debugging; integrations with third-party stacks (OTel, Prometheus, Datadog) remain
  DIY. See the MLflow middleware example for a lightweight pattern.
- **Roadmap**: follow-up releases focus on optional distributed backends, deeper observability integrations, and additional playbook patterns. Contributions and proposals are welcome!

---

## 📊 Benchmarks

Lightweight benchmarks live under `benchmarks/`. Run them via `uv run python benchmarks/<name>.py`
to capture baselines for fan-out throughput, retry/timeout overhead, and controller
playbook latency. Copy them into product repos to watch for regressions over time.

---

## 🔮 Roadmap

* **v2.7 (current)**: Interactive Playground, External Tool Integration (ToolNode), Short-Term Memory with multi-tenant isolation.
* **v2.6**: Streaming support with `stream` and `on_stream_chunk` parameters in `JSONLLMClient` protocol.
* **v2.5**: CLI scaffolding system with 9 templates and enhancement flags, extended ReactPlanner with ToolContext protocol and explicit context splits.
* **v2.x**: per-trace cancellation, deadlines/budgets, metadata propagation, observability hooks, visualizer, routing policies, traceable errors, and FlowTestKit.
* **Future**: optional distributed runners, richer third-party observability adapters, and extended template library.

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
* `examples/roadmap_status_updates/`: roadmap-aware agent scaffold that streams status updates and final chunks.
* `examples/status_roadmap_flow/`: roadmap-driven websocket status updates with FlowResponse scaffolding.
* `examples/react_minimal/`: JSON-only ReactPlanner loop with a stubbed LLM.
* `examples/react_pause_resume/`: Phase B planner features with pause/resume and developer hints.
* `examples/policy_filtering/`: tenant-aware planner with runtime `ToolPolicy` filtering.
* `examples/memory_basic/`: short-term memory with rolling summary strategy.
* `examples/memory_truncation/`: truncation strategy for cost-effective memory.
* `examples/memory_persistence/`: cross-process memory continuity via state store.
* `examples/memory_redis/`: production-ready Redis-based memory persistence.
* `examples/memory_callbacks/`: observability hooks for memory events.
* `examples/memory_custom/`: custom `ShortTermMemory` implementation.


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
