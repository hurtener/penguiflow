"""Planner-compatible nodes with comprehensive type safety and observability.

All nodes are decorated with @tool to make them discoverable by the ReactPlanner.
Each node includes structured metadata for LLM decision-making.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from penguiflow import Message, Node, NodePolicy, StreamChunk
from penguiflow.catalog import tool

# ============================================================================
# Pydantic Models (Shared Contracts)
# ============================================================================


class UserQuery(BaseModel):
    """User's input question or task."""

    text: str
    tenant_id: str = "default"


class RouteDecision(BaseModel):
    """Router's classification of query intent."""

    query: UserQuery
    route: Literal["documents", "bug", "general"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class RoadmapStep(BaseModel):
    """UI progress indicator for multi-step workflows."""

    id: int
    name: str
    description: str
    status: Literal["pending", "running", "ok", "error"] = "pending"


class DocumentState(BaseModel):
    """Accumulated state for document analysis workflow."""

    query: UserQuery
    route: Literal["documents"] = "documents"
    roadmap: list[RoadmapStep]
    sources: list[str] = Field(default_factory=list)
    metadata: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None


class BugState(BaseModel):
    """Accumulated state for bug triage workflow."""

    query: UserQuery
    route: Literal["bug"] = "bug"
    roadmap: list[RoadmapStep]
    logs: list[str] = Field(default_factory=list)
    diagnostics: dict[str, str] = Field(default_factory=dict)
    recommendation: str | None = None


class GeneralResponse(BaseModel):
    """Simple response for general queries."""

    query: UserQuery
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)


class FinalAnswer(BaseModel):
    """Unified final response across all routes."""

    text: str
    route: str
    artifacts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StatusUpdate(BaseModel):
    """Structured status update for frontend websocket."""

    status: Literal["thinking", "ok", "error"]
    message: str | None = None
    roadmap_step_id: int | None = None
    roadmap_step_status: Literal["running", "ok", "error"] | None = None
    roadmap: list[RoadmapStep] | None = None


# Roadmap templates
DOCUMENT_ROADMAP = [
    RoadmapStep(id=1, name="Parse files", description="Enumerate candidate documents"),
    RoadmapStep(id=2, name="Extract metadata", description="Analyze files in parallel"),
    RoadmapStep(id=3, name="Generate summary", description="Produce analysis summary"),
    RoadmapStep(id=4, name="Render report", description="Assemble structured output"),
]

BUG_ROADMAP = [
    RoadmapStep(id=10, name="Collect logs", description="Gather error context"),
    RoadmapStep(id=11, name="Run diagnostics", description="Execute validation checks"),
    RoadmapStep(id=12, name="Recommend fix", description="Propose remediation steps"),
]


# ============================================================================
# Planner-Discoverable Nodes
# ============================================================================


@tool(
    desc="Classify user intent and route to appropriate workflow",
    tags=["planner", "routing"],
    side_effects="read",
)
async def triage_query(args: UserQuery, ctx: Any) -> RouteDecision:
    """Intelligent routing based on query content analysis."""
    text_lower = args.text.lower()

    # Pattern-based routing (in production: use LLM classifier)
    if any(kw in text_lower for kw in ["bug", "error", "crash", "traceback"]):
        route: Literal["documents", "bug", "general"] = "bug"
        confidence = 0.95
        reason = "Detected incident keywords (bug, error, crash)"
    elif any(kw in text_lower for kw in ["document", "file", "report", "analyze"]):
        route = "documents"
        confidence = 0.90
        reason = "Detected document analysis keywords"
    else:
        route = "general"
        confidence = 0.75
        reason = "General query - no specific workflow match"

    return RouteDecision(
        query=args, route=route, confidence=confidence, reason=reason
    )


@tool(
    desc="Initialize document analysis workflow with roadmap",
    tags=["planner", "documents"],
    side_effects="stateful",
)
async def initialize_document_workflow(
    args: RouteDecision, ctx: Any
) -> DocumentState:
    """Set up document analysis pipeline."""
    if args.route != "documents":
        raise ValueError(f"Expected documents route, got {args.route}")

    return DocumentState(query=args.query, roadmap=list(DOCUMENT_ROADMAP))


@tool(
    desc="Parse and enumerate document sources from query context",
    tags=["planner", "documents"],
    side_effects="read",
)
async def parse_documents(args: DocumentState, ctx: Any) -> DocumentState:
    """Extract document references from query."""
    # In production: use LLM to extract file paths, URLs, or search queries
    # For example: parse file paths from query, enumerate directory, etc.

    # Simulate parsing
    await asyncio.sleep(0.05)

    sources = [
        "README.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        "docs/deployment.md",
    ]

    roadmap = list(args.roadmap)
    roadmap[0] = roadmap[0].model_copy(update={"status": "ok"})

    return args.model_copy(update={"sources": sources, "roadmap": roadmap})


@tool(
    desc="Extract structured metadata from documents in parallel",
    tags=["planner", "documents"],
    side_effects="read",
    latency_hint_ms=1000  # High latency,
)
async def extract_metadata(args: DocumentState, ctx: Any) -> DocumentState:
    """Concurrent metadata extraction from document sources."""

    async def analyze_file(source: str) -> dict[str, Any]:
        """Analyze single document (simulated)."""
        await asyncio.sleep(0.02)
        return {
            "source": source,
            "size_kb": len(source) * 100,
            "last_modified": "2025-10-22",
            "checksum": hash(source) % 10000,
        }

    # Simulate parallel processing
    metadata = []
    for source in args.sources:
        meta = await analyze_file(source)
        metadata.append(meta)

    roadmap = list(args.roadmap)
    roadmap[1] = roadmap[1].model_copy(update={"status": "ok"})

    return args.model_copy(update={"metadata": metadata, "roadmap": roadmap})


@tool(
    desc="Generate summary from extracted document metadata",
    tags=["planner", "documents"],
    side_effects="pure",
)
async def generate_document_summary(args: DocumentState, ctx: Any) -> DocumentState:
    """Synthesize findings into natural language summary."""
    # In production: use LLM to generate summary from metadata

    summary = (
        f"Analyzed {len(args.sources)} documents. "
        f"Total size: {sum(m.get('size_kb', 0) for m in args.metadata)}KB. "
        f"Key files: {', '.join(args.sources[:3])}."
    )

    roadmap = list(args.roadmap)
    roadmap[2] = roadmap[2].model_copy(update={"status": "ok"})

    return args.model_copy(update={"summary": summary, "roadmap": roadmap})


@tool(
    desc="Render final document analysis report with artifacts",
    tags=["planner", "documents"],
    side_effects="pure",
)
async def render_document_report(args: DocumentState, ctx: Any) -> FinalAnswer:
    """Package results into structured final answer."""
    roadmap = list(args.roadmap)
    roadmap[3] = roadmap[3].model_copy(update={"status": "ok"})

    return FinalAnswer(
        text=args.summary or "No summary available",
        route="documents",
        artifacts={
            "sources": args.sources,
            "metadata": args.metadata,
        },
        metadata={
            "source_count": len(args.sources),
            "roadmap_complete": all(s.status == "ok" for s in roadmap),
        },
    )


@tool(
    desc="Initialize bug triage workflow with diagnostic roadmap",
    tags=["planner", "bugs"],
    side_effects="stateful",
)
async def initialize_bug_workflow(args: RouteDecision, ctx: Any) -> BugState:
    """Set up bug triage pipeline."""
    if args.route != "bug":
        raise ValueError(f"Expected bug route, got {args.route}")

    return BugState(query=args.query, roadmap=list(BUG_ROADMAP))


@tool(
    desc="Collect error logs and stack traces from system",
    tags=["planner", "bugs"],
    side_effects="read",
)
async def collect_error_logs(args: BugState, ctx: Any) -> BugState:
    """Gather diagnostic logs from error context."""
    # In production: query logging infrastructure, parse stack traces

    logs = [
        "ERROR: ValueError: Invalid configuration",
        "Traceback (most recent call last):",
        '  File "app.py", line 42, in process',
        "    validate_config(settings)",
        "ValueError: Missing required field: api_key",
    ]

    roadmap = list(args.roadmap)
    roadmap[0] = roadmap[0].model_copy(update={"status": "ok"})

    return args.model_copy(update={"logs": logs, "roadmap": roadmap})


@tool(
    desc="Run automated diagnostics and health checks",
    tags=["planner", "bugs"],
    side_effects="external",
    latency_hint_ms=1000  # High latency,
)
async def run_diagnostics(args: BugState, ctx: Any) -> BugState:
    """Execute validation suite to isolate failure."""
    # In production: run actual health checks, integration tests, etc.

    await asyncio.sleep(0.1)  # Simulate diagnostic execution

    diagnostics = {
        "api_health": "degraded",
        "database": "ok",
        "cache": "ok",
        "config_validation": "failed",
    }

    roadmap = list(args.roadmap)
    roadmap[1] = roadmap[1].model_copy(update={"status": "ok"})

    return args.model_copy(update={"diagnostics": diagnostics, "roadmap": roadmap})


@tool(
    desc="Analyze diagnostics and recommend remediation steps",
    tags=["planner", "bugs"],
    side_effects="pure",
)
async def recommend_bug_fix(args: BugState, ctx: Any) -> FinalAnswer:
    """Generate actionable fix recommendation."""
    # In production: use LLM to analyze logs + diagnostics

    failed_checks = [k for k, v in args.diagnostics.items() if v in ("failed", "degraded")]

    recommendation = (
        f"Root cause: Configuration validation failure. "
        f"Failed checks: {', '.join(failed_checks)}. "
        f"Action: Review environment variables and ensure api_key is set."
    )

    roadmap = list(args.roadmap)
    roadmap[2] = roadmap[2].model_copy(update={"status": "ok"})

    return FinalAnswer(
        text=recommendation,
        route="bug",
        artifacts={
            "logs": args.logs,
            "diagnostics": args.diagnostics,
        },
        metadata={
            "failed_checks": failed_checks,
            "roadmap_complete": all(s.status == "ok" for s in roadmap),
        },
    )


@tool(
    desc="Handle simple general queries with direct LLM response",
    tags=["planner", "general"],
    side_effects="read",
    latency_hint_ms=500  # Medium latency,
)
async def answer_general_query(args: RouteDecision, ctx: Any) -> FinalAnswer:
    """Direct LLM answer for queries not requiring specialized workflows."""
    # In production: call LLM with query

    await asyncio.sleep(0.05)

    answer = (
        f"I understand your query: '{args.query.text}'. "
        f"This appears to be a general question. In production, this would "
        f"invoke an LLM to generate a contextual response."
    )

    return FinalAnswer(
        text=answer,
        route="general",
        metadata={"confidence": args.confidence},
    )


# ============================================================================
# Sink Nodes (for status updates and streaming)
# ============================================================================


async def status_sink(message: Message, ctx: Any) -> None:
    """Collect status updates for frontend websocket delivery."""
    # In production: send to websocket, SSE endpoint, or message queue
    payload = message.payload
    if isinstance(payload, StatusUpdate):
        # Stub: log for visibility
        ctx_logger = getattr(ctx, "logger", None)
        if ctx_logger:
            ctx_logger.debug(
                "status_update",
                extra={
                    "trace_id": message.trace_id,
                    "status": payload.status,
                    "message": payload.message,
                },
            )


async def chunk_sink(message: Message, ctx: Any) -> None:
    """Collect streaming chunks for real-time frontend updates."""
    # In production: send to websocket for progressive rendering
    payload = message.payload
    if isinstance(payload, StreamChunk):
        ctx_logger = getattr(ctx, "logger", None)
        if ctx_logger:
            ctx_logger.debug(
                "stream_chunk",
                extra={
                    "trace_id": message.trace_id,
                    "text": payload.text[:50],  # Truncate for logging
                    "done": payload.done,
                },
            )


# Create non-planner nodes for sinks (they don't need LLM discovery)
status_sink_node = Node(status_sink, name="status_sink", policy=NodePolicy(validate="none"))
chunk_sink_node = Node(chunk_sink, name="chunk_sink", policy=NodePolicy(validate="none"))
