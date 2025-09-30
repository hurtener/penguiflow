# PenguiFlow v2.5 — ReAct Planner (LiteLLM)

*A lightweight, typed, product-agnostic planner that chooses & sequences PenguiFlow nodes/recipes using a JSON-only protocol.*

## Vision

Add intelligent planning to PenguiFlow without compromising its core DNA: **typed, reliable, lightweight**.

## Goals

* **Planner chooses PenguiFlow Nodes/Recipes** (tools) at runtime
* **Typed I/O**: strict JSON contracts from your Node schemas (Pydantic v2)
* **Reliability**: use existing NodeRunner semantics (retries, exponential backoff, timeouts, cancellation)
* **Pause/Resume**: approvals / deferred work (persisted via v2.1 `StateStore` hook; in-proc fallback)
* **Adaptive re-planning**: detect failures/assumption breaks and request constrained plan updates
* **Token-aware**: compact trajectory state with structured summaries
* **LLM-agnostic** via **LiteLLM**; JSON-only responses (no free-form ReAct logs)
* **Library-only**: no endpoints, UI, or storage; provide hooks only

## Non-Goals

* Not a DSL or training framework (no DSPy-style signature graph)
* No built-in database, broker, or scheduler (use v2.1 `StateStore` / `MessageBus` when present)
* No endpoint/UI scaffolding (samples may show FastAPI glue in `examples/` only)

---

## Architecture Overview

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
  "side_effects": "read",
  "input_schema": { ... JSON Schema from Pydantic ... },
  "output_schema": { ... }
}
```

**Planner contract** (LLM output):

```json
{"thought":"...", "next_node":"retrieve_docs", "args":{"topic":"metrics", "k":5}}
```

---

## Tool Catalog System

Before diving into phases, let's establish how nodes become discoverable tools for the planner.

### NodeSpec Dataclass

New module: `penguiflow/catalog.py`

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional, Sequence, Type
from pydantic import BaseModel
from penguiflow.node import Node

SideEffect = Literal["pure", "read", "write", "external", "stateful"]

@dataclass(frozen=True)
class NodeSpec:
    node: Node
    name: str
    desc: str
    args_model: Type[BaseModel]
    out_model: Type[BaseModel]
    side_effects: SideEffect = "pure"
    tags: Sequence[str] = ()
    auth_scopes: Sequence[str] = ()
    cost_hint: Optional[str] = None         # "low", "med", "high" or "$$"
    latency_hint_ms: Optional[int] = None   # rough typical latency
    safety_notes: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_tool_record(self) -> dict[str, Any]:
        """Used by planner to render compact tool descriptor + JSON schemas."""
        return {
            "name": self.name,
            "desc": self.desc,
            "side_effects": self.side_effects,
            "tags": list(self.tags),
            "auth_scopes": list(self.auth_scopes),
            "cost_hint": self.cost_hint,
            "latency_hint_ms": self.latency_hint_ms,
            "safety_notes": self.safety_notes,
            "args_schema": self.args_model.model_json_schema(),
            "out_schema": self.out_model.model_json_schema(),
            "extra": dict(self.extra),
        }
```

### Three Ergonomic Ways to Annotate Nodes

#### A) Decorator (Preferred)

```python
from penguiflow.catalog import tool

@tool(desc="KB search over internal docs", side_effects="read", tags=["search","docs"])
async def search_docs(msg: SearchArgs, ctx) -> SearchOut:
    """Search the internal knowledge base for relevant documents."""
    ...

search_node = Node(search_docs, name="search_docs")
```

#### B) Node Wrapper

```python
from penguiflow.catalog import describe_node

search_node = describe_node(
    Node(search_docs, name="search_docs"),
    desc="KB search over internal docs",
    side_effects="read",
    tags=["search","docs"],
)
```

#### C) External Catalog

```python
from penguiflow.catalog import NodeSpec

specs = [
    NodeSpec(
        node=search_node,
        name="search_docs",
        desc="KB search",
        args_model=SearchArgs,
        out_model=SearchOut,
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

**Fallback behavior**:
- If no `desc` provided: use function docstring → Pydantic Field descriptions → `"{name} (no description)"`
- I/O models from `ModelRegistry` if provided, otherwise from Node's adapters

---

## Phase A — JSON-only ReAct Loop

**Objective**: Implement minimal, deterministic loop: *user query → choose node (JSON) → run node → record observation → repeat (bounded)*.

### Deliverables

1. **`penguiflow/planner/react.py`**

   ```python
   class ReactPlanner:
       def __init__(
           self,
           llm: str | dict,                      # LiteLLM model name or config
           nodes: Sequence[Node] | None = None,  # or provide catalog directly
           catalog: Sequence[NodeSpec] | None = None,
           *,
           max_iters: int = 8,
           temperature: float = 0.0,
           json_schema_mode: bool = True,
           registry: ModelRegistry | None = None,
       ) -> None: ...

       async def run(self, query: str, *, context_meta: dict | None = None) -> Any: ...
       async def step(self, trajectory: Trajectory) -> PlannerAction: ...
   ```

   - JSON coercion & validation (Pydantic) for `PlannerAction`
   - Catalog builder: introspect Node/Recipe → `NodeSpec` → tool descriptor
   - LiteLLM client shim (strict JSON response via `response_format={"type": "json_object"}` where supported)

2. **Prompt Templates** (`planner/prompts.py`)

   - System prompt: objectives, rules, JSON-only format, available nodes with compact schemas
   - Few-shot mini examples (optional) demonstrating valid outputs & failure cases

3. **Trajectory Model** (`planner/types.py`)

   ```python
   class TrajectoryStep(BaseModel):
       thought: str
       node: str
       args: dict[str, Any]
       observation: Any
       error: Optional[str] = None
       tokens: Optional[int] = None
       latency_ms: Optional[float] = None

   class Trajectory(BaseModel):
       steps: list[TrajectoryStep]
       # ... stats methods
   ```

### Algorithm

1. Build catalog from nodes (via `build_catalog`)
2. Seed trajectory with user query
3. Ask LLM for `{thought, next_node, args}` (JSON only)
4. Validate `args` against Node's **input schema**; if invalid, repair (max 2 attempts)
5. Execute Node via NodeRunner (inherits retries/backoff/timeouts)
6. Append observation (validated against `out` schema); loop until `finish`/`stop`/`max_iters`

### Testing

**Unit**:
- JSON parsing & schema validation (good/invalid/repair)
- Node failures: ensure NodeRunner backoff kicks in; observation records error
- Deterministic output with temperature=0

**Integration**:
- Example flow: `triage → retrieve → summarize`
- Streaming: if a node emits `StreamChunk`, planner surfaces partials (pass through or aggregate)

### Example

`examples/react_minimal/` — 3 nodes + planner; run once; prints final result & trajectory

### Acceptance

* Planner completes within `max_iters` for happy-path scenario
* On invalid args, it self-repairs JSON and proceeds
* No free-form tool logs: planner and LLM exchange **strict JSON**

---

## Phase B — Trajectory Summarization + Pause/Resume + Developer Hints

**Objective**: Keep token footprint low, enable approvals / long-running handoffs, and let developers inject domain knowledge.

### Deliverables

1. **Token-aware Summarizer**

   - `Trajectory.compress()` creates structured mini-state:

     ```json
     {
       "goals": ["..."],
       "facts": {"topic": "metrics", "k": 5, "docs_seen": 12},
       "pending": ["call summarize(topic, docs)"],
       "last_output_digest": "…"
     }
     ```

   - **Optional cheaper LLM** for summarization:
     - Planner accepts `summarizer_llm` separate from main `llm`
     - If unset: (a) rule-based compressor first (deterministic truncation), then (b) fall back to `llm` when necessary
     - Summarizer prompt: **short**, **schema-bound**, JSON-only output

   - Prompts include **summary** instead of full logs when over budget

2. **Pause/Resume Hooks**

   ```python
   class PlannerPause(BaseModel):
       reason: Literal["approval_required", "await_input", "external_event", "constraints_conflict"]
       payload: dict
       resume_token: str

   # API methods:
   async def pause(self, reason: str, payload: dict) -> PlannerPause: ...
   async def resume(self, token: str, user_input: str | None = None) -> Any: ...
   ```

   - Storage: in-proc dict; if v2.1 `StateStore` provided, use it for durability (opt-in)

3. **Approvals Pattern**

   - Inject "policy node" requiring human approval on high-risk actions
   - Planner yields `PlannerPause(approval_required)`

4. **Developer Prompt Hints**

   Configurable **system prompt extension** + **structured hints**:

   ```python
   class ReactPlanner:
       def __init__(
           self,
           llm: str | dict,
           nodes: Sequence[Node] | None = None,
           catalog: Sequence[NodeSpec] | None = None,
           *,
           max_iters: int = 8,
           temperature: float = 0.0,
           json_schema_mode: bool = True,
           token_budget: int | None = None,
           pause_enabled: bool = True,
           state_store: Any | None = None,
           registry: ModelRegistry | None = None,
           # NEW in Phase B:
           summarizer_llm: str | dict | None = None,       # cheaper model for compaction
           system_prompt_extra: str | None = None,         # append-only dev guidance
           planning_hints: dict | None = None,             # structured constraints/guidance
       ) -> None: ...
   ```

   **Planning Hints Schema**:

   ```json
   {
     "ordering_hints": ["triage", "retrieve_docs", "rerank", "summarize"],
     "parallel_groups": [["retrieve_docs_A", "retrieve_docs_B"], ["rerank"]],
     "sequential_only": ["apply_compliance", "send_email"],
     "disallow_nodes": ["expensive_tool_v1"],
     "prefer_nodes": ["cached_search"],
     "budget_hints": {"max_parallel": 3, "max_cost_usd": 0.10}
   }
   ```

   **Execution Semantics**:

   - **Hard constraints enforced in code**:
     - `disallow_nodes`: reject actions referencing these nodes, ask for corrected plan
     - `max_parallel`: cap concurrent actions at executor level
     - `sequential_only`: prevent nodes from appearing in parallel groups; auto-rewrite or request revision

   - **Soft preferences** (prompt-level nudges):
     - `prefer_nodes`, `ordering_hints`: if violated, do **one** corrective iteration before proceeding

   **Prompting Changes**:

   - Planner system prompt now appends:
     - `system_prompt_extra` (verbatim, if provided)
     - Compact rendering of `planning_hints`:
       - "**Respect** the following constraints: …"
       - "Preferred order (if applicable): …"
       - "Allowed parallel groups: …; do not exceed `max_parallel`."
       - "Disallowed tools: … (never call)."

   - Summarizer prompt (when compaction needed):
     - Ultra-brief instructions, JSON-only output, keep **facts/pending/goals**
     - If `summarizer_llm` not set, try rule-based compressor first; if still too long, use main `llm`

### Testing

**Summarizer**:
- Uses cheaper model when set; falls back to main LLM only if summarizer fails
- Rule-based shrink first → summarizer second; ensure output remains valid and helpful for re-planning

**Pause/Resume**:
- Serialize → restore → continue; deterministic outcome

**Prompt Hints**:
- Plans follow `ordering_hints` when feasible; if not, we get a single correction pass
- `disallow_nodes` never executed (guard tested)
- `max_parallel` enforced even if LLM proposes larger fan-out

### Example

`examples/react_pause_resume/`:

```python
planner = ReactPlanner(
    llm="gpt-4o",
    summarizer_llm="gpt-4o-mini",
    nodes=[triage, retrieve_docs, rerank, summarize, apply_compliance, send_email],
    system_prompt_extra="Break the query into minimal steps. Prefer cached_search when available.",
    planning_hints={
        "ordering_hints": ["triage", "retrieve_docs", "rerank", "summarize"],
        "parallel_groups": [["retrieve_docs_A", "retrieve_docs_B"]],
        "sequential_only": ["apply_compliance", "send_email"],
        "disallow_nodes": ["expensive_tool_v1"],
        "prefer_nodes": ["cached_search"],
        "budget_hints": {"max_parallel": 2, "max_cost_usd": 0.05}
    }
)

result = await planner.run("Share last month's metrics to Slack")
```

### Error Handling & Fallbacks

- If summarizer returns invalid JSON:
  - 1–2 JSON repair attempts; then fall back to rule-based truncation with clear `summary_note`

- If hints produce impossible plan (e.g., required node is disallowed):
  - Planner returns `PlannerPause(reason="constraints_conflict")`, or falls back to safe, minimal path if allowed

### Acceptance

* Prompts stay under configurable token budget
* Resumed runs preserve constraints and state
* Cheaper summarizer slashes cost for iterative sessions
* Developer hints yield faster, cheaper, more reliable plans without hardcoding flows

---

## Phase C — Adaptive Re-Planning (Error Feedback)

**Objective**: When execution fails after retries or assumptions break, request constrained, minimal re-plan.

### Deliverables

1. **Error Channel to LLM**

   On Node failure (after NodeRunner retries), send:

   ```json
   {
     "failure": {
       "node": "retrieve_docs",
       "args": {...},
       "error_code": "Timeout",
       "message": "...",
       "suggestion": "reduce_k or pick alternate source"
     }
   }
   ```

   Prompt: "Propose revised next action that **respects budgets** and avoids failure cause."

2. **Constraint Manager**

   - Hard limits: wall-clock deadline, hop budget, token budget
   - Planner refuses plans violating constraints; asks for another revision (max N)

3. **Finish Conditions**

   ```python
   class PlannerFinish(BaseModel):
       reason: Literal["answer_complete", "no_path", "budget_exhausted"]
       payload: Any
       metadata: dict
   ```

### Testing

* Simulate transient and permanent failures; verify re-plan path changes (different node or args)
* Verify constraints enforcement (deadline/hops) terminates gracefully with reason

### Example

`examples/react_replan/` — retrieval timeout → re-plan using cached index; completes

### Acceptance

* On failure, planner requests revised JSON action and succeeds where possible
* Otherwise exits with typed final result + reason

---

## Phase D — Multi-Node Concurrency (Parallel Calls)

**Objective**: Allow planner to propose *sets* of independent calls evaluated in parallel; then join results.

### Deliverables

1. **Parallel Action Schema**

   LLM can return:

   ```json
   {
     "plan": [
       {"node": "retrieve_part", "args": {"id": 1}},
       {"node": "retrieve_part", "args": {"id": 2}}
     ],
     "join": {"node": "merge_parts", "args": {"expect": 2}}
   }
   ```

2. **Executor**

   - Launch N nodes concurrently (`map_concurrent`) with bounded parallelism
   - Collect observations; run join node
   - Backoff/retries per node preserved; partial failures short-circuit or degrade gracefully per policy

3. **Join-k Semantics**

   - If `join.expect=k`, integrate with existing `join_k` helper

### Testing

* Parallel fan-out correctness; ordering independence; join determinism
* Fault injection: one branch fails → re-plan or degrade according to policy

### Example

`examples/react_parallel/` — shard retrieval across 3 sources, merge and summarize

### Acceptance

* Planner can propose valid parallel sets
* Executor runs them safely under existing reliability guarantees

---

## Public API (Final)

```python
from penguiflow.planner import ReactPlanner
from penguiflow.planner.types import PlannerAction, PlannerPause, PlannerFinish

class ReactPlanner:
    def __init__(
        self,
        llm: str | dict,                        # LiteLLM model name or config
        nodes: Sequence[Node] | None = None,    # will build catalog automatically
        catalog: Sequence[NodeSpec] | None = None,  # or provide pre-built catalog
        *,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        token_budget: int | None = None,
        pause_enabled: bool = True,
        state_store: Any | None = None,         # v2.1 StateStore for durability
        registry: ModelRegistry | None = None,
        summarizer_llm: str | dict | None = None,
        system_prompt_extra: str | None = None,
        planning_hints: dict | None = None,
    ) -> None: ...

    async def run(self, query: str, *, context_meta: dict | None = None) -> Any: ...
    async def resume(self, token: str, user_input: str | None = None) -> Any: ...
    async def step(self, trajectory: Trajectory) -> PlannerAction: ...

class PlannerAction(BaseModel):
    thought: str
    next_node: str | None = None     # "finish" or None to stop
    args: dict[str, Any] | None = None
    plan: list[dict] | None = None   # Phase D parallel actions
    join: dict | None = None         # join descriptor

class PlannerPause(BaseModel):
    reason: Literal["approval_required", "await_input", "external_event", "constraints_conflict"]
    payload: dict
    resume_token: str

class PlannerFinish(BaseModel):
    reason: Literal["answer_complete", "no_path", "budget_exhausted"]
    payload: Any
    metadata: dict
```

---

## Prompting Strategy (JSON-only)

**System Prompt** summarizes rules:
- Tools = PF nodes; must output *valid JSON only* matching provided schemas
- Use minimal text in `thought`
- Respect constraints: `deadline_s`, hop budget, token budget, cost
- Prefer plans that reduce token footprint (reuse summaries)

**Tool Catalog**: list of nodes with name, description, side effects, and compact JSON Schemas

**Repair Loop**: on schema violation, reply with short machine message: `"args did not validate: <error>. Return corrected JSON."`

---

## Testing Matrix

| Area          | Unit                     | Integration            | Fault Injection                |
| ------------- | ------------------------ | ---------------------- | ------------------------------ |
| JSON I/O      | parse/repair/validate    | end-to-end example     | malformed tool args            |
| Reliability   | backoff/timeouts honored | long node + cancel     | repeated transient failures    |
| Summarization | compaction threshold     | quality after resume   | pathological long runs         |
| Re-planning   | constraint enforcement   | recovery after failure | hard failure → graceful finish |
| Concurrency   | join correctness         | mixed success paths    | one branch fails mid-fanout    |

---

## Examples

* `examples/react_minimal/` — Phase A, single-threaded loop
* `examples/react_pause_resume/` — Phase B, approval & resume with hints
* `examples/react_replan/` — Phase C, failure → constrained re-plan
* `examples/react_parallel/` — Phase D, concurrent fan-out & join

Each example includes a README and runnable script:

```bash
uv run python examples/react_minimal/main.py
```

---

## Backwards Compatibility

* Purely **opt-in**; does not change Node, Flow, or Core APIs
* Works in-proc today; later can use `StateStore` for durable pause/resume with zero breaking changes
* No new mandatory dependencies; LiteLLM required only if you import/use the planner

---

## Risks & Mitigations

* **LLM returns non-JSON** → strict response_format (where supported) + repair loop
* **Hallucinated args** → Pydantic validation + corrective prompt, bounded retries
* **Token sprawl** → structured summarization + budgets
* **Complexity creep** → keep planner ~300–500 LOC; prompts/data contracts do the heavy lifting
* **Vendor lock-in** → LiteLLM keeps providers swappable

---

## Definition of Done

* **Phase A**: JSON-only planner completes typical triage→retrieve→summarize flows with deterministic outputs
* **Phase B**: Summarization keeps prompts within budget; pause/resume reliable; hints work as expected
* **Phase C**: Re-planning succeeds on common failures or exits with typed "no_path/budget_exhausted"
* **Phase D**: Parallel fan-out with join works; reliability preserved per branch

---

## Stretch Goals (post-v2.5)

* Planner policies (cost ceilings per tenant)
* Automatic tool selection from **Agent Cards (A2A)** when available
* Cached tool arg priors (few-shot from past successful trajectories)

---

## Catalog System Benefits

* **Ergonomic**: use decorator, wrapper, or external catalog—whatever fits your codebase
* **Typed**: args/out schemas come straight from your Pydantic models (no duplicate typing)
* **Lightweight**: no core API break; everything is additive
* **Planner-ready**: clean, compact tool catalog with side-effects and hints the planner can use
* **Side-effect-aware**: planner can apply rules ("avoid `write` unless approved"; "never call `stateful` in parallel")
* **Cost/latency shaping**: use `cost_hint` and `latency_hint_ms` to penalize expensive/slow nodes
* **Auth scopes**: if caller lacks scopes, planner blocks those nodes (hard constraint)
* **Tags**: great for domain routing ("finance", "customer_support") and developer hints

---

**TL;DR**

This planner stays true to PenguiFlow's DNA: **typed, reliable, lightweight**. It borrows the *good parts* of DSPy/Google/Pydantic—tool iteration, plan→act discipline, typed outputs—while avoiding heavy frameworks, free-form logs, and complex servers.
