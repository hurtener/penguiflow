# PenguiFlow Evolution Plan

## v2.1 — Distributed & Agent-to-Agent (COMPLETED ✅)

### Vision (Achieved)

PenguiFlow v2.1 successfully evolved into a lightweight, repo-agnostic orchestration library that:

* Maintains v2's async core, type-safety, reliability (backpressure, retries, timeouts, graceful stop), routing, controller loops, subflows, and **streaming**
* Adds **opt-in** hooks for distributed execution and inter-agent calls without bloating the core:

  * **StateStore** (shared brain) — durable run history & correlation
  * **MessageBus** (shared nervous system) — distributed edges between nodes/workers
  * **RemoteTransport** (tiny seam) — optional HTTP/A2A client to call external agents
  * **A2A Server Adapter** — FastAPI-based adapter to expose PenguiFlow flows as A2A agents

### Key Features Delivered

* **Remote Agent Integration**: `RemoteNode` for calling external agents
* **Distributed Observability**: Full telemetry for remote calls with `StateStore` persistence
* **Streaming & Cancellation Handshake**: Proper propagation across agent boundaries
* **A2A Compliance**: Full `message/send`, `message/stream`, `tasks/cancel` surface
* **CLI Tools**: `penguiflow-admin history` and `replay` commands

Remains **asyncio-only** and **Pydantic v2**. No heavy deps. Users bring their own backends.

---

## v2 Foundation (Stable)

Current stable features:

* **Streaming support** (token/partial results with `Context.emit_chunk`)
* **Per-trace cancellation**
* **Deadlines & budgets**
* **Message metadata propagation**
* **Observability hooks** (FlowEvent)
* **Flow visualizer** (Mermaid/DOT)
* **Dynamic routing by policy**
* **Traceable exceptions** (FlowError)
* **Testing harness (FlowTestKit)**

---

## v2.5 — ReAct Planner (NEXT) 🎯

### Vision

Add a **lightweight, typed, product-agnostic planner** that chooses & sequences PenguiFlow nodes/recipes using a JSON-only protocol powered by LiteLLM.

### Goals

* **Planner chooses PenguiFlow Nodes/Recipes** (tools) at runtime
* **Typed I/O**: strict JSON contracts from your Node schemas (Pydantic v2)
* **Reliability**: use existing NodeRunner semantics (retries, exponential backoff, timeouts, cancellation)
* **Pause/Resume**: approvals / deferred work (persisted via `StateStore` hook; in-proc fallback)
* **Adaptive re-planning**: detect failures/assumption breaks and request constrained plan updates
* **Token-aware**: compact trajectory state with structured summaries
* **LLM-agnostic** via **LiteLLM**; JSON-only responses (no free-form ReAct logs)
* **Library-only**: no endpoints, UI, or storage; provide hooks only

### Non-Goals

* Not a DSL or training framework (no DSPy-style signature graph)
* No built-in database, broker, or scheduler (use v2.1 `StateStore` / `MessageBus` when present)
* No endpoint/UI scaffolding (samples may show FastAPI glue in `examples/` only)

---

## Architecture Overview (v2.5)

```
User Query
   └─► ReactPlanner (LiteLLM JSON)
         ├─ Catalog: [NodeSpec...]  ← (auto from Node + registry)
         ├─ Trajectory: [Step{node,args,observation,summary}]
         ├─ Policies: budgets, max_iters, parallelism
         └─► NodeRunner
               ├─ retries / backoff / timeouts
               └─ emits observations (+ StreamChunks)
```

### Key Components

**NodeSpec** (derived from existing Nodes):

```json
{
  "name": "retrieve_docs",
  "description": "Fetch k docs by topic",
  "input_schema": { ... JSON Schema from Pydantic ... },
  "output_schema": { ... }
}
```

**Planner contract** (LLM output):

```json
{"thought":"...", "next_node":"retrieve_docs", "args":{"topic":"metrics", "k":5}}
```

---

## v2.5 Implementation Phases

### Phase A — JSON-only ReAct Loop

**Objective**: Implement minimal, deterministic loop: *user query → choose node (JSON) → run node → record observation → repeat (bounded)*.

**Deliverables**:

1. `penguiflow/planner/react.py`
   * `class ReactPlanner` with `__init__`, `run`, `step` methods
   * JSON coercion & validation (Pydantic) for `PlannerAction`
   * Node catalog builder (introspect Node/Recipe → description + in/out JSON Schema)
   * LiteLLM client shim (strict JSON response)

2. **Prompt Templates** (`planner/prompts.py`)
   * System prompt: objectives, rules, JSON-only format, available nodes with compact schemas
   * Few-shot mini examples (optional)

3. **Trajectory model** (`planner/types.py`)
   * `TrajectoryStep`: `{thought, node, args, observation, error?, tokens?, latency_ms?}`
   * `Trajectory`: bounded list + stats

**Algorithm**:

1. Seed trajectory with user query
2. Ask LLM for `{thought,next_node,args}` (JSON only)
3. Validate `args` against Node's **input schema**; if invalid, repair (max 2 repairs)
4. Execute Node via NodeRunner (inherits retries/backoff/timeouts)
5. Append observation (coerced to `out` schema); loop until `finish`/`stop`/`max_iters`

**Testing**:

* Unit: JSON parsing & schema validation (good/invalid/repair), node failures, deterministic output
* Integration: Example flow `triage → retrieve → summarize`, streaming with `StreamChunk`

**Examples**: `examples/react_minimal/` - 3 nodes + planner; run once; prints final result & trajectory

**Acceptance**:

* Planner completes within `max_iters` for happy-path scenario
* On invalid args, it self-repairs JSON and proceeds
* No free-form tool logs: planner and LLM exchange **strict JSON**

---

### Phase B — Trajectory Summarization + Pause/Resume

**Objective**: Keep token footprint low and enable approvals / long-running handoffs.

**Deliverables**:

1. **Token-aware summarizer**
   * `Trajectory.compress()` creates structured mini-state
   * Prompts include **summary** instead of full logs when over budget
   * **Optional cheaper LLM** for summarization/compaction to cut cost

2. **Pause/Resume hooks**
   * `ReactPlanner.pause(reason, payload)` returns `PlannerPause` result
   * `ReactPlanner.resume(resume_token, user_input)` continues from compressed state
   * Storage: in-proc dict; if v2.1 `StateStore` provided, use it for durability (opt-in)

3. **Approvals pattern**
   * Inject "policy node" requiring human approval; planner yields `PlannerPause(approval_required)`

4. **Developer Prompt Hints**
   * Configurable **system prompt extension** merged into planner's system prompt
   * Optional **structured hints** for ordering, parallelism, constraints:

```json
{
  "ordering_hints": ["triage", "retrieve_docs", "rerank", "summarize"],
  "parallel_groups": [["retrieve_docs_A","retrieve_docs_B"], ["rerank"]],
  "sequential_only": ["apply_compliance","send_email"],
  "disallow_nodes": ["expensive_tool_v1"],
  "prefer_nodes": ["cached_search"],
  "budget_hints": {"max_parallel": 3, "max_cost_usd": 0.10}
}
```

**Testing**:

* Summarization: ensures re-planning quality remains high after compression
* Pause/Resume: serialize → restore → continue; deterministic outcome

**Examples**: `examples/react_pause_resume/` - approval workflow with cheaper summarizer LLM

**Acceptance**:

* Prompts stay under configurable token budget
* Resumed runs preserve constraints and state

---

### Phase C — Adaptive Re-Planning (Error Feedback)

**Objective**: When execution fails after retries or assumptions break, request constrained, minimal re-plan.

**Deliverables**:

1. **Error channel to LLM**
   * On Node failure (after NodeRunner retries), send structured error feedback
   * Prompt: "Propose revised next action that **respects budgets** and avoids failure cause"

2. **Constraint manager**
   * Hard limits: wall-clock deadline, hop budget, token budget
   * Planner refuses plans violating constraints; asks for another revision (max N)

3. **Finish conditions**
   * `finish(reason="answer_complete" | "no_path" | "budget_exhausted")` with typed final payload

**Testing**:

* Simulate transient and permanent failures; verify re-plan path changes
* Verify constraints enforcement (deadline/hops) terminates gracefully with reason

**Examples**: `examples/react_replan/` - retrieval timeout → re-plan using cached index

**Acceptance**: On failure, planner requests revised JSON action and succeeds where possible; otherwise exits with typed final result + reason

---

### Phase D — Multi-Node Concurrency (Parallel Calls)

**Objective**: Allow planner to propose *sets* of independent calls evaluated in parallel; then join results.

**Deliverables**:

1. **Parallel action schema**
   * LLM can return plan with multiple parallel actions and join descriptor

2. **Executor**
   * Launch N nodes concurrently (`map_concurrent`) with bounded parallelism
   * Collect observations; run join node
   * Backoff/retries per node preserved; partial failures short-circuit or degrade gracefully

3. **Join-k semantics**
   * Integrate with existing `join_k` helper when available

**Testing**:

* Parallel fan-out correctness; ordering independence; join determinism
* Fault injection: one branch fails → re-plan or degrade according to policy

**Examples**: `examples/react_parallel/` - shard retrieval across 3 sources, merge and summarize

**Acceptance**: Planner can propose valid parallel sets; executor runs them safely under existing reliability guarantees

---

## Tool Metadata & Catalog (v2.5)

### NodeSpec Dataclass

New module: `penguiflow/catalog.py`

```python
@dataclass(frozen=True)
class NodeSpec:
    node: Node
    name: str
    desc: str
    args_model: Type[BaseModel]
    out_model: Type[BaseModel]
    side_effects: Literal["pure", "read", "write", "external", "stateful"] = "pure"
    tags: Sequence[str] = ()
    auth_scopes: Sequence[str] = ()
    cost_hint: Optional[str] = None
    latency_hint_ms: Optional[int] = None
    safety_notes: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
```

### Three Ergonomic Ways to Provide Specs

**A) Decorator** (most ergonomic, preferred):

```python
from penguiflow.catalog import tool

@tool(desc="KB search over internal docs", side_effects="read", tags=["search","docs"])
async def search_docs(msg: SearchArgs, ctx) -> SearchOut:
    ...
```

**B) Node wrapper method** (no decorator needed):

```python
from penguiflow.catalog import describe_node

search_node = describe_node(
    Node(search_docs, name="search_docs"),
    desc="KB search over internal docs",
    side_effects="read",
    tags=["search","docs"],
)
```

**C) External catalog** (no changes to nodes):

```python
specs = [
    NodeSpec(
        node=search_node,
        name="search_docs",
        desc="KB search",
        args_model=SearchArgs, out_model=SearchOut,
        side_effects="read",
    ),
]
```

### Catalog Builder

```python
from penguiflow.catalog import build_catalog

catalog = build_catalog(
    nodes=[search_node, triage_node, summarize_node],
    registry=my_registry,
    overrides={"search_docs": {"cost_hint": "low"}}
)
```

---

## Public API (v2.5)

```python
from penguiflow.planner import ReactPlanner

class ReactPlanner:
    def __init__(
        self,
        llm: str | dict,                        # LiteLLM model name or config
        nodes: Sequence[Node],                  # or Recipes
        *,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        token_budget: int | None = None,
        pause_enabled: bool = True,
        state_store: Any | None = None,         # optional (v2.1) for durability
        summarizer_llm: str | dict | None = None,   # cheaper model for summarization
        system_prompt_extra: str | None = None,     # append-only dev guidance
        planning_hints: dict | None = None,         # structured constraints/guidance
    ) -> None: ...

    async def run(self, query: str, *, context_meta: dict | None = None) -> Any: ...
    async def resume(self, token: str, user_input: str | None = None) -> Any: ...
    async def step(self, trajectory: "Trajectory") -> "PlannerAction": ...
```

**Types**:

```python
class PlannerAction(BaseModel):
    thought: str
    next_node: str | None = None
    args: dict[str, Any] | None = None
    plan: list[dict] | None = None   # Phase D parallel actions
    join: dict | None = None         # join descriptor

class PlannerPause(BaseModel):
    reason: Literal["approval_required", "await_input", "external_event"]
    payload: dict
    resume_token: str
```

---

## Prompting Strategy (JSON-only)

* **System prompt** summarizes rules:
  * Tools = PF nodes; must output *valid JSON only* matching provided schemas
  * Use minimal text in `thought`
  * Respect constraints: `deadline_s`, hop budget, token budget, cost
  * Prefer plans that reduce token footprint (reuse summaries)
* **Tool catalog**: list of nodes with name, description, and compact JSON Schemas
* **Repair loop**: on schema violation, reply with short machine message: `"args did not validate: <error>. Return corrected JSON."`

---

## Testing Matrix (v2.5)

| Area          | Unit                     | Integration            | Fault Injection                |
| ------------- | ------------------------ | ---------------------- | ------------------------------ |
| JSON I/O      | parse/repair/validate    | end-to-end example     | malformed tool args            |
| Reliability   | backoff/timeouts honored | long node + cancel     | repeated transient failures    |
| Summarization | compaction threshold     | quality after resume   | pathological long runs         |
| Re-planning   | constraint enforcement   | recovery after failure | hard failure → graceful finish |
| Concurrency   | join correctness         | mixed success paths    | one branch fails mid-fanout    |

---

## Examples (v2.5)

* `examples/react_minimal/` — Phase A, single-threaded loop
* `examples/react_pause_resume/` — Phase B, approval & resume
* `examples/react_replan/` — Phase C, failure → constrained re-plan
* `examples/react_parallel/` — Phase D, concurrent fan-out & join

Each example includes a tiny README and runnable script:

```bash
uv run python examples/react_minimal/main.py
```

---

## Backwards Compatibility (v2.5)

* Purely **opt-in**; does not change Node, Flow, or Core APIs
* Works in-proc today; later can use `StateStore` for durable pause/resume with zero breaking changes
* No new mandatory dependencies; LiteLLM required only if you import/use the planner

---

## Risks & Mitigations (v2.5)

* **LLM returns non-JSON** → strict response_format (where supported) + repair loop
* **Hallucinated args** → Pydantic validation + corrective prompt, bounded retries
* **Token sprawl** → structured summarization + budgets
* **Complexity creep** → keep planner ~300–500 LOC; prompts/data contracts do the heavy lifting
* **Vendor lock-in** → LiteLLM keeps providers swappable

---

## Definition of Done (v2.5)

* **Phase A**: JSON-only planner completes typical triage→retrieve→summarize flows with deterministic outputs
* **Phase B**: Summarization keeps prompts within budget; pause/resume reliable
* **Phase C**: Re-planning succeeds on common failures or exits with typed "no_path/budget_exhausted"
* **Phase D**: Parallel fan-out with join works; reliability preserved per branch

---

## Stretch Goals (post-v2.5)

* Planner policies (cost ceilings per tenant)
* Automatic tool selection from **Agent Cards (A2A)** when available
* Cached tool arg priors (few-shot from past successful trajectories)

---

## TL;DR

**v2.1**: Distributed & A2A-ready orchestration with opt-in `StateStore`, `MessageBus`, `RemoteTransport`, and A2A server adapter — **COMPLETED ✅**

**v2.5**: Lightweight, typed ReAct planner powered by LiteLLM that chooses & sequences PenguiFlow nodes using JSON-only protocol — **NEXT 🎯**

This evolution stays true to PenguiFlow's DNA: **typed, reliable, lightweight**. The planner borrows the *good parts* of DSPy/Google/Pydantic—tool iteration, plan→act discipline, typed outputs—while avoiding heavy frameworks, free-form logs, and complex servers.


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


