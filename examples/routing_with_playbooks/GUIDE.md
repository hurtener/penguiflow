# Router + Playbook Pattern - Complete Guide

The **idiomatic PenguiFlow way** to route messages to different playbooks using built-in routers.

---

## ⚡ Quick Reference

```python
# 1. Define playbook factory
def build_my_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    # Create nodes, wire flow, return (flow, registry)
    return flow, registry

# 2. Create wrapper node that calls playbook
async def my_wrapper(msg: Message, ctx) -> Message:
    playbook_msg = msg.model_copy(update={"payload": prepared_input})
    result = await ctx.call_playbook(build_my_playbook, playbook_msg)  # ← Returns PAYLOAD!
    return msg.model_copy(update={"payload": result})

# 3. Create router
router = predicate_router("router", lambda msg: msg.payload.route)

# 4. Wire everything
flow = create(
    router.to(wrapper_node1, wrapper_node2),
    wrapper_node1.to(),
    wrapper_node2.to(),
)
```

**🔥 Critical:** `call_playbook` returns the **payload**, NOT the `Message`!

---

## 📚 Core Concepts

### What is `call_playbook`?

```python
async def call_playbook(
    playbook: Callable[[], tuple[PenguiFlow, ModelRegistry]],
    parent_msg: Message,
    timeout: float | None = None,
) -> Any:
    """Execute a subflow and return its final payload."""
```

**Behavior:**
- Takes a **playbook factory** (function returning `(flow, registry)`)
- Executes the entire subflow
- Returns the **payload** of the final message (unwrapped!)
- Automatically propagates `trace_id`, headers, and cancellation

### What is a Playbook Factory?

A function that returns a fresh flow instance:

```python
def build_my_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Factory builds fresh flow each call."""

    node1 = Node(step1, name="step1")
    node2 = Node(step2, name="step2")

    flow = create(
        node1.to(node2),
        node2.to(),
    )

    registry = ModelRegistry()
    registry.register("step1", InputModel, OutputModel)
    registry.register("step2", OutputModel, FinalModel)

    return flow, registry
```

**Why a factory?** Each `call_playbook` needs independent state.

---

## 🏗️ Architecture Pattern

```
UserQuery
    ↓
[Triage Node] ──→ RouteDecision
    ↓
[Router Node] ─────┬──→ [Wrapper A] ──→ call_playbook(playbook_A) ──→ FinalAnswer
                   ├──→ [Wrapper B] ──→ call_playbook(playbook_B) ──→ FinalAnswer
                   └──→ [Wrapper C] ──→ call_playbook(playbook_C) ──→ FinalAnswer
```

**Flow:**
1. **Triage** classifies the query → outputs `RouteDecision`
2. **Router** inspects `RouteDecision.route` → picks target wrapper
3. **Wrapper** calls the appropriate playbook → converts result to `FinalAnswer`

---

## 📝 Step-by-Step Implementation

### Step 1: Define Playbooks

```python
def build_documents_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Multi-step document analysis."""

    async def parse(msg: Message, ctx) -> Message:
        state = msg.payload  # DocumentState
        state.sources = ["README.md", "CHANGELOG.md"]
        return msg.model_copy(update={"payload": state})

    async def summarize(msg: Message, ctx) -> Message:
        state = msg.payload
        state.summary = f"Analyzed {len(state.sources)} docs"
        return msg.model_copy(update={"payload": state})

    parse_node = Node(parse, name="parse")
    summary_node = Node(summarize, name="summarize")

    flow = create(
        parse_node.to(summary_node),
        summary_node.to(),
    )

    registry = ModelRegistry()
    registry.register("parse", Message, Message)
    registry.register("summarize", Message, Message)

    return flow, registry
```

### Step 2: Create Wrapper Nodes

**Critical pattern:** Wrapper receives `Message`, calls playbook, re-wraps result.

```python
async def documents_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that calls documents playbook."""
    decision = msg.payload  # RouteDecision

    # Prepare playbook input
    playbook_msg = msg.model_copy(update={
        "payload": DocumentState(sources=[], summary=None)
    })

    # Call playbook - returns DocumentState (PAYLOAD only!)
    result_state = await ctx.call_playbook(
        build_documents_playbook,
        playbook_msg
    )

    # Convert to final format
    final = FinalAnswer(
        text=result_state.summary or "No summary",
        route="documents",
        artifacts={"sources": result_state.sources},
    )

    # Re-wrap in Message
    return msg.model_copy(update={"payload": final})
```

### Step 3: Create Router

```python
from penguiflow.patterns import predicate_router

def route_predicate(msg: Message) -> str:
    """Extract route from RouteDecision."""
    decision = msg.payload  # RouteDecision
    return decision.route  # Returns "documents", "bug", or "general"

router = predicate_router("route_dispatcher", route_predicate)
```

The router returns the **node name** to route to.

### Step 4: Wire Everything Together

```python
# Create nodes
triage_node = Node(triage_query, name="triage")
router = predicate_router("route_dispatcher", route_predicate)
documents_node = Node(documents_wrapper, name="documents")
bug_node = Node(bug_wrapper, name="bug")
general_node = Node(general_wrapper, name="general")

# Wire the flow
flow = create(
    triage_node.to(router),
    router.to(documents_node, bug_node, general_node),  # Router picks one
    documents_node.to(),  # Terminals
    bug_node.to(),
    general_node.to(),
)

# Registry
registry = ModelRegistry()
registry.register("triage", Message, Message)
registry.register("route_dispatcher", Message, Message)
registry.register("documents", Message, Message)
registry.register("bug", Message, Message)
registry.register("general", Message, Message)
```

### Step 5: Run It

```python
# Start flow
flow.run(registry=registry)

# Create message
message = Message(
    payload=UserQuery(text="Analyze the logs"),
    headers=Headers(tenant="acme"),
    trace_id=uuid4().hex,
)

# Emit and fetch
await flow.emit(message)
result = await flow.fetch()

print(result.payload)  # FinalAnswer

await flow.stop()
```

---

## 🔄 Alternative: Union Router

For **type-based routing** instead of string fields:

```python
from penguiflow.patterns import union_router

# Define route-specific types
class DocumentRoute(BaseModel):
    kind: Literal["documents"] = "documents"
    query: UserQuery

class BugRoute(BaseModel):
    kind: Literal["bug"] = "bug"
    query: UserQuery

# Union type
RouteUnion = DocumentRoute | BugRoute

# Router automatically routes by `kind` field
router = union_router("route_dispatcher", RouteUnion)

# Wire as before
flow = create(
    triage_node.to(router),
    router.to(documents_node, bug_node),
    # ...
)
```

---

## 🆚 Comparison: Router vs ReactPlanner

| Aspect | Router + Playbooks | ReactPlanner |
|--------|-------------------|--------------|
| **Routing Logic** | Deterministic (predicate/types) | LLM decides dynamically |
| **Cost** | Low (no LLM for routing) | Higher (LLM on every decision) |
| **Flexibility** | Fixed flow topology | Can adapt mid-execution |
| **Use Case** | Deterministic workflows | Adaptive, exploratory tasks |
| **Observability** | Explicit flow graph | Tool calls logged |
| **Control** | Full programmatic control | LLM has autonomy |

### Decision Guide

**Use Router + Playbooks when:**
- ✅ Routing logic is **deterministic** (rule-based)
- ✅ You want **explicit control** over flow topology
- ✅ You need **low-cost, high-throughput** processing
- ✅ Workflows are **linear or branching** (not exploratory)

**Use ReactPlanner when:**
- ✅ The LLM should **decide dynamically** which tools to use
- ✅ Workflows need to **adapt** based on intermediate results
- ✅ Tool selection is **context-dependent and complex**
- ✅ You want the agent to **explore** multiple solution paths

### Hybrid Approach

Combine both for maximum power:

```python
# High-level router for domains
router = predicate_router("domain_router", route_by_domain)

# Simple playbook for deterministic workflows
async def simple_workflow_wrapper(msg: Message, ctx) -> Message:
    result = await ctx.call_playbook(build_simple_playbook, msg)
    return msg.model_copy(update={"payload": result})

# ReactPlanner for complex exploration
async def complex_analysis_wrapper(msg: Message, ctx) -> Message:
    """This branch uses ReactPlanner for dynamic exploration."""
    planner = ReactPlanner(llm="gpt-4", catalog=analysis_catalog)
    result = await planner.run(query=msg.payload["query"])
    return msg.model_copy(update={"payload": result.payload})

# Mix both patterns
flow = create(
    router.to(simple_workflow_node, complex_planner_node),
    # ...
)
```

---

## ⚠️ Common Pitfalls

### ❌ Forgetting to Re-wrap Payload

```python
# WRONG - call_playbook returns payload, not Message!
async def wrapper(msg: Message, ctx) -> Message:
    result = await ctx.call_playbook(build_playbook, msg)
    return result  # ❌ Returns payload, not Message!
```

```python
# CORRECT
async def wrapper(msg: Message, ctx) -> Message:
    result = await ctx.call_playbook(build_playbook, msg)
    return msg.model_copy(update={"payload": result})  # ✅
```

### ❌ Not Using a Factory

```python
# WRONG - reuses same flow instance!
my_flow, my_registry = build_playbook()  # ❌ Called once

async def wrapper(msg: Message, ctx) -> Message:
    result = await ctx.call_playbook(lambda: (my_flow, my_registry), msg)
    # ❌ State pollution across calls!
```

```python
# CORRECT - fresh instance each time
async def wrapper(msg: Message, ctx) -> Message:
    result = await ctx.call_playbook(build_playbook, msg)  # ✅ Factory
```

### ❌ Wrong Router Return Type

```python
# WRONG - router returns Node objects
def route_predicate(msg: Message) -> Node:
    if msg.payload.route == "documents":
        return documents_node  # ❌ Don't return Node directly
```

```python
# CORRECT - router returns node name (string)
def route_predicate(msg: Message) -> str:
    return msg.payload.route  # ✅ Returns "documents" (string)
```

---

## 🎯 Key Takeaways

1. **`call_playbook` returns payload**, not Message → always re-wrap!
2. **Playbook factories** return `(flow, registry)` → fresh instances each time
3. **Wrapper nodes** bridge the main flow and playbooks
4. **Routers** enable declarative, deterministic routing
5. **Choose your pattern** based on control vs. flexibility tradeoffs

---

## 📖 See Also

- `examples/routing_with_playbooks/flow.py` - Complete working example
- `penguiflow/patterns.py` - Router implementations (`predicate_router`, `union_router`)
- `examples/playbook_retrieval/` - Basic playbook pattern
- `penguiflow/core.py:1479` - `call_playbook` implementation
- `examples/planner_enterprise_agent_v2/` - ReactPlanner approach
