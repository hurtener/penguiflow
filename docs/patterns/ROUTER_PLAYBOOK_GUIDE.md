# Router + Playbook Pattern in PenguiFlow

A comprehensive guide showing the **idiomatic PenguiFlow way** to route messages to different playbooks using built-in routers.

---

## 📋 Table of Contents

1. [Core Concepts](#core-concepts)
2. [Architecture Overview](#architecture-overview)
3. [Step-by-Step Implementation](#step-by-step-implementation)
4. [Complete Working Example](#complete-working-example)
5. [Comparison: Router vs ReactPlanner](#comparison-router-vs-reactplanner)

---

## Core Concepts

### What is `call_playbook`?

```python
async def call_playbook(
    playbook: Callable[[], tuple[PenguiFlow, ModelRegistry]],
    parent_msg: Message,
    timeout: float | None = None,
) -> Any:
    """Execute a subflow and return its final payload."""
```

**Key behaviors:**
- Takes a **playbook factory** (function returning `(flow, registry)`)
- Executes the entire subflow with the given message
- Returns the **payload** of the final message (not the Message itself!)
- Automatically propagates `trace_id`, headers, and cancellation

### What is a Playbook?

A **playbook** is a factory function that returns a complete, self-contained flow:

```python
def build_my_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Factory that builds a fresh flow instance."""

    # Define nodes
    node1 = Node(process_step1, name="step1")
    node2 = Node(process_step2, name="step2")

    # Wire them up
    flow = create(
        node1.to(node2),
        node2.to(),  # Terminal
    )

    # Create registry
    registry = ModelRegistry()
    registry.register("step1", InputModel, OutputModel)
    registry.register("step2", OutputModel, FinalModel)

    return flow, registry
```

**Why a factory?** Each call needs a **fresh flow instance** with independent state.

---

## Architecture Overview

```
UserQuery
    ↓
[Triage Node] ──→ RouteDecision
    ↓
[Router Node] ─────┬──→ [Documents Wrapper] ──→ call_playbook(documents_flow) ──→ FinalAnswer
                   ├──→ [Bug Wrapper]       ──→ call_playbook(bug_flow)       ──→ FinalAnswer
                   └──→ [General Wrapper]   ──→ call_playbook(general_flow)   ──→ FinalAnswer
```

**Key insight:** Wrapper nodes receive `Message`, call playbook, get back **payload**, re-wrap in `Message`.

---

## Step-by-Step Implementation

### Step 1: Define Your Playbooks

Each workflow becomes a **playbook factory**:

```python
from penguiflow import create, Node, Message, ModelRegistry
from pydantic import BaseModel

# ============================================================================
# Documents Playbook
# ============================================================================

class DocumentState(BaseModel):
    sources: list[str]
    summary: str | None = None

def build_documents_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Multi-step document analysis pipeline."""

    async def parse_docs(msg: Message, ctx) -> Message:
        state = msg.payload  # DocumentState
        # Simulate parsing
        state.sources = ["README.md", "CHANGELOG.md"]
        return msg.model_copy(update={"payload": state})

    async def generate_summary(msg: Message, ctx) -> Message:
        state = msg.payload  # DocumentState
        state.summary = f"Analyzed {len(state.sources)} documents"
        return msg.model_copy(update={"payload": state})

    parse_node = Node(parse_docs, name="parse")
    summary_node = Node(generate_summary, name="summarize")

    flow = create(
        parse_node.to(summary_node),
        summary_node.to(),
    )

    registry = ModelRegistry()
    registry.register("parse", Message, Message)
    registry.register("summarize", Message, Message)

    return flow, registry

# ============================================================================
# Bug Playbook
# ============================================================================

class BugState(BaseModel):
    logs: list[str] = []
    recommendation: str | None = None

def build_bug_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Bug diagnosis pipeline."""

    async def collect_logs(msg: Message, ctx) -> Message:
        state = msg.payload  # BugState
        state.logs = ["ERROR: ValueError", "Traceback..."]
        return msg.model_copy(update={"payload": state})

    async def recommend_fix(msg: Message, ctx) -> Message:
        state = msg.payload  # BugState
        state.recommendation = f"Found {len(state.logs)} errors. Fix: Check config."
        return msg.model_copy(update={"payload": state})

    logs_node = Node(collect_logs, name="collect_logs")
    fix_node = Node(recommend_fix, name="recommend")

    flow = create(
        logs_node.to(fix_node),
        fix_node.to(),
    )

    registry = ModelRegistry()
    registry.register("collect_logs", Message, Message)
    registry.register("recommend", Message, Message)

    return flow, registry

# ============================================================================
# General Playbook (Simple)
# ============================================================================

def build_general_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Simple direct answer."""

    async def answer(msg: Message, ctx) -> Message:
        query = msg.payload  # str
        response = f"General answer for: {query}"
        return msg.model_copy(update={"payload": response})

    answer_node = Node(answer, name="answer")
    flow = create(answer_node.to())

    registry = ModelRegistry()
    registry.register("answer", Message, Message)

    return flow, registry
```

### Step 2: Create Wrapper Nodes That Call Playbooks

**Critical pattern:** Wrapper nodes must:
1. Extract payload from incoming `Message`
2. Prepare a new `Message` for the playbook
3. Call `ctx.call_playbook(factory, message)`
4. Re-wrap the returned **payload** in a `Message`

```python
# ============================================================================
# Wrapper Nodes
# ============================================================================

async def documents_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that calls documents playbook."""
    decision = msg.payload  # RouteDecision

    # Prepare playbook input
    playbook_msg = msg.model_copy(update={
        "payload": DocumentState(sources=[], summary=None)
    })

    # Call playbook - returns DocumentState (payload only!)
    result_state = await ctx.call_playbook(build_documents_playbook, playbook_msg)

    # Convert to FinalAnswer
    final = FinalAnswer(
        text=result_state.summary or "No summary",
        route="documents",
        artifacts={"sources": result_state.sources},
    )

    # Re-wrap in Message
    return msg.model_copy(update={"payload": final})


async def bug_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that calls bug playbook."""
    decision = msg.payload  # RouteDecision

    playbook_msg = msg.model_copy(update={
        "payload": BugState(logs=[], recommendation=None)
    })

    # Call playbook
    result_state = await ctx.call_playbook(build_bug_playbook, playbook_msg)

    final = FinalAnswer(
        text=result_state.recommendation or "No recommendation",
        route="bug",
        artifacts={"logs": result_state.logs},
    )

    return msg.model_copy(update={"payload": final})


async def general_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that calls general playbook."""
    decision = msg.payload  # RouteDecision

    playbook_msg = msg.model_copy(update={
        "payload": decision.query.text
    })

    # Call playbook
    response = await ctx.call_playbook(build_general_playbook, playbook_msg)

    final = FinalAnswer(
        text=response,
        route="general",
    )

    return msg.model_copy(update={"payload": final})
```

### Step 3: Create Router and Wire Everything Together

```python
from penguiflow.patterns import predicate_router

# ============================================================================
# Router Definition
# ============================================================================

def route_predicate(msg: Message) -> str:
    """Extract route from RouteDecision payload."""
    decision = msg.payload  # RouteDecision
    return decision.route  # Returns "documents", "bug", or "general"

router = predicate_router("route_dispatcher", route_predicate)

# ============================================================================
# Create Nodes
# ============================================================================

triage_node = Node(triage_query, name="triage")
documents_node = Node(documents_wrapper, name="documents")
bug_node = Node(bug_wrapper, name="bug")
general_node = Node(general_wrapper, name="general")

# ============================================================================
# Wire the Flow
# ============================================================================

flow = create(
    triage_node.to(router),
    router.to(documents_node, bug_node, general_node),  # Router picks one
    documents_node.to(),  # Terminal
    bug_node.to(),        # Terminal
    general_node.to(),    # Terminal
)

# ============================================================================
# Registry
# ============================================================================

registry = ModelRegistry()
registry.register("triage", UserQuery, RouteDecision)
registry.register("route_dispatcher", Message, Message)  # Router (no validation)
registry.register("documents", Message, Message)
registry.register("bug", Message, Message)
registry.register("general", Message, Message)
```

### Step 4: Run It

```python
async def main():
    from uuid import uuid4

    # Start the flow
    flow.run(registry=registry)

    # Create input message
    trace_id = uuid4().hex
    headers = Headers(tenant="acme", topic="query")

    message = Message(
        payload=UserQuery(text="Analyze the deployment logs"),
        headers=headers,
        trace_id=trace_id,
    )

    # Emit and fetch result
    await flow.emit(message)
    result_msg = await flow.fetch()

    final_answer = result_msg.payload  # FinalAnswer
    print(f"Route: {final_answer.route}")
    print(f"Answer: {final_answer.text}")

    await flow.stop()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## Complete Working Example

See `examples/routing_with_playbooks/flow.py` for a complete, runnable example.

---

## Comparison: Router vs ReactPlanner

| Aspect | Router + Playbooks | ReactPlanner |
|--------|-------------------|--------------|
| **Routing Logic** | Deterministic (predicate/types) | LLM decides dynamically |
| **Cost** | Low (no LLM for routing) | Higher (LLM on every decision) |
| **Flexibility** | Fixed flow topology | Can adapt mid-execution |
| **Use Case** | Deterministic workflows | Adaptive, exploratory tasks |
| **Observability** | Explicit flow graph | Tool calls logged |
| **Control** | Full programmatic control | LLM has autonomy |

### When to Use Which?

**Use Router + Playbooks when:**
- Routing logic is **deterministic** (e.g., "if route=documents, go here")
- You want **explicit control** over the flow topology
- You need **low-cost, high-throughput** processing
- Workflows are **linear or branching** (not exploratory)

**Use ReactPlanner when:**
- The LLM should **decide dynamically** which tools to use
- Workflows need to **adapt** based on intermediate results
- You want the agent to **explore** multiple paths
- **Tool selection** is context-dependent and complex

### Hybrid Approach

You can **combine both**:

```python
# Use router for high-level routing
router = predicate_router("main_router", route_by_domain)

# Use ReactPlanner within specific branches
async def complex_analysis_wrapper(msg: Message, ctx) -> Message:
    """This branch uses ReactPlanner for dynamic exploration."""
    planner = ReactPlanner(llm="gpt-4", catalog=analysis_catalog)
    result = await planner.run(query=msg.payload["query"])
    return msg.model_copy(update={"payload": result.payload})

# Mix router with planner nodes
flow = create(
    router.to(simple_playbook_node, complex_planner_node),
    # ...
)
```

---

## Key Takeaways

1. **`call_playbook` returns payload**, not Message - always re-wrap!
2. **Playbook factories** return `(flow, registry)` - fresh instances each time
3. **Wrapper nodes** bridge the main flow and playbooks
4. **Routers** provide deterministic, declarative routing
5. **Choose your pattern** based on control vs. flexibility tradeoffs

---

## References

- `penguiflow/patterns.py` - Router implementations
- `examples/playbook_retrieval/` - Basic playbook example
- `examples/planner_enterprise_agent_v2/nodes.py` - ReactPlanner with subflows
- `penguiflow/core.py:1479` - `call_playbook` implementation
