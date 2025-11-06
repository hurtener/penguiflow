# Router + Playbooks Example

Demonstrates the **idiomatic PenguiFlow pattern** for routing messages to different playbooks using built-in routers and `call_playbook`.

## Architecture

```
UserQuery
    ↓
[Triage] ──→ RouteDecision
    ↓
[Router] ─────┬──→ [Documents Wrapper] ──→ call_playbook(documents_flow) ──→ FinalAnswer
              ├──→ [Bug Wrapper]       ──→ call_playbook(bug_flow)       ──→ FinalAnswer
              └──→ [General Wrapper]   ──→ call_playbook(general_flow)   ──→ FinalAnswer
```

## Key Patterns

### 1. Playbook Factory

Each workflow is a **factory function** returning `(flow, registry)`:

```python
def build_documents_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Document analysis pipeline: parse → summarize."""

    async def parse_documents(msg: Message, ctx) -> Message:
        # Process documents
        ...

    async def generate_summary(msg: Message, ctx) -> Message:
        # Generate summary
        ...

    flow = create(
        parse_node.to(summary_node),
        summary_node.to(),
    )

    registry = ModelRegistry()
    registry.register("parse", Message, Message)
    registry.register("summarize", Message, Message)

    return flow, registry  # Fresh instance each time!
```

### 2. Wrapper Nodes with `call_playbook`

Wrapper nodes bridge the main flow and playbooks:

```python
async def documents_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that invokes documents playbook."""
    decision = msg.payload  # RouteDecision

    # Prepare playbook input
    playbook_msg = msg.model_copy(
        update={"payload": DocumentState(sources=[])}
    )

    # Call playbook - returns PAYLOAD (not Message!)
    result_state = await ctx.call_playbook(
        build_documents_playbook,
        playbook_msg
    )

    # Convert to final format
    final = FinalAnswer(
        text=result_state.summary,
        route="documents",
        artifacts={"sources": result_state.sources},
    )

    # Re-wrap in Message
    return msg.model_copy(update={"payload": final})
```

**Critical:** `call_playbook` returns the **payload**, not a `Message`!

### 3. Router with Predicate

Use `predicate_router` for deterministic routing:

```python
from penguiflow.patterns import predicate_router

def route_predicate(msg: Message) -> str:
    """Extract route from RouteDecision."""
    decision = msg.payload  # RouteDecision
    return decision.route  # "documents", "bug", or "general"

router = predicate_router("route_dispatcher", route_predicate)
```

The router inspects the message and returns the **name** of the target node.

## Run

```bash
uv run python examples/routing_with_playbooks/flow.py
```

## Expected Output

```
================================================================================
Router + Playbooks Example
================================================================================

────────────────────────────────────────────────────────────────────────────────
Query 1: Analyze the deployment logs and summarize findings
────────────────────────────────────────────────────────────────────────────────
  → Triaging query: Analyze the deployment logs and summarize findings
  → Routed to: documents (confidence: 0.9)
  → Calling documents playbook for: Analyze the deployment logs and summarize findings

✓ Route: documents
✓ Answer: Analyzed 3 documents. Key files: README.md, CHANGELOG.md.
✓ Artifacts: ['sources']

────────────────────────────────────────────────────────────────────────────────
Query 2: We're seeing a ValueError in production, help diagnose
────────────────────────────────────────────────────────────────────────────────
  → Triaging query: We're seeing a ValueError in production, help diagnose
  → Routed to: bug (confidence: 0.95)
  → Calling bug playbook for: We're seeing a ValueError in production, help diagnose

✓ Route: bug
✓ Answer: Found 3 log entries. Root cause: Configuration error. Fix: Check environment variables.
✓ Artifacts: ['logs']

────────────────────────────────────────────────────────────────────────────────
Query 3: What's the weather like today?
────────────────────────────────────────────────────────────────────────────────
  → Triaging query: What's the weather like today?
  → Routed to: general (confidence: 0.75)
  → Calling general playbook for: What's the weather like today?

✓ Route: general
✓ Answer: General answer for: 'What's the weather like today?'. In production, this would invoke an LLM.
✓ Artifacts: []

================================================================================
All queries processed successfully!
================================================================================
```

## When to Use This Pattern

**Use Router + Playbooks when:**
- Routing logic is **deterministic** (rule-based or simple classification)
- You want **explicit control** over flow topology
- You need **low-cost, high-throughput** processing (no LLM for routing)
- Workflows are **linear or branching** (not exploratory)

**Use ReactPlanner when:**
- The LLM should **decide dynamically** which tools to use
- Workflows need to **adapt** based on intermediate results
- Tool selection is **context-dependent and complex**

## See Also

- `examples/planner_enterprise_agent_v2/ROUTER_PLAYBOOK_GUIDE.md` - Comprehensive guide
- `examples/playbook_retrieval/` - Basic playbook example
- `penguiflow/patterns.py` - Router implementations
