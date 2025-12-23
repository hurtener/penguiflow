# PenguiFlow: React Planner Integration Guide (v2.4+)

> **v2.7 Update**: Planner now returns structured `FinalPayload` with `raw_answer` field. All examples updated to show the new format. See [Handling Different Result Types](#handling-different-result-types) for payload extraction patterns.

This guide is the **source of truth** for wiring `ReactPlanner` into a production agent. It captures patterns from `planner_enterprise_agent_v2`, `react_typed_tools`, `react_parallel_join`, and `react_pause_resume`.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Quick Start](#2-quick-start)
3. [Understanding Context Types](#3-understanding-context-types)
   - [Short-Term Memory (Optional)](#short-term-memory-optional)
4. [Building an Orchestrator](#4-building-an-orchestrator)
5. [Designing Tools with ToolContext](#5-designing-tools-with-toolcontext)
6. [Pause/Resume Patterns](#6-pauseresume-patterns)
7. [Parallel Execution](#7-parallel-execution)
8. [Reflection Loop](#8-reflection-loop)
9. [Planning Hints and Tool Policy](#9-planning-hints-and-tool-policy)
10. [Observability and Telemetry](#10-observability-and-telemetry)
11. [Testing Patterns](#11-testing-patterns)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick Reference](#13-quick-reference)

---

## 1. Overview

### Architecture

```
┌─────────────┐    run/resume      ┌───────────────┐    tool calls     ┌────────┐
│ Orchestrator│ ─────────────────▶ │ ReactPlanner  │ ────────────────▶ │ Tools  │
└─────────────┘ ◀──── events ───── └───────────────┘ ◀── results/pause └────────┘
        │                                 │
        └─ status/logs → sinks            └─ enforces budgets, hints, reflection
```

### Key Contracts

- **Catalog**: `NodeSpec` set with input/output schemas (built via `build_catalog`)
- **LLM**: JSON-only responses (LiteLLM or custom client)
- **Context surfaces**: `llm_context` for LLM prompt, `tool_context` for tools
- **Pause/Resume**: `PlannerPause` round-trips through orchestrator
- **Parallel**: `plan` + `join` with explicit injection sources

### What Changed in v2.4

| Before (v2.3) | After (v2.4) |
|---------------|--------------|
| `context_meta` parameter | `llm_context` + `tool_context` |
| `_SerializableContext` wrapper | Direct dict separation |
| `ctx: Any` in tools | `ctx: ToolContext` with protocol |
| Magic join field injection | Explicit `join.inject` mapping |

---

## 2. Quick Start

### Minimal Example

```python
import asyncio
from pydantic import BaseModel
from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.registry import ModelRegistry

# Define models
class Query(BaseModel):
    text: str

class Answer(BaseModel):
    response: str

# Define a tool
@tool(desc="Answer a question", tags=["answer"])
async def answer_query(args: Query, ctx: ToolContext) -> Answer:
    return Answer(response=f"Response to: {args.text}")

# Build and run
async def main():
    registry = ModelRegistry()
    registry.register("answer_query", Query, Answer)

    nodes = [Node(answer_query, name="answer_query")]
    catalog = build_catalog(nodes, registry)

    planner = ReactPlanner(
        llm="gpt-4o-mini",
        catalog=catalog,
        max_iters=4,
    )

    result = await planner.run("What is 2+2?")

    # Extract structured payload (v2.7+)
    payload = result.payload
    print(f"Answer: {payload['raw_answer']}")
    print(f"Confidence: {payload.get('confidence')}")
    print(f"Artifacts: {payload.get('artifacts', {})}")

asyncio.run(main())
```

**Expected Output:**
```
Answer: The sum of 2+2 is 4.
Confidence: 0.95
Artifacts: {}
```

---

## 3. Understanding Context Types

This is the **most critical concept** in v2.4. Understanding the separation between `llm_context` and `tool_context` is essential.

### The Two Context Surfaces

```python
# llm_context: Sent to the LLM in the prompt
# - MUST be JSON-serializable
# - Contains data the LLM needs for planning decisions
# - Examples: memories, preferences, conversation history

# tool_context: Available ONLY to tools via ctx.tool_context
# - Can contain ANY Python objects
# - Never sent to LLM
# - Examples: callbacks, loggers, database connections
```

### Why Separate Contexts?

```python
# PROBLEM (v2.3): Everything mixed together
context = {
    "memories": [...],           # LLM should see this
    "status_publisher": fn,      # LLM CANNOT see this (not JSON-serializable!)
}
# Had to use _SerializableContext wrapper to filter non-serializable values

# SOLUTION (v2.4): Explicit separation
llm_context = {"memories": [...]}          # JSON only, LLM sees
tool_context = {"status_publisher": fn}    # Anything, tools only
```

### The Serialization Boundary

```
┌─────────────────────────────────────────────────────────────────┐
│                        Orchestrator                             │
│  ┌─────────────────┐      ┌────────────────────────────────┐   │
│  │   llm_context   │      │         tool_context           │   │
│  │  (JSON only)    │      │     (callbacks, loggers)       │   │
│  └────────┬────────┘      └──────────────┬─────────────────┘   │
└───────────┼──────────────────────────────┼─────────────────────┘
            │                              │
            ▼                              │
┌───────────────────────┐                  │
│     LLM Prompt        │                  │
│  ┌─────────────────┐  │                  │
│  │ User message    │  │                  │
│  │ with context:   │  │                  │
│  │ {memories:...}  │  │                  │
│  └─────────────────┘  │                  │
└───────────────────────┘                  │
                                           │
┌──────────────────────────────────────────┼─────────────────────┐
│                        Tool Execution    │                     │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                       ToolContext                        │  │
│  │  ctx.llm_context → read-only view of llm_context        │  │
│  │  ctx.tool_context → mutable tool_context ◄──────────────┼──┘
│  │  ctx.meta → deprecated ChainMap (both combined)         │  │
│  └─────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### What Goes Where?

| Data | Context | Reason |
|------|---------|--------|
| User preferences | `llm_context` | LLM needs for personalization |
| Conversation history | `llm_context` | LLM needs for continuity |
| Status history (as JSON) | `llm_context` | LLM can track progress |
| Status publisher callback | `tool_context` | Not JSON-serializable |
| trace_id, tenant_id | `tool_context` | Internal routing metadata |
| Telemetry/logger objects | `tool_context` | Not JSON-serializable |
| Database connections | `tool_context` | Runtime resources |

### Validation

The planner validates `llm_context` on startup:

```python
# This will raise TypeError immediately
result = await planner.run(
    query="...",
    llm_context={"callback": lambda x: x},  # TypeError: not JSON-serializable
)
```

### Short-Term Memory (Optional)

PenguiFlow supports **opt-in short-term memory** for `ReactPlanner` to maintain conversation continuity across multiple `run()` calls within a single session.

Key properties:
- Memory is injected via `llm_context` (user prompt), not the system prompt.
- Memory is isolated by `MemoryKey(tenant_id, user_id, session_id)` and fails closed by default when no key is available.
- Persistence is optional via duck-typed `state_store.save_memory_state` / `state_store.load_memory_state`.

For a full deep dive (strategies, budgets, persistence, troubleshooting), see `docs/MEMORY_GUIDE.md`.

#### Enable memory

```python
from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import MemoryBudget, ShortTermMemoryConfig

planner = ReactPlanner(
    llm="gpt-4o-mini",
    catalog=catalog,
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        budget=MemoryBudget(full_zone_turns=5, total_max_tokens=8000),
    ),
    system_prompt_extra=(
        "If context.conversation_memory is present, it contains conversation history (recent turns and an optional "
        "summary). Use it to maintain continuity and avoid repeating questions."
    ),
)
```

#### Provide a memory key (recommended)

```python
from penguiflow.planner.memory import MemoryKey

key = MemoryKey(tenant_id="acme", user_id="u123", session_id="chat_001")
result = await planner.run("First turn", memory_key=key)
result = await planner.run("Second turn", memory_key=key)
```

#### Derive the key from tool_context (optional)

`tool_context` is tools-only (not shown to the LLM), so it is the preferred place for internal routing metadata:

```python
tool_context = {"tenant_id": "acme", "user_id": "u123", "session_id": "chat_001"}
result = await planner.run("Continue", tool_context=tool_context)
```

#### Pause/resume + memory

If you rely on pause/resume, pass the same key on resume:

```python
pause = await planner.run("Do something sensitive", memory_key=key)
final = await planner.resume(pause.resume_token, user_input="approved", memory_key=key)
```

---

## 4. Building an Orchestrator

### Complete Production Pattern

```python
from collections import defaultdict
from uuid import uuid4
from penguiflow.planner import ReactPlanner, PlannerPause

# Global buffers (in production: Redis/message queue)
STATUS_BUFFER: defaultdict[str, list[StatusUpdate]] = defaultdict(list)

class AgentOrchestrator:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._planner = self._build_planner()

    def _build_planner(self) -> ReactPlanner:
        # Build catalog from nodes
        nodes = [
            Node(triage, name="triage"),
            Node(search, name="search"),
            Node(respond, name="respond"),
        ]

        registry = ModelRegistry()
        registry.register("triage", Query, Intent)
        registry.register("search", Intent, Documents)
        registry.register("respond", Documents, Answer)

        catalog = build_catalog(nodes, registry)

        return ReactPlanner(
            llm=self.config.llm_model,
            catalog=catalog,
            max_iters=self.config.max_iters,
            temperature=0.0,
            event_callback=self._on_planner_event,  # Observability
        )

    async def execute(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        memories: list[dict] | None = None,
    ) -> FinalAnswer:
        trace_id = uuid4().hex
        status_history: list[StatusUpdate] = STATUS_BUFFER[trace_id]
        status_history_for_llm: list[dict] = []  # JSON version for LLM

        def publish_status(update: StatusUpdate) -> None:
            status_history.append(update)
            status_history_for_llm.append(update.model_dump())
            # Push to WebSocket/Redis/etc.

        # CRITICAL: Separate context surfaces
        llm_context = {
            "status_history": status_history_for_llm,
        }
        if memories:
            llm_context["memories"] = memories

        tool_context = {
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "status_publisher": publish_status,
            "telemetry": self.telemetry,
        }

        result = await self._planner.run(
            query=query,
            llm_context=llm_context,
            tool_context=tool_context,
        )

        if isinstance(result, PlannerPause):
            return FinalAnswer(
                text="Paused awaiting input.",
                route="pause",
                metadata={
                    "resume_token": result.resume_token,
                    "reason": result.reason,
                    "payload": dict(result.payload),
                },
            )

        # Payload is a FinalPayload dict with raw_answer field
        payload = result.payload
        return FinalAnswer(
            text=payload["raw_answer"],
            artifacts=payload.get("artifacts", {}),
            confidence=payload.get("confidence"),
        )
```

### Handling Different Result Types

```python
result = await planner.run(query, llm_context=..., tool_context=...)

if isinstance(result, PlannerPause):
    # Tool requested human input
    return handle_pause(result)

elif result.reason == "answer_complete":
    # Success - extract payload (always has raw_answer)
    payload = result.payload  # FinalPayload dict
    return FinalAnswer(
        text=payload["raw_answer"],
        artifacts=payload.get("artifacts", {}),
        confidence=payload.get("confidence"),
        sources=payload.get("sources", []),
    )

elif result.reason == "no_path":
    # LLM couldn't find a solution
    return FinalAnswer(
        text=f"Could not complete: {result.metadata.get('thought')}",
        route="error",
    )

elif result.reason == "budget_exhausted":
    # Hit deadline, hop limit, or cost limit
    return FinalAnswer(
        text="Task interrupted due to resource constraints.",
        route="error",
    )
```

---

## 5. Designing Tools with ToolContext

### The ToolContext Protocol

```python
from penguiflow.planner import ToolContext

class ToolContext(Protocol):
    @property
    def llm_context(self) -> Mapping[str, Any]:
        """Context visible to LLM (read-only)."""

    @property
    def tool_context(self) -> dict[str, Any]:
        """Tool-only context (callbacks, telemetry)."""

    @property
    def meta(self) -> MutableMapping[str, Any]:
        """Combined context. DEPRECATED: use llm_context/tool_context."""

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> PlannerPause:
        """Pause execution for human input."""

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit a streaming chunk."""
```

### Tool Design Pattern

```python
from penguiflow.catalog import tool
from penguiflow.planner import ToolContext

def _publish_status(ctx: ToolContext, message: str) -> None:
    """Helper to safely publish status updates."""
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(StatusUpdate(status="thinking", message=message))

@tool(desc="Search for documents", side_effects="read", tags=["search"])
async def search_docs(args: SearchArgs, ctx: ToolContext) -> SearchResult:
    # 1. Get LLM-visible context if needed
    preferences = ctx.llm_context.get("preferences", {})

    # 2. Get tool-only resources
    _publish_status(ctx, f"Searching for: {args.query}")

    # 3. Optional: Emit streaming chunks for long operations
    await ctx.emit_chunk("search", 0, "Starting search...")

    results = await backend.search(args.query)

    await ctx.emit_chunk("search", 1, f"Found {len(results)} results", done=True)

    return SearchResult(docs=results)
```

### Tools That Work in Both Flow and Planner

Use `AnyContext` for shared tools:

```python
from penguiflow.planner import AnyContext  # ToolContext | FlowContext

@tool(desc="Fetch data")
async def fetch_data(args: FetchArgs, ctx: AnyContext) -> FetchResult:
    # Try tool_context first (planner), fall back to meta (flow)
    if hasattr(ctx, "tool_context"):
        trace_id = ctx.tool_context.get("trace_id")
    else:
        trace_id = ctx.meta.get("trace_id")

    # ... implementation
```

---

## 6. Pause/Resume Patterns

### Triggering a Pause from a Tool

```python
@tool(desc="Request user approval", side_effects="external")
async def request_approval(args: ApprovalArgs, ctx: ToolContext) -> ApprovalResult:
    # Pause the planner - this raises internally and returns control
    await ctx.pause(
        "approval_required",
        {"action": args.action, "reason": args.reason},
    )
    # This line is unreachable but satisfies type checkers
    return ApprovalResult(approved=False)
```

### Pause Reasons

| Reason | Use Case |
|--------|----------|
| `approval_required` | Human must approve an action |
| `await_input` | Need clarification or additional data |
| `external_event` | Waiting for external system callback |
| `constraints_conflict` | Multiple valid paths, need guidance |

### Resuming After Pause

```python
# In orchestrator
result = await planner.run(query, llm_context=..., tool_context=...)

if isinstance(result, PlannerPause):
    # Store resume_token for later
    store_pause_state(result.resume_token, result.payload)
    return {"status": "paused", "resume_token": result.resume_token}

# Later, when user provides input
async def handle_resume(token: str, user_input: str):
    final = await planner.resume(
        token=token,
        user_input=user_input,
        tool_context={"status_publisher": publish_status},  # Refresh callbacks
    )
    return FinalAnswer.model_validate(final.payload)
```

---

## 7. Parallel Execution

### Parallel Fan-Out with Explicit Join Injection

The planner can execute multiple tools in parallel, then merge results with a join node.

### LLM Action Structure

```json
{
  "thought": "Search multiple providers in parallel",
  "plan": [
    {"node": "search_catalog", "args": {"provider": "wiki"}},
    {"node": "search_catalog", "args": {"provider": "arxiv"}}
  ],
  "join": {
    "node": "merge_results",
    "inject": {
      "branch_outputs": "$results",
      "total_requests": "$expect",
      "failures": "$failures",
      "failure_count": "$failure_count",
      "success_count": "$success_count"
    },
    "args": {"note": "additional static args"}
  }
}
```

### Injection Sources

| Source | Type | Description |
|--------|------|-------------|
| `$results` | `list[T]` | Successful branch outputs |
| `$expect` | `int` | Total branches that were planned |
| `$branches` | `list[dict]` | Full branch data including args |
| `$failures` | `list[dict]` | Failed branch error details |
| `$success_count` | `int` | Number of successful branches |
| `$failure_count` | `int` | Number of failed branches |

### Join Tool Example

```python
class JoinArgs(BaseModel):
    """Join node for parallel fan-out."""
    branch_outputs: list[SearchResult]  # Injected via $results
    total_requests: int                  # Injected via $expect
    failures: list[dict] = Field(default_factory=list)  # Via $failures
    failure_count: int = 0               # Via $failure_count
    success_count: int = 0               # Via $success_count

@tool(desc="Merge parallel search results", side_effects="pure")
async def merge_results(args: JoinArgs, ctx: ToolContext) -> MergedResult:
    _publish_status(ctx, f"Merging {args.success_count}/{args.total_requests} results")

    if args.failure_count > 0:
        # Handle partial failures
        logger.warning(f"Partial failures: {args.failures}")

    merged = []
    for result in args.branch_outputs:
        merged.extend(result.docs)

    return MergedResult(
        all_docs=merged,
        note=f"Merged from {args.success_count} providers",
        partial=args.failure_count > 0,
    )
```

### Why Explicit Injection?

**Before (magic injection - deprecated):**
```python
class JoinArgs(BaseModel):
    results: list[T]  # Magically injected if named exactly "results"
    expect: int       # Magically injected if named exactly "expect"
    # What if I name it "data"? Nothing happens. Surprise!
```

**After (explicit injection):**
```python
class JoinArgs(BaseModel):
    branch_outputs: list[T]  # Any name works
    total_branches: int      # Any name works

# LLM specifies the mapping explicitly:
# "inject": {"branch_outputs": "$results", "total_branches": "$expect"}
```

---

## 8. Reflection Loop

The reflection loop critiques answers before finishing, enabling quality assurance.

### Configuration

```python
from penguiflow.planner import ReactPlanner, ReflectionConfig, ReflectionCriteria

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    reflection_config=ReflectionConfig(
        enabled=True,
        criteria=ReflectionCriteria(
            completeness="Addresses all parts of the query",
            accuracy="Factually correct based on observations",
            clarity="Well-explained and coherent",
        ),
        quality_threshold=0.80,  # Score 0-1 that must be exceeded
        max_revisions=2,         # Retry up to 2 times if below threshold
        use_separate_llm=False,  # Use same LLM for critique
    ),
    reflection_llm="gpt-4o-mini",  # Optional: cheaper LLM for critique
)
```

### How It Works

1. LLM generates an answer
2. Reflection LLM critiques it against criteria
3. If score < threshold, LLM revises with feedback
4. Repeat up to `max_revisions` times
5. Return best answer (or clarification if all attempts fail)

### Clarification Response

If reflection fails after all revisions:

```python
ClarificationResponse(
    text="I couldn't fully answer your question about X because...",
    confidence="unsatisfied",
    attempted_approaches=["search_docs", "query_database"],
    clarifying_questions=["Could you specify which date range?"],
    suggestions=["More context about the specific error would help"],
    reflection_score=0.65,
    revision_attempts=2,
)
```

---

## 9. Planning Hints and Tool Policy

### Planning Hints

Guide the planner without hard constraints:

```python
planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    planning_hints={
        # Suggested tool order
        "ordering_hints": ["triage", "approval", "retrieve", "respond"],

        # Tools that can run in parallel
        "parallel_groups": [["search_wiki", "search_arxiv"]],

        # Tools to never use
        "disallow_nodes": ["deprecated_tool"],

        # Preferred tools when multiple options exist
        "preferred_nodes": ["fast_search"],

        # Resource constraints
        "budget_hints": {
            "max_parallel": 3,
            "max_cost_usd": 0.10,
        },
    },
)
```

### Tool Policy

Runtime access control for multi-tenant deployments:

```python
from penguiflow.planner import ToolPolicy

# Whitelist mode
policy = ToolPolicy(
    allowed_tools={"search", "respond"},  # Only these tools available
)

# Blacklist mode
policy = ToolPolicy(
    denied_tools={"admin_action", "delete_data"},  # Block specific tools
)

# Tag-based access
policy = ToolPolicy(
    require_tags={"safe"},  # Only tools with "safe" tag
)

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    tool_policy=policy,
)
```

---

## 10. Observability and Telemetry

### Event Callback

```python
from penguiflow.planner import PlannerEvent

def record_event(evt: PlannerEvent) -> None:
    logger.info(
        "planner_event",
        extra={
            "type": evt.event_type,
            "step": evt.trajectory_step,
            "node": evt.node_name,
            "latency_ms": evt.latency_ms,
        },
    )

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    event_callback=record_event,
)
```

### Event Types

| Event | When Emitted |
|-------|--------------|
| `step_start` | Before executing a tool |
| `step_complete` | After tool returns |
| `llm_call` | After LLM responds |
| `pause` | Tool triggers pause |
| `resume` | Planner resumes |
| `finish` | Planning completes |
| `error` | Exception occurs |
| `reflection_critique` | Reflection scores an answer |
| `stream_chunk` | Tool emits streaming chunk |
| `planner_repair_attempt` | LLM response failed JSON/schema validation |
| `planner_args_invalid` | Tool args rejected by validation policy |
| `planner_args_suspect` | Suspect tool args detected (telemetry only) |

### Metrics in PlannerFinish

```python
result = await planner.run(...)
if isinstance(result, PlannerFinish):
    print(result.metadata)
    # {
    #   "step_count": 5,
    #   "total_latency_ms": 1234,
    #   "constraints": {
    #     "hops_used": 5,
    #     "hops_budget": 10,
    #     "deadline_remaining_s": 45.2,
    #   },
    #   "reflection": {
    #     "final_score": 0.85,
    #     "revision_count": 1,
    #   }
    # }
```

### Capturing Repair Telemetry

ReactPlanner emits a `planner_repair_attempt` event whenever the LLM response
fails JSON/schema validation. Use this to build ACE/GEPA feedback loops and
compare model reliability.

Event payload fields:
- `step`: planner step index (already in the event payload)
- `attempt`: 1-based attempt number within the step
- `error_type`: validation exception type
- `error_summary`: short, sanitised error string
- `next_node_detected`: tool name if salvage inferred it, else null
- `response_len`: length of the raw LLM response (no content stored)
- `had_code_fence`: true if response included ``` fences
- `had_non_json_prefix`: true if response started with non-JSON text

Example event capture:

```python
from penguiflow.planner import PlannerEvent

def record_event(evt: PlannerEvent) -> None:
    if evt.event_type == "planner_repair_attempt":
        payload = evt.to_payload()
        # Persist to your event store or metrics pipeline
        logger.warning("planner_repair_attempt", extra=payload)

planner = ReactPlanner(
    llm="gpt-4o",
    catalog=catalog,
    event_callback=record_event,
)
```

Summary counters are also surfaced on completion:

```python
result = await planner.run(...)
if isinstance(result, PlannerFinish):
    print(result.metadata["validation_failures_count"])
    print(result.metadata["repair_attempts"])
    print(result.metadata["salvage_used"])
    print(result.metadata["consecutive_arg_failures"])
```

If you persist trajectories (e.g., via the Playground state store), detailed
invalid-response entries are attached under `trajectory.metadata.invalid_responses`
with the same sanitised fields. This avoids storing raw LLM text by default.

### Tool Arg Validation (Production Guardrails)

ReactPlanner can detect and optionally reject placeholder/tool-arg issues without
hard-coding global heuristics. Configure this per tool via the `extra` metadata
passed to the `@tool(...)` decorator:

```python
from penguiflow import tool
from pydantic import BaseModel

class QueryArgs(BaseModel):
    query: str

@tool(
    desc="Search the analytics index.",
    extra={
        "arg_validation": {
            "reject_placeholders": True,
            "reject_autofill": True,
            "placeholders": ["<auto>", "unknown", "n/a"],
            "emit_suspect": True,
        }
    },
)
async def search(args: QueryArgs, ctx):
    ...
```

Policy fields:
- `reject_placeholders`: block calls when placeholder strings are detected
- `reject_autofill`: block calls when required args were auto-filled
- `placeholders`: list of strings to treat as placeholders (defaults to `<auto>`)
- `emit_suspect`: emit telemetry for placeholder/autofill signals (default true)

You can also attach a custom validator:

```python
def my_arg_validator(parsed_args, action):
    if len(parsed_args.query.split()) < 3:
        return "query too short"
    return None

@tool(extra={"arg_validator": my_arg_validator})
async def search(args: QueryArgs, ctx):
    ...
```

Telemetry:
- `planner_args_invalid` is emitted when validation rejects args
- `planner_args_suspect` is emitted when placeholder/autofill signals are detected

Both events include: `tool`, `error_summary`, `placeholders`, `placeholder_paths`,
`autofilled_fields`, and `source`. Detailed entries are also stored in
`trajectory.metadata.invalid_args` / `trajectory.metadata.suspect_args`.

#### Consecutive Failure Threshold

Small models may loop indefinitely on invalid args, retrying the same tool with
bad arguments. To prevent this, ReactPlanner includes a threshold that forces
early termination:

```python
planner = ReactPlanner(
    llm="llama-3.2-3b",
    catalog=catalog,
    max_consecutive_arg_failures=3,  # Default: 3
)
```

When the threshold is reached:
- Planner finishes with `reason="no_path"`
- `payload.requires_followup = True` signals the caller that user input is needed
- `payload.failure_reason = "consecutive_arg_failures"` identifies the cause
- `metadata.consecutive_arg_failures` contains the count

The counter resets whenever a tool executes successfully, so interleaving valid
calls prevents false positives.

---

## 11. Testing Patterns

### Scripted LLM for Deterministic Tests

```python
class ScriptedLLM:
    """Returns predefined planner actions."""

    def __init__(self, actions: list[dict]) -> None:
        self._payloads = [json.dumps(a) for a in actions]

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        return self._payloads.pop(0)

# Test
scripted = [
    {"thought": "triage", "next_node": "triage", "args": {"text": "test"}},
    {"thought": "finish", "next_node": None, "args": {"raw_answer": "Complete response"}},
]

planner = ReactPlanner(
    llm_client=ScriptedLLM(scripted),
    catalog=catalog,
)

result = await planner.run("Test query")
assert result.reason == "answer_complete"
assert result.payload["raw_answer"] == "Complete response"
```

### Testing Pause/Resume

```python
scripted = [
    {"thought": "approve", "next_node": "approval", "args": {"intent": "test"}},
    {"thought": "finish", "next_node": None, "args": {"raw_answer": "Approved and completed"}},
]

planner = ReactPlanner(llm_client=ScriptedLLM(scripted), catalog=catalog)

result = await planner.run("Need approval")
assert isinstance(result, PlannerPause)
assert result.reason == "approval_required"

final = await planner.resume(result.resume_token, user_input="approved")
assert final.reason == "answer_complete"
assert final.payload["raw_answer"] == "Approved and completed"
```

### Testing Parallel Execution

```python
scripted = [
    {
        "thought": "parallel search",
        "plan": [
            {"node": "search", "args": {"provider": "a"}},
            {"node": "search", "args": {"provider": "b"}},
        ],
        "join": {
            "node": "merge",
            "inject": {"results": "$results", "count": "$expect"},
        },
    },
    {"thought": "finish", "next_node": None, "args": {"raw_answer": "Search complete with 2 sources"}},
]

result = await planner.run("Search both")
# Verify merge received both results
assert result.payload["raw_answer"] == "Search complete with 2 sources"
```

---

## 12. Troubleshooting

### Common Errors

**TypeError: llm_context must be JSON-serializable**
```python
# Wrong
await planner.run(query, llm_context={"callback": lambda x: x})

# Right
await planner.run(
    query,
    llm_context={},
    tool_context={"callback": lambda x: x},
)
```

**DeprecationWarning: ctx.meta is deprecated**
```python
# Old
trace_id = ctx.meta.get("trace_id")

# New
trace_id = ctx.tool_context.get("trace_id")
# OR for LLM-visible data:
prefs = ctx.llm_context.get("preferences")
```

**Parallel join fields not populated**
```python
# Check that inject mapping uses correct sources
"inject": {
    "outputs": "$results",      # ✓ Valid source
    "outputs": "$output",       # ✗ Invalid - no such source
}
```

**Tool not found in catalog**
```python
# Ensure node is registered:
registry.register("my_tool", InputModel, OutputModel)
nodes.append(Node(my_tool, name="my_tool"))
```

### Debug Logging

```python
import logging
logging.getLogger("penguiflow.planner").setLevel(logging.DEBUG)
```

---

## 13. Quick Reference

### Imports

```python
from penguiflow.planner import (
    # Core
    ReactPlanner,
    PlannerPause,
    PlannerFinish,
    PlannerEvent,

    # Types
    ToolContext,
    AnyContext,

    # Parallel
    JoinInjection,
    ParallelCall,
    ParallelJoin,

    # Reflection
    ReflectionConfig,
    ReflectionCriteria,

    # Policy
    ToolPolicy,
)

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
```

### Minimal Orchestrator Template

```python
async def execute(query: str) -> Answer:
    planner = ReactPlanner(llm="gpt-4o", catalog=catalog)

    result = await planner.run(
        query=query,
        llm_context={"memories": memories},
        tool_context={"status_publisher": publish},
    )

    if isinstance(result, PlannerPause):
        raise PauseRequired(result.resume_token)

    # Extract raw_answer from FinalPayload
    payload = result.payload
    return Answer(
        text=payload["raw_answer"],
        confidence=payload.get("confidence"),
        artifacts=payload.get("artifacts", {}),
    )
```

### Tool Template

```python
@tool(desc="Description here", side_effects="read", tags=["tag"])
async def my_tool(args: MyArgs, ctx: ToolContext) -> MyResult:
    # Get tool-only resources
    publisher = ctx.tool_context.get("status_publisher")

    # Get LLM-visible context
    prefs = ctx.llm_context.get("preferences", {})

    # Do work
    result = await do_something(args.input)

    return MyResult(output=result)
```

### Parallel Join Template

```python
class JoinArgs(BaseModel):
    branch_outputs: list[BranchResult]
    total_requests: int
    failures: list[dict] = Field(default_factory=list)

@tool(desc="Merge parallel results", side_effects="pure")
async def merge(args: JoinArgs, ctx: ToolContext) -> MergedResult:
    return MergedResult(
        merged=[r.data for r in args.branch_outputs],
        partial=len(args.failures) > 0,
    )

# LLM action:
# {
#   "plan": [...],
#   "join": {
#     "node": "merge",
#     "inject": {
#       "branch_outputs": "$results",
#       "total_requests": "$expect",
#       "failures": "$failures"
#     }
#   }
# }
```

---

## Migration Checklist (v2.3 → v2.4)

- [ ] Replace `context_meta` with `llm_context` + `tool_context`
- [ ] Remove `_SerializableContext` wrappers
- [ ] Update tool signatures to use `ToolContext`
- [ ] Add `join.inject` mappings for parallel joins
- [ ] Update orchestrator prompts to mention `llm_context` contract
- [ ] Run lint/type/tests; ensure no deprecation warnings

For concise migration steps, see `docs/MIGRATION_V24.md`.
For end-to-end examples, run `examples/planner_enterprise_agent_v2`.
