# PenguiFlow — ReAct Planner (LiteLLM)

*A lightweight, typed, product-agnostic planner that chooses & sequences PenguiFlow nodes/recipes using a JSON-only protocol.*

## Goals

* **Planner chooses PenguiFlow Nodes/Recipes** (tools) at runtime.
* **Typed I/O**: strict JSON contracts from your Node schemas (Pydantic v2).
* **Reliability**: use existing NodeRunner semantics (retries, exponential backoff, timeouts, cancellation).
* **Pause/Resume**: approvals / deferred work (persisted via future `StateStore` hook; in-proc fallback now).
* **Adaptive re-planning**: detect failures/assumption breaks and request a constrained plan update.
* **Token-aware**: compact trajectory state with structured summaries.
* **LLM-agnostic** via **LiteLLM**; JSON-only responses (no free-form ReAct logs).
* **Library-only**: no endpoints, UI, or storage; provide hooks only.

## Non-Goals

* Not a DSL or training framework (no DSPy-style signature graph).
* No built-in database, broker, or scheduler (use v2.1 `StateStore` / `MessageBus` when present).
* No endpoint/UI scaffolding (samples may show FastAPI glue in `examples/` only).

---

## Architecture (at a glance)

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

* **NodeSpec** (derived from existing Nodes):

  ```json
  {
    "name": "retrieve_docs",
    "description": "Fetch k docs by topic",
    "input_schema": { ... JSON Schema from Pydantic ... },
    "output_schema": { ... }
  }
  ```
* **Planner contract** (LLM output):

  ```json
  {"thought":"...", "next_node":"retrieve_docs", "args":{"topic":"metrics", "k":5}}
  ```

---

## Phase A — JSON-only ReAct Loop

**Objective**
Implement a minimal, deterministic loop: *user query → choose node (JSON) → run node → record observation → repeat (bounded)*.

**Deliverables**

1. `penguiflow/planner/react.py`

   * `class ReactPlanner:`

     * `__init__(llm: str|dict, nodes: Sequence[Node|Recipe], *, max_iters=8, json_schema_mode=True, temperature=0, system_prompt: str|None=None)`
     * `async def run(self, query: str, *, context_meta: dict|None=None) -> Any`
     * `async def step(self, trajectory: Trajectory) -> PlannerAction`
   * JSON coercion & validation (Pydantic) for `PlannerAction`.
   * Node catalog builder (introspect Node/Recipe → description + in/out JSON Schema).
   * LiteLLM client shim (strict JSON response: use `response_format={"type": "json_object"}` where supported; otherwise tool a guardrail with regex/repair).

2. **Prompt Templates** (`planner/prompts.py`)

   * *System prompt*: objectives, rules, JSON-only format, available nodes with compact schemas.
   * *Few-shot* mini examples (optional) demonstrating valid outputs & failure cases.

3. **Trajectory model** (`planner/types.py`)

   * `TrajectoryStep`: `{thought, node, args, observation, error?, tokens?, latency_ms?}`
   * `Trajectory`: bounded list + stats

**Algorithm**

1. Seed trajectory with user query.
2. Ask LLM for `{thought,next_node,args}` (JSON only).
3. Validate `args` against Node’s **input schema**; if invalid, reply to LLM with a *schema violation message* and request corrected JSON (max 2 repairs).
4. Execute Node via NodeRunner (inherits retries/backoff/timeouts).
5. Append observation (coerced to `out` schema); loop until `finish`/`stop`/`max_iters`.

**Testing**

* Unit:

  * JSON parsing & schema validation (good/invalid/repair).
  * Node failures: ensure NodeRunner backoff kicks in; observation records error.
  * Deterministic output with temperature=0.
* Integration:

  * Example flow: `triage → retrieve → summarize`.
  * Streaming: if a node emits `StreamChunk`, ensure planner can surface partials (pass through to caller or aggregate).

**Examples**

* `examples/react_minimal/`

  * 3 nodes + planner; run once; prints final result & trajectory.

**Acceptance**

* Planner completes within `max_iters` for happy-path scenario.
* On invalid args, it self-repairs JSON and proceeds.
* No free-form tool logs: planner and LLM exchange **strict JSON**.

**Non-Goals**

* No summarization yet; no pause/resume; no concurrency.

---

## Phase B — Trajectory Summarization + Pause/Resume

**Objective**
Keep token footprint low and enable approvals / long-running handoffs.

**Deliverables**

1. **Token-aware summarizer**

   * `Trajectory.compress()` creates a *structured* mini-state:

     ```json
     {
       "goals":["..."],
       "facts":{"topic":"metrics","k":5,"docs_seen":12},
       "pending":["call summarize(topic, docs)"],
       "last_output_digest":"…"
     }
     ```
   * Prompts now include the **summary** instead of full logs when over budget.

2. **Pause/Resume hooks**

   * `ReactPlanner.pause(reason: str, payload: dict)` returns a `PlannerPause` result.
   * `ReactPlanner.resume(resume_token, user_input=None)` continues from compressed state.
   * Storage: in-proc dict now; if v2.1 `StateStore` is provided later, use it for durability (opt-in).

3. **Approvals pattern**

   * Inject a “policy node” that requires human approval on high-risk actions; planner yields `PlannerPause(approval_required)`.

**Testing**

* Summarization: ensures re-planning quality remains high after compression (golden tests).
* Pause/Resume: serialize → restore → continue; deterministic outcome.

**Examples**

* `examples/react_pause_resume/`

  * Simulate “need approval to send email” → pause → resume with approval.

**Acceptance**

* Prompts stay under a configurable token budget.
* Resumed runs preserve constraints and state.

**Non-Goals**

* No persistent storage in core; no UI for approval (example only).

---

## Phase C — Adaptive Re-Planning (Error Feedback)

**Objective**
When execution fails after retries or assumptions break, request a constrained, minimal re-plan.

**Deliverables**

1. **Error channel to LLM**

   * On Node failure (after NodeRunner retries), send:

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
   * Prompt: “Propose a revised next action that **respects budgets** and avoids the failure cause.”

2. **Constraint manager**

   * Hard limits: wall-clock deadline, hop budget, token budget.
   * Planner refuses plans violating constraints; asks for another revision (max N).

3. **Finish conditions**

   * `finish(reason="answer_complete" | "no_path" | "budget_exhausted")` with a typed final payload.

**Testing**

* Simulate transient and permanent failures; verify re-plan path changes (e.g., different node or args).
* Verify constraints enforcement (deadline/hops) terminates gracefully with a reason.

**Examples**

* `examples/react_replan/`

  * Retrieval timeout → re-plan using cached index; completes.

**Acceptance**

* On failure, planner requests a revised JSON action and succeeds where possible; otherwise exits with typed final result + reason.

**Non-Goals**

* Learning across sessions; this is stateless aside from Trajectory.

---

## Phase D — Multi-Node Concurrency (Parallel Calls)

**Objective**
Allow the planner to propose *sets* of independent calls evaluated in parallel; then join results.

**Deliverables**

1. **Parallel action schema**

   * LLM can return:

     ```json
     {
       "plan": [
         {"node":"retrieve_part","args":{"id":1}},
         {"node":"retrieve_part","args":{"id":2}}
       ],
       "join": {"node":"merge_parts","args":{"expect":2}}
     }
     ```

2. **Executor**

   * Launch N nodes concurrently (`map_concurrent`) with bounded parallelism; collect observations; run join node.
   * Backoff/retries per node preserved; partial failures short-circuit or degrade gracefully per policy.

3. **Join-k semantics**

   * If `join.expect=k`, integrate with existing `join_k` helper when available.

**Testing**

* Parallel fan-out correctness; ordering independence; join determinism.
* Fault injection: one branch fails → re-plan or degrade according to policy.

**Examples**

* `examples/react_parallel/`

  * Shard retrieval across 3 sources, merge and summarize.

**Acceptance**

* Planner can propose valid parallel sets; executor runs them safely under existing reliability guarantees.

**Non-Goals**

* Cross-step speculative execution (out of scope).

---

## Public API (proposed, stable after Phase B)

```python
# penguiflow/planner/react.py
from typing import Any, Sequence, Optional
from penguiflow.node import Node

class ReactPlanner:
    def __init__(
        self,
        llm: str | dict,                      # LiteLLM model name or config
        nodes: Sequence[Node],                # or Recipes
        *,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        token_budget: int | None = None,      # for summarization threshold
        pause_enabled: bool = True,
        state_store: Any | None = None,       # optional (v2.1) for durability
    ) -> None: ...

    async def run(self, query: str, *, context_meta: dict | None = None) -> Any: ...
    async def resume(self, token: str, user_input: str | None = None) -> Any: ...

    # Advanced:
    async def step(self, trajectory: "Trajectory") -> "PlannerAction": ...
```

**Types**

```python
class PlannerAction(BaseModel):
    thought: str
    next_node: str | None = None     # "finish" or None to stop
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

  * Tools = PF nodes; must output *valid JSON only* matching provided schemas.
  * Use minimal text in `thought`.
  * Respect constraints: `deadline_s`, hop budget, token budget, cost.
  * Prefer plans that reduce token footprint (reuse summaries).
* **Tool catalog**: list of nodes with name, description, and compact JSON Schemas.
* **Repair loop**: on schema violation, reply with a short machine message: `"args did not validate: <error>. Return corrected JSON."`

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

## Examples (folders)

* `examples/react_minimal/` — Phase A, single-threaded loop.
* `examples/react_pause_resume/` — Phase B, approval & resume.
* `examples/react_replan/` — Phase C, failure → constrained re-plan.
* `examples/react_parallel/` — Phase D, concurrent fan-out & join.

Each example includes a tiny README and runnable script:

```bash
uv run python examples/react_minimal/main.py
```

---

## Backwards Compatibility

* Purely **opt-in**; does not change Node, Flow, or Core APIs.
* Works in-proc today; later can use `StateStore` for durable pause/resume with zero breaking changes.
* No new mandatory dependencies; LiteLLM required only if you import/use the planner.

---

## Risks & Mitigations

* **LLM returns non-JSON** → strict response_format (where supported) + repair loop.
* **Hallucinated args** → Pydantic validation + corrective prompt, bounded retries.
* **Token sprawl** → structured summarization + budgets.
* **Complexity creep** → keep planner ~300–500 LOC; prompts/data contracts do the heavy lifting.
* **Vendor lock-in** → LiteLLM keeps providers swappable.

---

## Definition of Done

* **A**: JSON-only planner completes typical triage→retrieve→summarize flows with deterministic outputs.
* **B**: Summarization keeps prompts within budget; pause/resume reliable.
* **C**: Re-planning succeeds on common failures or exits with typed “no_path/budget_exhausted”.
* **D**: Parallel fan-out with join works; reliability preserved per branch.

---

## Stretch (post-D)

* Planner policies (cost ceilings per tenant).
* Automatic tool selection from **Agent Cards (A2A)** when available.
* Cached tool arg priors (few-shot from past successful trajectories).

---

**TL;DR**
This planner stays true to PenguiFlow’s DNA: **typed, reliable, lightweight**. It borrows the *good parts* of DSPy/Google/Pydantic—tool iteration, plan→act discipline, typed outputs—while avoiding heavy frameworks, free-form logs, and complex servers.



## Phase B addendum — Trajectory Summarization + Pause/Resume *(enhanced)*

### New Objectives (additions)

* Allow an **optional cheaper LLM** for summarization/compaction to cut cost.
* Let developers **inject prompt hints** into the planner system prompt:

  * task decomposition guidance
  * preferred **parallel vs. sequential** constraints
  * suggested **node ordering** (domain knowledge)
  * allow/deny lists, cost ceilings, or tenant-specific rules

### Additions to Deliverables

1. **Summarizer LLM (optional)**

   * Planner accepts a `summarizer_llm` separate from the main `llm`.
   * If unset, use (a) a rule-based compressor first (deterministic truncation), then (b) fall back to `llm` for summarization when necessary.
   * Summarizer prompt is **short** and **schema-bound**; outputs a compact JSON state:

     ```json
     {"goals":[...],"facts":{...},"pending":[...],"last_output_digest":"..."}
     ```

2. **Developer Prompt Hints**

   * Configurable **system prompt extension** merged into the planner’s system prompt:

     * `system_prompt_extra`: freeform developer text appended after the core rules.
     * Optional **structured hints** that the planner can leverage programmatically:

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
   * The planner includes these hints *succinctly* in the system prompt and enforces hard constraints in code (e.g., deny list, max_parallel).

### Updated Public API

```python
class ReactPlanner:
    def __init__(
        self,
        llm: str | dict,                        # main LLM via LiteLLM
        nodes: Sequence[Node],
        *,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        token_budget: int | None = None,        # summarization threshold
        pause_enabled: bool = True,
        state_store: Any | None = None,
        # NEW:
        summarizer_llm: str | dict | None = None,   # cheaper model for Phase B compaction
        system_prompt_extra: str | None = None,     # append-only dev guidance
        planning_hints: dict | None = None,         # structured constraints/guidance (see schema above)
    ) -> None: ...
```

### Prompting Changes

* **Planner system prompt** now appends:

  * `system_prompt_extra` (verbatim, if provided)
  * A compact rendering of `planning_hints`:

    * “**Respect** the following constraints: …”
    * “Preferred order (if applicable): …”
    * “Allowed parallel groups: …; do not exceed `max_parallel`.”
    * “Disallowed tools: … (never call).”
* **Summarizer prompt** (when compaction needed):

  * Ultra-brief instructions, JSON-only output, keep **facts/pending/goals**.
  * If `summarizer_llm` not set, try rule-based compressor first; if still too long, use main `llm`.

### Execution Semantics

* **Hard constraints enforced in code**, not just via prompt:

  * `disallow_nodes`: reject planner actions referencing these nodes and ask for a corrected plan.
  * `max_parallel`: cap concurrent actions at executor level.
  * `sequential_only`: prevent those nodes from appearing in a parallel group; auto-rewrite plan or request revision.
* **Soft preferences** (e.g., `prefer_nodes`, `ordering_hints`): prompt-level nudge; if violated, do **one** corrective iteration before proceeding.

### Tests (additions)

* **Summarizer LLM**

  * Uses cheaper model when set; falls back to main LLM only if summarizer fails.
  * Rule-based shrink first → summarizer second; ensure output remains valid and helpful for re-planning.
* **Prompt Hints**

  * Plans follow `ordering_hints` when feasible; if not, we get a single correction pass.
  * `disallow_nodes` never executed (guard tested).
  * `max_parallel` enforced even if LLM proposes larger fan-out.

### Examples (update)

* `examples/react_pause_resume/`

  * Provide `summarizer_llm="gpt-4o-mini"` (or any LiteLLM alias).
  * Show `system_prompt_extra` with a domain-aware decomposition.
  * Show `planning_hints`:

    ```python
    planner = ReactPlanner(
        llm="gpt-4o",
        summarizer_llm="gpt-4o-mini",
        nodes=[triage, retrieve_docs, rerank, summarize, apply_compliance, send_email],
        system_prompt_extra=(
          "Break the query into minimal steps. Prefer cached_search when available."
        ),
        planning_hints={
          "ordering_hints": ["triage", "retrieve_docs", "rerank", "summarize"],
          "parallel_groups": [["retrieve_docs_A","retrieve_docs_B"]],
          "sequential_only": ["apply_compliance","send_email"],
          "disallow_nodes": ["expensive_tool_v1"],
          "prefer_nodes": ["cached_search"],
          "budget_hints": {"max_parallel": 2, "max_cost_usd": 0.05}
        }
    )
    ```

### Error Handling & Fallbacks

* If summarizer returns invalid JSON:

  * 1–2 JSON repair attempts; then fall back to rule-based truncation with a clear `summary_note`.
* If hints produce an impossible plan (e.g., required node is disallowed):

  * Planner requests a **constraint-relaxation** from caller (returns `PlannerPause` with `reason="constraints_conflict"`), or falls back to a safe, minimal path if allowed.

---

### Rationale

* **Cheaper summarizer** slashes cost for iterative/planning-heavy sessions.
* **Developer hints** exploit domain knowledge about your nodes (ordering, parallelism, risk), yielding faster, cheaper, and more reliable plans—without hardcoding flows or adding weight to the core.


# Goal ergonomics for Node's Tool metadata addition

Let developers attach **tool metadata** to nodes so the planner can build a tool catalog:

* `desc`: short human description
* `side_effects`: `"read" | "write" | "external" | "stateful" | "pure"`
* optional hints (cost, latency, auth, safety tags, preferred order, etc.)
* automatic JSON Schema for args/out from your existing Pydantic models

# Design (no breaking changes)

## 1) A tiny `NodeSpec` dataclass (new module: `penguiflow/catalog.py`)

```python
# penguiflow/catalog.py
from __future__ import annotations
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
    cost_hint: Optional[str] = None         # e.g. "low", "med", "high" or "$$"
    latency_hint_ms: Optional[int] = None   # rough typical latency
    safety_notes: Optional[str] = None
    # free-form extras for planner-specific hints:
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_tool_record(self) -> dict[str, Any]:
        # Used by the planner to render a compact tool descriptor + JSON schemas.
        return {
            "name": self.name,
            "desc": self.desc,
            "side_effects": self.side_effects,
            "tags": list(self.tags),
            "auth_scopes": list(self.auth_scopes),
            "cost_hint": self.cost_hint,
            "latency_hint_ms": self.latency_hint_ms,
            "safety_notes": self.safety_notes,
            "args_schema": self._schema(self.args_model),
            "out_schema": self._schema(self.out_model),
            "extra": dict(self.extra),
        }

    @staticmethod
    def _schema(model: Type[BaseModel]) -> dict[str, Any]:
        # Pydantic v2 JSON Schema
        return model.model_json_schema()
```

## 2) Three ergonomic ways to provide specs (pick any)

### A) **Decorator** on the node function (most ergonomic, preferred method)

```python
# penguiflow/catalog.py
from typing import Callable
def tool(
    *,
    name: str | None = None,
    desc: str,
    side_effects: SideEffect = "pure",
    **kw,
):
    """Annotate a node function with tool-like metadata for the planner."""
    def wrap(fn: Callable):
        setattr(fn, "_pf_tool_meta", {"name": name, "desc": desc, "side_effects": side_effects, **kw})
        return fn
    return wrap
```

Usage:

```python
from pydantic import BaseModel
from penguiflow.node import Node
from penguiflow.catalog import tool

class SearchArgs(BaseModel):
    topic: str
    k: int = 5

class SearchOut(BaseModel):
    docs: list[str]

@tool(desc="KB search over internal docs", side_effects="read", tags=["search","docs"])
async def search_docs(msg: SearchArgs, ctx) -> SearchOut:
    ...

search_node = Node(search_docs, name="search_docs")
```

The planner (or a helper) can read `search_docs._pf_tool_meta` and combine it with the node + registry types to produce a `NodeSpec`.

### B) **Node wrapper method** (no decorator needed)

Add a tiny helper in `catalog.py`:

```python
def describe_node(
    node: Node,
    *,
    desc: str,
    side_effects: SideEffect = "pure",
    **kw,
) -> Node:
    """Attach tool-like metadata to an existing Node instance."""
    node.meta = getattr(node, "meta", {})  # ensure attr
    node.meta["pf_tool_meta"] = {"desc": desc, "side_effects": side_effects, **kw}
    return node
```

Usage:

```python
search_node = describe_node(
    Node(search_docs, name="search_docs"),
    desc="KB search over internal docs",
    side_effects="read",
    tags=["search","docs"],
)
```

### C) **External catalog** (no changes to nodes at all)

```python
from penguiflow.catalog import NodeSpec

specs = [
    NodeSpec(
        node=search_node,
        name="search_docs",
        desc="KB search",
        args_model=SearchArgs, out_model=SearchOut,
        side_effects="read",
    ),
    # ...
]
```

This is useful when you don’t control node source or want per-deployment descriptions.

## 3) Catalog builder: turn Nodes into Specs

```python
# penguiflow/catalog.py
from penguiflow.registry import ModelRegistry

def build_catalog(
    nodes: list[Node],
    registry: ModelRegistry | None = None,
    overrides: dict[str, dict] | None = None,  # optional per-node metadata overrides
) -> list[NodeSpec]:
    specs: list[NodeSpec] = []
    for n in nodes:
        # Try metadata from decorator or describe_node
        meta = getattr(getattr(n.func, "_pf_tool_meta", None), "copy", lambda: {})()
        if not meta:
            meta = dict(getattr(getattr(n, "meta", {}), "get", lambda *_: {})("pf_tool_meta") or {})
        if overrides and n.name in overrides:
            meta.update(overrides[n.name])

        # Infer name/desc fallbacks
        name = meta.get("name") or n.name
        desc = meta.get("desc") or (n.func.__doc__ or "").strip() or f"{name} (no description)"
        side_effects = meta.get("side_effects", "pure")

        # Resolve I/O models (prefer registry; fallback to Node’s known adapters)
        # Assume you already have registry.register(name, In, Out)
        if registry:
            in_model, out_model = registry.get_io_models(name)  # you may add this helper
        else:
            in_model, out_model = n.in_model, n.out_model       # if you store them on Node

        specs.append(NodeSpec(
            node=n, name=name, desc=desc,
            args_model=in_model, out_model=out_model,
            side_effects=side_effects,
            tags=tuple(meta.get("tags", ())),
            auth_scopes=tuple(meta.get("auth_scopes", ())),
            cost_hint=meta.get("cost_hint"),
            latency_hint_ms=meta.get("latency_hint_ms"),
            safety_notes=meta.get("safety_notes"),
            extra=meta.get("extra", {}),
        ))
    return specs
```

> If `ModelRegistry` doesn’t expose `get_io_models(name)`, we can implement a tiny helper or store `in_model/out_model` on `Node` at registration time (non-breaking).

## 4) Planner consumption (zero friction)

Your planner simply accepts `nodes` **or** `catalog`:

```python
# planner/react.py
class ReactPlanner:
    def __init__(self, llm, nodes: list[Node] | None = None, catalog: list[NodeSpec] | None = None, **kw):
        if catalog is None:
            assert nodes is not None, "Provide nodes or a prebuilt catalog"
            catalog = build_catalog(nodes, registry=kw.get("registry"), overrides=kw.get("node_overrides"))
        self._tool_records = [spec.to_tool_record() for spec in catalog]
```

The prompt then lists tools succinctly:

```text
TOOLS (JSON):
1) search_docs — KB search (read)
   args_schema: {...}
   out_schema: {...}
2) query_sql — Run SQL (read)
3) send_slack — Post to Slack (write)
```

# Extra niceties (optional but useful)

* **Docstring fallback**: If no `desc`, use function docstring; second fallback is model docstrings (Pydantic supports `Field(description="...")`).
* **Side-effect-aware constraints**: Planner can apply rules (“avoid `write` unless approved”; “never call `stateful` in parallel”).
* **Cost/latency shaping**: Use `cost_hint` and `latency_hint_ms` to penalize expensive/slow nodes in the planner’s selection step.
* **Auth scopes**: If caller lacks scopes, the planner blocks those nodes (hard constraint).
* **Tags**: Great for domain routing (“finance”, “customer_support”) and for developer hints you added in Phase B.

# End-to-end example

```python
from pydantic import BaseModel, Field
from penguiflow import Node
from penguiflow.catalog import tool, build_catalog
from penguiflow.planner import ReactPlanner

class SearchArgs(BaseModel):
    topic: str = Field(..., description="Topic or query")
    k: int = 5

class SearchOut(BaseModel):
    docs: list[str]

@tool(desc="KB search over internal docs", side_effects="read", tags=["search","docs"])
async def search_docs(msg: SearchArgs, ctx) -> SearchOut:
    """Search the internal knowledge base for relevant documents."""
    # impl...
    ...

search_node = Node(search_docs, name="search_docs")

catalog = build_catalog([search_node], registry=my_registry)

planner = ReactPlanner(
    llm="gpt-4o-mini",
    catalog=catalog,
    summarizer_llm="gpt-4o-mini",          # Phase B option
    system_prompt_extra="Prefer cached_search when available.",
    planning_hints={"sequential_only": ["send_slack"], "max_parallel": 2},
)

result = await planner.run("Share last month's metrics to Slack")
```

# Why this works

* **Ergonomic**: use a decorator, a one-liner `describe_node`, or an external `NodeSpec`—whatever fits your codebase.
* **Typed**: args/out schemas come straight from your models (no duplicate typing).
* **Lightweight**: no core API break; everything is additive.
* **Planner-ready**: you get a clean, compact tool catalog with side-effects and hints the planner can actually use.


