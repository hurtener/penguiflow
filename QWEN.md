## Vision

Build **penguiflow**: a mature, production-ready Python library that orchestrates async agent pipelines with:

* **Typed messages** (Pydantic v2),
* **Concurrent fan-out / fan-in**,
* **Routing/decision points**, and
* **Dynamic multi-hop controller loops** + **runtime subflows (playbooks)**,
* **LLM-driven orchestration** via `ReactPlanner` with autonomous tool selection,
* **Short-term memory** for conversation continuity,
* **External tool integration** (MCP/UTCP/HTTP),
* **Streaming capabilities** for real-time responses,
* **Distributed orchestration** with optional `StateStore` persistence,
* **Interactive playground** for development and debugging,
* **A2A server adapter** for remote agent communication,
* **Policy-driven routing** and access control,
* **Rich output tooling** for UI components,
* **Human-in-the-loop** capabilities for approval workflows,
* **Artifact store** for binary content (PDFs, images, large files),

…with strong reliability (backpressure, retries, timeouts, graceful stop) and comprehensive observability (structured logs, metrics, MLflow hooks).

## Non-Goals

* Heavy scheduling/cron features (out of scope).
* Built-in UI/console beyond logs & examples (interactive playground available separately).

## Repo Layout (current)

```
penguiflow/
  __init__.py
  admin.py         # Admin CLI commands
  artifacts.py     # Artifact storage and management
  bus.py           # Message bus for distributed flows
  catalog.py       # Tool catalog and specifications
  core.py          # Context, Floe, PenguiFlow (runtime)
  debug.py         # Debug utilities
  errors.py        # Flow error definitions
  logging.py       # Structured logging
  metrics.py       # Flow event metrics
  middlewares.py   # Middleware implementations
  node.py          # Node, NodePolicy
  patterns.py      # Routing and concurrency patterns
  policies.py      # Routing policies
  planner/         # ReactPlanner and related components
  registry.py      # ModelRegistry (TypeAdapters per node)
  remote.py        # Remote node and transport
  sessions/        # Session management
  state/           # State store protocol and implementations
  steering.py      # Steering event handling
  streaming.py     # Streaming utilities
  templates/       # CLI scaffolding templates
  testkit.py       # Testing utilities
  tools/           # Tool integration utilities
  types.py         # Message, Headers (Pydantic models)
  viz.py           # DOT/Mermaid exporter
  README.md        # developer guide + quickstart
  cli/             # Command-line interface
  agui_adapter/    # AGUI protocol adapter
  llm/             # LLM client implementations
  rich_output/     # Rich output components
examples/
  quickstart/
  routing_predicate/
  routing_union/
  fanout_join/
  controller_multihop/
  playbook_retrieval/
  react_parallel_join/
  streaming_llm/
  mlflow_metrics/
  testkit_demo/
  ...
tests/
  test_core.py
  test_types.py
  test_registry.py
  test_patterns.py
  test_controller.py
  test_planner.py
  test_remote.py
  test_streaming.py
  test_cancel.py
  test_errors.py
  test_routing_policy.py
  test_memory.py
  ...
pyproject.toml
```

## Current Implementation Status

### Phase 0 — ✅ Complete
Package structure and tooling are fully implemented.

### Phase 1 — ✅ Complete
Safe Core Runtime is fully implemented with:
* `Floe.queue = asyncio.Queue(maxsize=DEFAULT_MAXSIZE)` (configurable).
* `Context.fetch_any()` uses `asyncio.wait` (no busy-wait).
* `PenguiFlow.stop()` cancels **and awaits** tasks (graceful).
* Error boundaries around each node task (structured logs).
* Stable `node_id` (uuid) + human `name`.
* **Cycle detection** (toposort). Allow opt-in cycles via `allow_cycle` flag.
* `emit(msg, to=[...])` selective emission (keep existing semantics).
* Per-trace cancellation with `PenguiFlow.cancel(trace_id)` and `TraceCancelled` exception.
* Deadlines and budgets (`Message.deadline_s`, `WM.budget_hops`, `WM.budget_tokens`).

### Phase 2 — ✅ Complete
Pydantic Type-Safety & Registry is fully implemented with:
* `Headers` and `Message` models with proper validation.
* `ModelRegistry` with cached `TypeAdapter`s per node.
* `NodePolicy` with validation options (`validate="both"|"in"|"out"|"none"`).

### Phase 3 — ✅ Complete
Reliability & Observability is fully implemented with:
* Retries with exponential backoff per `NodePolicy`.
* Timeouts per node (`asyncio.wait_for`).
* Structured logs with fields: `{ts, level, node_name, node_id, event, trace_id, latency_ms, q_depth_in, attempt}`.
* Middleware hook points for MLflow and custom metrics.
* Comprehensive error handling with `FlowError` objects.

### Phase 4 — ✅ Complete
Concurrency, Routing & Patterns is fully implemented with:
* `map_concurrent(items, worker, max_concurrency=8)` (semaphore-bounded).
* `join_k(name, k)` Node: aggregate K messages per `trace_id`.
* **Routers**:
  * Predicate router: emits to selected successors based on payload/headers.
  * Union router: use discriminated unions; successor nodes typed to each variant.
* **Policy-driven routing**: Optional policies can override or filter routing decisions.

### Phase 5 — ✅ Complete
Dynamic Multi-Hop Controller Loop is fully implemented with:
* Support for controller nodes with opt-in cycles via `allow_cycle` flag.
* Controller returns `WM` → loops; `FinalAnswer` → goes to Rookery.
* Enforced budgets: hops, deadline, tokens.
* `ReactPlanner` for LLM-driven orchestration with autonomous tool selection.

### Phase 6 — ✅ Complete
Subflows ("Playbooks") is fully implemented with:
* `call_playbook()` helper for subflow execution.
* Subflows inherit trace_id/headers and return first Rookery payload.
* Proper cancellation propagation between parent and child flows.

## Additional Implemented Features

### React Planner (LLM-Driven Orchestration)
* Autonomous tool selection based on query and context
* Type-safe execution with Pydantic validation
* Parallel execution with automatic result joining
* Pause/resume workflows with `await ctx.pause()`
* Adaptive replanning with error feedback to LLM
* Constraint enforcement with hop budgets, deadlines, and token limits
* Planning hints and policy-based tool filtering

### External Tool Integration (ToolNode)
* Unified MCP/UTCP/HTTP tool connections with authentication
* Built-in resilience with exponential backoff and timeout protection
* Semaphore-based concurrency limiting
* Smart retry classification (429/5xx = retry, 4xx = no retry)
* CLI helpers for testing tool connections

### Streaming & Incremental Delivery
* `Context.emit_chunk()` for token-level streaming
* Backpressure and ordering guarantees preserved
* Per-stream sequence number management
* SSE streaming support for real-time responses

### Short-Term Memory
* Opt-in session memory with tenant/user/session isolation
* Multiple strategies: truncation, rolling summary
* Memory budgets and overflow policies
* Optional persistence via StateStore

### Distribution Hooks
* Pluggable `StateStore` protocol for trace history persistence
* `MessageBus` for publishing floe traffic to remote workers
* `RemoteNode` bridges for external agent delegation
* A2A server adapter for FastAPI service exposure

### Interactive Playground
* Browser-based development environment
* Real-time chat with streaming responses
* Trajectory visualization with step-by-step execution
* Event inspector for debugging
* Context editors for runtime configuration

### CLI Scaffolding
* `penguiflow new` command with 9 project templates
* Tier 1: `minimal`, `react`, `parallel` - foundational patterns
* Tier 2: `rag_server`, `wayfinder`, `analyst` - domain-ready agents
* Tier 3: `enterprise` - multi-tenant with RBAC, quotas, audit trails
* Enhancement flags: `--with-streaming`, `--with-hitl`, `--with-a2a`, etc.

### Rich Output & Artifact Store
* Rich output tooling for UI components (charts, reports, grids)
* Binary content storage (PDFs, images, large files)
* Artifact references with retention policies
* Accessible via Playground UI and REST API

### Human-in-the-Loop (HITL)
* Pause/resume workflows with approval gates
* OAuth integration for consent flows
* User input collection during execution
* Multi-step approval processes

## Coding Standards

* Python ≥ 3.11, Pydantic ≥ 2.6.
* Async only (`asyncio`); no threads.
* Tests with `pytest`; maintain ~85%+ core coverage.
* Lint with `ruff`; type-check with `mypy`.
* Use `uv` for environment management.
* Every new feature must have: unit tests + a runnable example.

## Performance Notes

* Cache `TypeAdapter`s (registry).
* Allow `validate="in"` or `"none"` in hot nodes.
* Use `maxsize` queues; expose config in `PenguiFlow` ctor.
* Streaming chunks preserve backpressure and ordering guarantees.
* Efficient cancellation propagation without resource leaks.

