"""Complete example: Router + Playbooks pattern in PenguiFlow.

This demonstrates the idiomatic way to route messages to different playbooks
using PenguiFlow's built-in routers and call_playbook.

Architecture:
    UserQuery → Triage → Router ──┬→ Documents Playbook → FinalAnswer
                                   ├→ Bug Playbook → FinalAnswer
                                   └→ General Playbook → FinalAnswer

Run:
    uv run python examples/routing_with_playbooks/flow.py
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from penguiflow import Headers, Message, ModelRegistry, Node, create
from penguiflow.patterns import predicate_router

# ============================================================================
# Models
# ============================================================================


class UserQuery(BaseModel):
    """User's input question."""

    text: str
    tenant_id: str = "default"


class RouteDecision(BaseModel):
    """Router's classification result."""

    query: UserQuery
    route: Literal["documents", "bug", "general"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class DocumentState(BaseModel):
    """State for document analysis workflow."""

    sources: list[str] = Field(default_factory=list)
    summary: str | None = None


class BugState(BaseModel):
    """State for bug triage workflow."""

    logs: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class FinalAnswer(BaseModel):
    """Final response."""

    text: str
    route: str
    artifacts: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Step 1: Define Playbooks
# ============================================================================


def build_documents_playbook() -> tuple[Any, ModelRegistry]:
    """Document analysis pipeline: parse → summarize."""

    async def parse_documents(msg: Message, ctx) -> Message:
        state = msg.payload  # DocumentState
        await asyncio.sleep(0.01)  # Simulate work
        state.sources = ["README.md", "CHANGELOG.md", "docs/architecture.md"]
        return msg.model_copy(update={"payload": state})

    async def generate_summary(msg: Message, ctx) -> Message:
        state = msg.payload  # DocumentState
        await asyncio.sleep(0.01)  # Simulate work
        state.summary = f"Analyzed {len(state.sources)} documents. Key files: {', '.join(state.sources[:2])}."
        return msg.model_copy(update={"payload": state})

    parse_node = Node(parse_documents, name="parse")
    summary_node = Node(generate_summary, name="summarize")

    flow = create(
        parse_node.to(summary_node),
        summary_node.to(),
    )

    registry = ModelRegistry()
    registry.register("parse", Message, Message)
    registry.register("summarize", Message, Message)

    return flow, registry


def build_bug_playbook() -> tuple[Any, ModelRegistry]:
    """Bug diagnosis pipeline: collect logs → recommend fix."""

    async def collect_logs(msg: Message, ctx) -> Message:
        state = msg.payload  # BugState
        await asyncio.sleep(0.01)
        state.logs = [
            "ERROR: ValueError: Invalid configuration",
            "Traceback (most recent call last):",
            '  File "app.py", line 42',
        ]
        return msg.model_copy(update={"payload": state})

    async def recommend_fix(msg: Message, ctx) -> Message:
        state = msg.payload  # BugState
        await asyncio.sleep(0.01)
        state.recommendation = (
            f"Found {len(state.logs)} log entries. Root cause: Configuration error. Fix: Check environment variables."
        )
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


def build_general_playbook() -> tuple[Any, ModelRegistry]:
    """Simple general query handler."""

    async def answer_general(msg: Message, ctx) -> Message:
        query = msg.payload  # str (query text)
        await asyncio.sleep(0.01)
        response = f"General answer for: '{query}'. In production, this would invoke an LLM."
        return msg.model_copy(update={"payload": response})

    answer_node = Node(answer_general, name="answer")
    flow = create(answer_node.to())

    registry = ModelRegistry()
    registry.register("answer", Message, Message)

    return flow, registry


# ============================================================================
# Step 2: Create Wrapper Nodes That Call Playbooks
# ============================================================================


async def documents_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that invokes documents playbook."""
    decision = msg.payload  # RouteDecision

    print(f"  → Calling documents playbook for: {decision.query.text}")

    # Prepare input for playbook
    playbook_msg = msg.model_copy(update={"payload": DocumentState(sources=[], summary=None)})

    # Call playbook - returns DocumentState payload
    result_state = await ctx.call_playbook(build_documents_playbook, playbook_msg)

    # Convert to FinalAnswer
    final = FinalAnswer(
        text=result_state.summary or "No summary available",
        route="documents",
        artifacts={"sources": result_state.sources},
    )

    # Re-wrap in Message
    return msg.model_copy(update={"payload": final})


async def bug_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that invokes bug playbook."""
    decision = msg.payload  # RouteDecision

    print(f"  → Calling bug playbook for: {decision.query.text}")

    playbook_msg = msg.model_copy(update={"payload": BugState(logs=[], recommendation=None)})

    # Call playbook
    result_state = await ctx.call_playbook(build_bug_playbook, playbook_msg)

    final = FinalAnswer(
        text=result_state.recommendation or "No recommendation",
        route="bug",
        artifacts={"logs": result_state.logs},
    )

    return msg.model_copy(update={"payload": final})


async def general_wrapper(msg: Message, ctx) -> Message:
    """Wrapper that invokes general playbook."""
    decision = msg.payload  # RouteDecision

    print(f"  → Calling general playbook for: {decision.query.text}")

    playbook_msg = msg.model_copy(update={"payload": decision.query.text})

    # Call playbook
    response = await ctx.call_playbook(build_general_playbook, playbook_msg)

    final = FinalAnswer(
        text=response,
        route="general",
    )

    return msg.model_copy(update={"payload": final})


# ============================================================================
# Step 3: Create Triage and Router
# ============================================================================


async def triage_query(msg: Message, ctx) -> Message:
    """Classify query intent."""
    query = msg.payload  # UserQuery
    text_lower = query.text.lower()

    print(f"  → Triaging query: {query.text}")

    # Pattern-based routing (in production: use LLM)
    if any(kw in text_lower for kw in ["bug", "error", "crash", "traceback"]):
        route: Literal["documents", "bug", "general"] = "bug"
        confidence = 0.95
        reason = "Detected error keywords"
    elif any(kw in text_lower for kw in ["document", "file", "report", "analyze"]):
        route = "documents"
        confidence = 0.90
        reason = "Detected document analysis keywords"
    else:
        route = "general"
        confidence = 0.75
        reason = "General query"

    decision = RouteDecision(
        query=query,
        route=route,
        confidence=confidence,
        reason=reason,
    )

    print(f"  → Routed to: {route} (confidence: {confidence})")

    return msg.model_copy(update={"payload": decision})


def route_predicate(msg: Message) -> str:
    """Extract route from RouteDecision."""
    decision = msg.payload  # RouteDecision
    return decision.route  # "documents", "bug", or "general"


# ============================================================================
# Step 4: Build the Main Flow
# ============================================================================


def build_main_flow() -> tuple[Any, ModelRegistry]:
    """Build the complete routing flow."""

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

    # Create registry
    registry = ModelRegistry()
    registry.register("triage", Message, Message)
    registry.register("route_dispatcher", Message, Message)  # Router (no validation)
    registry.register("documents", Message, Message)
    registry.register("bug", Message, Message)
    registry.register("general", Message, Message)

    return flow, registry


# ============================================================================
# Main
# ============================================================================


async def main() -> None:
    """Run example queries through the routing flow."""

    print("=" * 80)
    print("Router + Playbooks Example")
    print("=" * 80)

    # Build and start flow
    flow, registry = build_main_flow()
    flow.run(registry=registry)

    # Test queries
    queries = [
        "Analyze the deployment logs and summarize findings",
        "We're seeing a ValueError in production, help diagnose",
        "What's the weather like today?",
    ]

    for i, query_text in enumerate(queries, 1):
        print(f"\n{'─' * 80}")
        print(f"Query {i}: {query_text}")
        print(f"{'─' * 80}")

        # Create message
        trace_id = uuid4().hex
        headers = Headers(tenant="acme", topic="query")

        message = Message(
            payload=UserQuery(text=query_text),
            headers=headers,
            trace_id=trace_id,
        )

        # Emit and fetch result
        await flow.emit(message)
        result_msg = await flow.fetch()

        # Display result
        final_answer = result_msg.payload  # FinalAnswer
        print(f"\n✓ Route: {final_answer.route}")
        print(f"✓ Answer: {final_answer.text}")

        if final_answer.artifacts:
            print(f"✓ Artifacts: {list(final_answer.artifacts.keys())}")

    # Cleanup
    await flow.stop()

    print(f"\n{'=' * 80}")
    print("All queries processed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
