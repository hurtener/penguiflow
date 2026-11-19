"""Planner-compatible nodes with comprehensive type safety and observability.

All tools follow the same production patterns:
    * Typed Pydantic contracts for planner compatibility
    * Status updates emitted through the shared status publisher
    * ``WorkflowError`` for deterministic, structured error reporting
    * A pipeline tool that runs several steps as one planner action
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, MutableMapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from penguiflow.catalog import tool
from penguiflow.planner import ToolContext

logger = logging.getLogger("penguiflow.examples.planner_enterprise")


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


class DocumentRouteDecision(RouteDecision):
    """RouteDecision narrowed to document workflow."""

    route: Literal["documents"]


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


StatusPublisher = Callable[[StatusUpdate], None]


class WorkflowError(RuntimeError):
    """Structured error raised by the workflow tools."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        step: str,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.step = step
        self.trace_id = trace_id


# ============================================================================
# Helper utilities
# ============================================================================


def _resolve_meta(source: Any) -> MutableMapping[str, Any] | None:
    """Return the mutable metadata mapping from a planner context or a plain mapping."""

    if isinstance(source, MutableMapping):
        return source

    tool_ctx = getattr(source, "tool_context", None)
    if isinstance(tool_ctx, MutableMapping):
        return tool_ctx

    meta = getattr(source, "meta", None)
    if isinstance(meta, MutableMapping):
        return meta
    return None


def _trace_id_from_ctx(source: Any) -> str | None:
    meta = _resolve_meta(source)
    if not meta:
        return None
    trace_id = meta.get("trace_id")
    if isinstance(trace_id, str):
        return trace_id
    return None


def _status_logger(meta: MutableMapping[str, Any] | None) -> logging.Logger:
    if meta is None:
        return logger
    override = meta.get("status_logger")
    if isinstance(override, logging.Logger):
        return override
    return logger


def _publish_status(
    ctx_or_meta: Any,
    *,
    status: Literal["thinking", "ok", "error"],
    message: str | None = None,
    roadmap_step_id: int | None = None,
    roadmap_step_status: Literal["running", "ok", "error"] | None = None,
    roadmap: Sequence[RoadmapStep] | None = None,
) -> None:
    meta = _resolve_meta(ctx_or_meta)
    if meta is None:
        return

    publisher = meta.get("status_publisher")
    if not callable(publisher):
        return

    update = StatusUpdate(
        status=status,
        message=message,
        roadmap_step_id=roadmap_step_id,
        roadmap_step_status=roadmap_step_status,
        roadmap=list(roadmap) if roadmap is not None else None,
    )
    publisher(update)

    status_log = _status_logger(meta)
    status_log.debug(
        "status_update",
        extra={
            "trace_id": meta.get("trace_id"),
            "status": update.status,
            "message": update.message,
            "step_id": update.roadmap_step_id,
            "step_status": update.roadmap_step_status,
        },
    )


def _clone_roadmap(template: Sequence[RoadmapStep]) -> list[RoadmapStep]:
    return [step.model_copy() for step in template]


def _mark_step_status(
    roadmap: list[RoadmapStep],
    *,
    step_id: int,
    status: Literal["pending", "running", "ok", "error"],
) -> RoadmapStep | None:
    for idx, step in enumerate(roadmap):
        if step.id == step_id:
            updated = step.model_copy(update={"status": status})
            roadmap[idx] = updated
            return updated
    return None


# ============================================================================
# Planner-Discoverable Nodes
# ============================================================================


@tool(
    desc="Classify user intent and route to appropriate workflow",
    tags=["planner", "routing"],
    side_effects="read",
)
async def triage_query(args: UserQuery, ctx: ToolContext) -> RouteDecision:
    """Intelligent routing based on query content analysis."""
    _publish_status(
        ctx,
        status="thinking",
        message="Classifying query intent",
    )

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

    _publish_status(
        ctx,
        status="thinking",
        message=f"Routed query to {route} workflow",
    )

    return RouteDecision(query=args, route=route, confidence=confidence, reason=reason)


@tool(
    desc="Initialize document analysis workflow with roadmap",
    tags=["planner", "documents"],
    side_effects="stateful",
)
async def initialize_document_workflow(args: RouteDecision, ctx: ToolContext) -> DocumentState:
    """Set up document analysis pipeline."""
    if args.route != "documents":
        raise WorkflowError(
            trace_id=_trace_id_from_ctx(ctx),
            step="init_documents",
            code="INVALID_ROUTE",
            message=f"Expected documents route, got {args.route}",
        )

    roadmap = _clone_roadmap(DOCUMENT_ROADMAP)
    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[0].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Document workflow initialised",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    return DocumentState(query=args.query, roadmap=roadmap)


@tool(
    desc="Parse and enumerate document sources from query context",
    tags=["planner", "documents"],
    side_effects="read",
)
async def parse_documents(args: DocumentState, ctx: ToolContext) -> DocumentState:
    """Extract document references from query."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[0].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Parsing candidate document sources",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    await asyncio.sleep(0.05)  # Simulate parsing

    sources = [
        "README.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        "docs/deployment.md",
    ]

    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[0].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Document sources identified",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return args.model_copy(update={"sources": sources, "roadmap": roadmap})


@tool(
    desc="Extract structured metadata from documents in parallel",
    tags=["planner", "documents"],
    side_effects="read",
    latency_hint_ms=1000,  # High latency,
)
async def extract_metadata(args: DocumentState, ctx: ToolContext) -> DocumentState:
    """Concurrent metadata extraction from document sources."""

    async def analyze_file(source: str) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {
            "source": source,
            "size_kb": len(source) * 100,
            "last_modified": "2025-10-22",
            "checksum": hash(source) % 10000,
        }

    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[1].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Extracting document metadata",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    metadata = []
    for source in args.sources:
        meta = await analyze_file(source)
        metadata.append(meta)

    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[1].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Metadata extraction complete",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return args.model_copy(update={"metadata": metadata, "roadmap": roadmap})


@tool(
    desc="Generate summary from extracted document metadata",
    tags=["planner", "documents"],
    side_effects="pure",
)
async def generate_document_summary(args: DocumentState, ctx: ToolContext) -> DocumentState:
    """Synthesize findings into natural language summary."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[2].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Generating document summary",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    summary = (
        f"Analyzed {len(args.sources)} documents. "
        f"Total size: {sum(m.get('size_kb', 0) for m in args.metadata)}KB. "
        f"Key files: {', '.join(args.sources[:3])}."
    )

    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[2].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Document summary generated",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return args.model_copy(update={"summary": summary, "roadmap": roadmap})


@tool(
    desc="Render final document analysis report with artifacts",
    tags=["planner", "documents"],
    side_effects="pure",
)
async def render_document_report(args: DocumentState, ctx: ToolContext) -> FinalAnswer:
    """Package results into structured final answer."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[3].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Rendering document analysis report",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    roadmap_complete = all(s.status == "ok" for s in roadmap)
    trace_id = _trace_id_from_ctx(ctx) or uuid4().hex

    current = _mark_step_status(roadmap, step_id=DOCUMENT_ROADMAP[3].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Document workflow complete",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return FinalAnswer(
        text=args.summary or "No summary available",
        route="documents",
        artifacts={
            "sources": args.sources,
            "metadata": args.metadata,
        },
        metadata={
            "source_count": len(args.sources),
            "roadmap_complete": roadmap_complete,
            "trace_id": trace_id,
        },
    )


@tool(
    desc="Initialize bug triage workflow with diagnostic roadmap",
    tags=["planner", "bugs"],
    side_effects="stateful",
)
async def initialize_bug_workflow(args: RouteDecision, ctx: ToolContext) -> BugState:
    """Set up bug triage pipeline."""
    if args.route != "bug":
        raise WorkflowError(
            trace_id=_trace_id_from_ctx(ctx),
            step="init_bug",
            code="INVALID_ROUTE",
            message=f"Expected bug route, got {args.route}",
        )

    roadmap = _clone_roadmap(BUG_ROADMAP)
    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[0].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Bug triage workflow initialised",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    return BugState(query=args.query, roadmap=roadmap)


@tool(
    desc="Collect error logs and stack traces from system",
    tags=["planner", "bugs"],
    side_effects="read",
)
async def collect_error_logs(args: BugState, ctx: ToolContext) -> BugState:
    """Gather diagnostic logs from error context."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[0].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Collecting error logs",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    logs = [
        "ERROR: ValueError: Invalid configuration",
        "Traceback (most recent call last):",
        '  File "app.py", line 42, in process',
        "    validate_config(settings)",
        "ValueError: Missing required field: api_key",
    ]

    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[0].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Logs collected",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return args.model_copy(update={"logs": logs, "roadmap": roadmap})


@tool(
    desc="Run automated diagnostics and health checks",
    tags=["planner", "bugs"],
    side_effects="external",
    latency_hint_ms=1000,  # High latency,
)
async def run_diagnostics(args: BugState, ctx: ToolContext) -> BugState:
    """Execute validation suite to isolate failure."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[1].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Running automated diagnostics",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    await asyncio.sleep(0.1)  # Simulate diagnostic execution

    diagnostics = {
        "api_health": "degraded",
        "database": "ok",
        "cache": "ok",
        "config_validation": "failed",
    }

    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[1].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Diagnostics captured",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    return args.model_copy(update={"diagnostics": diagnostics, "roadmap": roadmap})


@tool(
    desc="Analyze diagnostics and recommend remediation steps",
    tags=["planner", "bugs"],
    side_effects="pure",
)
async def recommend_bug_fix(args: BugState, ctx: ToolContext) -> FinalAnswer:
    """Generate actionable fix recommendation."""
    roadmap = list(args.roadmap)
    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[2].id, status="running")
    _publish_status(
        ctx,
        status="thinking",
        message="Preparing remediation advice",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="running",
        roadmap=roadmap,
    )

    failed_checks = [k for k, v in args.diagnostics.items() if v in ("failed", "degraded")]

    recommendation = (
        f"Root cause: Configuration validation failure. "
        f"Failed checks: {', '.join(failed_checks)}. "
        f"Action: Review environment variables and ensure api_key is set."
    )

    current = _mark_step_status(roadmap, step_id=BUG_ROADMAP[2].id, status="ok")
    _publish_status(
        ctx,
        status="ok",
        message="Bug remediation plan ready",
        roadmap_step_id=current.id if current else None,
        roadmap_step_status="ok",
        roadmap=roadmap,
    )

    trace_id = _trace_id_from_ctx(ctx) or uuid4().hex

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
            "trace_id": trace_id,
        },
    )


@tool(
    desc="Handle simple general queries with direct LLM response",
    tags=["planner", "general"],
    side_effects="read",
    latency_hint_ms=500,  # Medium latency,
)
async def answer_general_query(args: RouteDecision, ctx: ToolContext) -> FinalAnswer:
    """Direct LLM answer for queries not requiring specialized workflows."""
    _publish_status(
        ctx,
        status="thinking",
        message="Handling general query",
    )

    await asyncio.sleep(0.05)

    answer = (
        f"I understand your query: '{args.query.text}'. "
        f"This appears to be a general question. In production, this would "
        f"invoke an LLM to generate a contextual response."
    )

    trace_id = _trace_id_from_ctx(ctx) or uuid4().hex

    _publish_status(
        ctx,
        status="ok",
        message="General response ready",
    )

    return FinalAnswer(
        text=answer,
        route="general",
        metadata={"confidence": args.confidence, "trace_id": trace_id},
    )


# ============================================================================
# Pipeline tool (Pattern A: several steps exposed as one planner tool)
# ============================================================================


async def run_document_analysis_pipeline(
    decision: RouteDecision,
    ctx: Any,
) -> FinalAnswer:
    """Run the document analysis steps in order: init, parse, extract, summarize, render."""

    state = await initialize_document_workflow(decision, ctx)
    state = await parse_documents(state, ctx)
    state = await extract_metadata(state, ctx)
    state = await generate_document_summary(state, ctx)
    return await render_document_report(state, ctx)


@tool(
    desc=("Complete document analysis pipeline (parse, extract metadata, summarize, render report)"),
    tags=["planner", "documents", "pipeline"],
    side_effects="read",
    latency_hint_ms=2000,  # Entire pipeline latency
    cost_hint="medium",  # Multiple internal operations
    extra={"auto_seq": True, "auto_seq_execute": True},
)
async def analyze_documents_pipeline(args: DocumentRouteDecision, ctx: ToolContext) -> FinalAnswer:
    """Execute complete document analysis workflow as a single operation."""
    if args.route != "documents":
        raise WorkflowError(
            trace_id=_trace_id_from_ctx(ctx),
            step="analyze_documents",
            code="INVALID_ROUTE",
            message=f"Expected documents route, got {args.route}",
        )

    meta = _resolve_meta(ctx)
    trace_id = meta.get("trace_id") if meta is not None else None
    if not isinstance(trace_id, str):
        trace_id = uuid4().hex

    try:
        result = await run_document_analysis_pipeline(args, ctx)
    except WorkflowError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _publish_status(
            ctx,
            status="error",
            message="Document workflow failed",
            roadmap_step_status="error",
        )
        raise WorkflowError(
            trace_id=trace_id,
            step="analyze_documents",
            code="DOCUMENT_PIPELINE_FAILED",
            message=str(exc) or exc.__class__.__name__,
        ) from exc

    metadata = dict(result.metadata)
    metadata.setdefault("trace_id", trace_id)
    return result.model_copy(update={"metadata": metadata})


__all__ = [
    "UserQuery",
    "RouteDecision",
    "DocumentRouteDecision",
    "DocumentState",
    "BugState",
    "FinalAnswer",
    "StatusUpdate",
    "DOCUMENT_ROADMAP",
    "BUG_ROADMAP",
    "triage_query",
    "initialize_document_workflow",
    "parse_documents",
    "extract_metadata",
    "generate_document_summary",
    "render_document_report",
    "initialize_bug_workflow",
    "collect_error_logs",
    "run_diagnostics",
    "recommend_bug_fix",
    "answer_general_query",
    "analyze_documents_pipeline",
    "run_document_analysis_pipeline",
    "WorkflowError",
]
