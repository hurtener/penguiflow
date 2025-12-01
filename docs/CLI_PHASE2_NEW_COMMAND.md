# PenguiFlow CLI Phase 2: `penguiflow new` Command Specification

**Status**: Planning
**Target Version**: v2.5
**Dependencies**: Phase 1 (`penguiflow init`) - Complete

---

## Overview

The `penguiflow new` command scaffolds complete, runnable agent projects with:
- **Best practices baked in** (observability, error handling, memory integration)
- **Meaningful placeholders** ("This is where you put X logic")
- **Test files included** (unit + integration test stubs)
- **Zero external dependencies** (only penguiflow + standard library)

---

## Design Principles

### 1. Runnable Skeletons, Not Boilerplate
Templates produce "Hello World" agents that:
- Run immediately after creation (`uv run python -m my_agent`)
- Demonstrate the pattern with stub implementations
- Have clear `# TODO:` markers for customization

### 2. Best Practices by Default
Every template includes:
- **Observability middleware** (telemetry, error extraction per PENGUIFLOW_BEST_PRACTICES.md)
- **Proper error handling** (FlowError → domain errors)
- **emit/fetch pattern** (never run_one in production code)
- **Graceful shutdown** (stop() method)
- **Test structure** (using run_one in tests only)

### 3. Memory Integration is Mandatory
All agent templates integrate with the Memory Server (Iceberg):
- Session lifecycle (start_session → per-message → end)
- Retrieval (auto_retrieve with conscious context)
- Ingestion (ingest_interaction after each turn)
- Reinforcement (feedback handling)

### 4. No External Dependencies
Templates only depend on:
- `penguiflow` (core library)
- `pydantic` (already a penguiflow dependency)
- Standard library (`asyncio`, `logging`, `dataclasses`)

Service clients (Lighthouse, Wayfinder, Memory) are **stubs** with interface definitions.

---

## Template Tiers

### Tier 1: Core Templates

| Template | Description | Use Case |
|----------|-------------|----------|
| `minimal` | Single tool + orchestrator | Learning, quick prototypes |
| `react` | ReactPlanner + 2-3 tools | Standard agent pattern |
| `parallel` | Fan-out/fan-in with join | Batch processing, multi-source |

### Tier 2: Enhancement Flags

Flags that add capabilities to **any template** (Tier 1, 3, or 4):

| Flag | What it Adds |
|------|--------------|
| `--with-streaming` | StreamChunk emission, status_publisher pattern (like `roadmap_status_updates` example) |
| `--with-hitl` | PlannerPause, resume tokens, approval flow |
| `--with-a2a` | A2A server setup for remote agent communication |
| `--no-memory` | Removes Memory Server integration (memory is ON by default) |

**Composability**: Flags work with ALL templates:
```bash
penguiflow new my-rag --template=lighthouse --with-streaming --with-hitl
penguiflow new my-nlq --template=wayfinder --with-a2a
penguiflow new my-agent --template=enterprise --with-streaming --with-a2a
```

### Tier 3: Pengui-Specific Templates

Templates that integrate with Pengui's internal services:

| Template | Service | Description |
|----------|---------|-------------|
| `lighthouse` | Lighthouse API | RAG agent with query/ingest tools |
| `wayfinder` | Wayfinder API | NLQ-to-SQL agent with clarification flow |
| `analyst` | A2A Remote | Code/data analysis agent (consumed by other agents) |

#### Lighthouse Template Details

The planner is configured with tools that map to Lighthouse API endpoints, allowing the agent to reason through the full RAG lifecycle:

```python
# Lighthouse tools registered in planner
nodes = [
    Node(upload_files_tool, name="upload"),      # POST /v1/files
    Node(ingest_tool, name="ingest"),            # POST /v1/ingest
    Node(check_status_tool, name="poll_status"), # GET /v1/ingest/{job_id}
    Node(query_tool, name="query"),              # POST /v1/query
]
```

Example agent reasoning: "Upload this PDF and find the vacation policy" → upload → ingest → poll until ready → query

#### Wayfinder Template Details

Includes the full clarification flow with status updates (same pattern as `examples/roadmap_status_updates`):

```python
# In wayfinder tools - status_publisher pattern
async def plan_query_tool(args: PlanQueryArgs, ctx: ToolContext) -> PlanQueryResult:
    publisher = ctx.tool_context.get("status_publisher")
    if callable(publisher):
        publisher(StatusUpdate(status="thinking", message="Planning SQL query..."))

    result = await wayfinder.plan_query(query=args.query)

    # Handle action_recommendation
    if result.action_recommendation == "clarify":
        if callable(publisher):
            publisher(StatusUpdate(
                status="paused",
                message=f"Need clarification: {result.suggested_clarifications}"
            ))
        # TODO: Return clarification request for HITL flow

    return PlanQueryResult(...)
```

The WebSocket/SSE consumer handles the status updates - no UI code in penguiflow.

#### Analyst Template Details

Configured as an A2A remote agent that other agents can call for complex analysis tasks. Includes:
- A2A server setup (if `--with-a2a` flag used, otherwise client-only)
- Analysis tools with structured input/output
- Memory integration for context continuity across calls

### Tier 4: Enterprise Template

| Template | Description |
|----------|-------------|
| `enterprise` | Full production setup matching `examples/planner_enterprise_agent` |

---

## Command Interface

```bash
# Tier 1: Core templates
penguiflow new my-agent                        # Default: react template
penguiflow new my-agent --template=minimal
penguiflow new my-agent --template=react
penguiflow new my-agent --template=parallel

# Tier 2: Enhancement flags
penguiflow new my-agent --with-streaming
penguiflow new my-agent --with-hitl
penguiflow new my-agent --with-a2a
penguiflow new my-agent --no-memory            # Opt-out of memory integration

# Tier 3: Pengui-specific
penguiflow new my-rag --template=lighthouse
penguiflow new my-nlq --template=wayfinder
penguiflow new my-analyst --template=analyst

# Tier 4: Enterprise
penguiflow new my-platform --template=enterprise

# Combinations
penguiflow new my-agent --template=react --with-streaming --with-hitl
```

---

## Project Structure (Generated)

### Minimal Template

```
my-agent/
├── pyproject.toml
├── src/
│   └── my_agent/
│       ├── __init__.py
│       ├── __main__.py           # Entry point: python -m my_agent
│       ├── config.py             # Environment-based configuration
│       ├── orchestrator.py       # Main orchestrator with emit/fetch
│       ├── tools.py              # Example tool with Args/Result models
│       ├── telemetry.py          # Observability middleware
│       └── clients/
│           ├── __init__.py
│           └── memory.py         # Memory Server client stub
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # Pytest fixtures
│   ├── test_tools.py             # Unit tests for tools
│   └── test_orchestrator.py      # Integration tests with run_one
├── .env.example
├── .gitignore
├── .vscode/                      # From penguiflow init
└── README.md
```

### React Template (Default)

```
my-agent/
├── pyproject.toml
├── src/
│   └── my_agent/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── orchestrator.py       # ReactPlanner-based orchestrator
│       ├── planner.py            # Planner configuration + catalog
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── search.py         # Example: search tool
│       │   └── analyze.py        # Example: analysis tool
│       ├── models.py             # Shared Pydantic models
│       ├── telemetry.py
│       └── clients/
│           ├── __init__.py
│           └── memory.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_tools/
│   │   ├── __init__.py
│   │   ├── test_search.py
│   │   └── test_analyze.py
│   └── test_orchestrator.py
├── .env.example
├── .gitignore
├── .vscode/
└── README.md
```

### Lighthouse Template (RAG)

```
my-rag/
├── pyproject.toml
├── src/
│   └── my_rag/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── orchestrator.py
│       ├── planner.py
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── query.py          # POST /v1/query wrapper
│       │   ├── upload.py         # POST /v1/files wrapper
│       │   ├── ingest.py         # POST /v1/ingest wrapper
│       │   └── status.py         # GET /v1/ingest/{job_id} wrapper
│       ├── models.py             # Lighthouse request/response models
│       ├── telemetry.py
│       └── clients/
│           ├── __init__.py
│           ├── memory.py
│           └── lighthouse.py     # Lighthouse API client stub
├── tests/
│   └── ...
├── .env.example
├── .gitignore
├── .vscode/
└── README.md
```

### Wayfinder Template (NLQ-to-SQL)

```
my-nlq/
├── pyproject.toml
├── src/
│   └── my_nlq/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── orchestrator.py
│       ├── planner.py
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── preflight.py      # POST /v1/nlq/preflight
│       │   ├── plan_query.py     # POST /v1/nlq/agent-query
│       │   ├── execute.py        # POST /v1/nlq/agent-query:execute
│       │   ├── refine.py         # POST /v1/nlq/agent-query:refine
│       │   └── clarify.py        # Handle ambiguities flow
│       ├── models.py             # Wayfinder request/response models
│       ├── telemetry.py
│       └── clients/
│           ├── __init__.py
│           ├── memory.py
│           └── wayfinder.py      # Wayfinder API client stub
├── tests/
│   └── ...
├── .env.example
├── .gitignore
├── .vscode/
└── README.md
```

### Enterprise Template

```
my-platform/
├── pyproject.toml
├── src/
│   └── my_platform/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py             # Full config with all services
│       ├── orchestrator.py       # Production orchestrator
│       ├── planner.py            # ReactPlanner with all patterns
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── search.py
│       │   ├── analyze.py
│       │   ├── summarize.py
│       │   └── validate.py
│       ├── models.py
│       ├── telemetry.py          # Full observability setup
│       ├── clients/
│       │   ├── __init__.py
│       │   ├── memory.py
│       │   └── service_registry.py
│       └── server/               # Optional: A2A server setup
│           ├── __init__.py
│           └── handlers.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_tools/
│   ├── test_orchestrator.py
│   └── test_integration.py       # Full flow tests
├── .env.example
├── .gitignore
├── .vscode/
└── README.md
```

---

## Key File Templates

### 1. Orchestrator Pattern (orchestrator.py)

```python
"""Main orchestrator for {project_name}."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from penguiflow.errors import FlowError
from penguiflow.types import Headers, Message

from .config import Config
from .planner import build_planner
from .telemetry import AgentTelemetry
from .clients.memory import MemoryClient

_LOGGER = logging.getLogger(__name__)


class {ProjectName}FlowError(RuntimeError):
    """Raised when the agent flow surfaces a FlowError."""

    def __init__(self, flow_error: FlowError) -> None:
        message = flow_error.message or str(flow_error)
        super().__init__(message)
        self.flow_error = flow_error


@dataclass
class AgentResponse:
    """Response from agent execution."""

    answer: str
    trace_id: str
    # TODO: Add your response fields here


class {ProjectName}Orchestrator:
    """Production orchestrator using emit/fetch pattern.

    Following PenguiFlow best practices:
    - Flow started once in __init__
    - emit/fetch for each request (NOT run_one)
    - Proper error handling with domain errors
    - Observability middleware attached
    - Graceful shutdown via stop()
    """

    def __init__(
        self,
        config: Config,
        *,
        telemetry: AgentTelemetry | None = None,
    ) -> None:
        self._config = config
        self._memory = MemoryClient(config.memory_base_url)

        # Build planner (ReactPlanner + tools)
        planner_bundle = build_planner(config)
        self._planner = planner_bundle.planner

        # Setup telemetry (observability is non-negotiable)
        self._telemetry = telemetry or AgentTelemetry(
            flow_name="{project_name}",
            logger=_LOGGER,
        )

        # Attach middleware BEFORE starting
        # This ensures all errors are properly logged with full context
        self._planner.event_callback = self._telemetry.record_planner_event

        self._started = True

    async def execute(
        self,
        query: str,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> AgentResponse:
        """Execute the agent for a user query.

        Lifecycle:
        1. Load memory context (conscious + retrieval)
        2. Run planner with context
        3. Ingest interaction to memory
        4. Return response
        """
        # 1. Load memory context
        conscious = await self._memory.start_session(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
        )

        retrieval = await self._memory.auto_retrieve(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            prompt=query,
        )

        # 2. Build context for planner
        llm_context = {
            "conscious_memories": conscious.get("conscious", []),
            "retrieved_memories": retrieval.get("snippets", []),
        }

        tool_context = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "status_publisher": self._telemetry.publish_status,
        }

        # 3. Execute planner
        result = await self._planner.run(
            query=query,
            llm_context=llm_context,
            tool_context=tool_context,
        )

        # 4. Handle errors
        if hasattr(result, "error") and result.error:
            _LOGGER.error(
                "Planner execution failed",
                extra={
                    "trace_id": result.trace_id,
                    "error": str(result.error),
                },
            )
            raise {ProjectName}FlowError(result.error)

        # 5. Extract answer
        # TODO: Adjust based on your FinalAnswer model
        answer = result.payload.text if hasattr(result.payload, "text") else str(result.payload)

        # 6. Ingest interaction to memory
        await self._memory.ingest_interaction(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_prompt=query,
            agent_response=answer,
        )

        return AgentResponse(
            answer=answer,
            trace_id=result.trace_id,
        )

    async def stop(self) -> None:
        """Graceful shutdown - always implement this."""
        if self._started:
            # Planner cleanup if needed
            self._started = False
            _LOGGER.info("Orchestrator stopped")
```

### 2. Telemetry Pattern (telemetry.py)

```python
"""Observability middleware for {project_name}.

This module implements the telemetry pattern from PENGUIFLOW_BEST_PRACTICES.md.
It extracts full error details from FlowEvents, making debugging possible.

WITHOUT this middleware: "node_error" with no details
WITH this middleware: Full stack trace, SQL statements, parameters, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Callable

from penguiflow.metrics import FlowEvent


@dataclass
class StatusUpdate:
    """Status update for UI/logging."""

    status: str  # "thinking", "ok", "error", "paused"
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class AgentTelemetry:
    """Telemetry middleware for agent flows.

    Attach to planner via:
        planner.event_callback = telemetry.record_planner_event

    This ensures all errors are logged with full context including:
    - Error class and message
    - Full stack trace
    - SQL statements (if database errors)
    - FlowError codes
    """

    def __init__(
        self,
        *,
        flow_name: str,
        logger: logging.Logger,
        status_callback: Callable[[StatusUpdate], None] | None = None,
    ) -> None:
        self.flow_name = flow_name
        self.logger = logger
        self._status_callback = status_callback
        self._latencies: list[float] = []

    async def record_planner_event(self, event: FlowEvent) -> FlowEvent:
        """Middleware that intercepts all planner events.

        CRITICAL: This extracts error_payload which contains the actual
        debugging information that would otherwise be swallowed.
        """
        event_type = event.event_type

        if event_type == "node_start":
            self.logger.debug(
                "node_start",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "flow": self.flow_name,
                },
            )

        elif event_type == "node_success":
            self.logger.debug(
                "node_success",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "latency_ms": event.latency_ms,
                    "flow": self.flow_name,
                },
            )
            if event.latency_ms:
                self._latencies.append(event.latency_ms)

        elif event_type == "node_error":
            # THIS IS CRITICAL - extract full error details
            error_payload = event.error_payload or {}

            self.logger.error(
                "node_error",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "flow": self.flow_name,
                    "error_class": error_payload.get("error_class"),
                    "error_message": error_payload.get("error_message"),
                    "error_traceback": error_payload.get("error_traceback"),
                    "flow_error_code": error_payload.get("code"),
                    "flow_error_message": error_payload.get("message"),
                    # Include full payload for debugging
                    **error_payload,
                },
            )

        elif event_type == "node_retry":
            self.logger.warning(
                "node_retry",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "attempt": event.attempt,
                    "flow": self.flow_name,
                },
            )

        # Always return event unmodified - middleware is read-only
        return event

    def publish_status(self, update: StatusUpdate) -> None:
        """Publish status update for UI/monitoring."""
        self.logger.info(
            f"status_{update.status}",
            extra={
                "status": update.status,
                "message": update.message,
                "flow": self.flow_name,
            },
        )
        if self._status_callback:
            self._status_callback(update)

    def get_average_latency(self) -> float | None:
        """Get average node latency in ms."""
        if not self._latencies:
            return None
        return sum(self._latencies) / len(self._latencies)
```

### 3. Memory Client Stub (clients/memory.py)

```python
"""Memory Server (Iceberg) client stub.

This is a stub implementation showing the interface.
Replace with actual HTTP client for production.

Memory Server Integration:
1. start_session() - Load conscious bundle at conversation start
2. auto_retrieve() - Get relevant memories per message
3. ingest_interaction() - Store conversation turn
4. reinforce() - Update memory based on feedback
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ConciousBundle:
    """Response from start_session."""

    memories: list[dict[str, Any]]
    token_estimate: int
    active_branch: str

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> ConciousBundle:
        return cls(
            memories=data.get("conscious", []),
            token_estimate=data.get("token_estimate", 0),
            active_branch=data.get("active_branch_label", "main"),
        )


@dataclass
class RetrievalResult:
    """Response from auto_retrieve."""

    snippets: list[dict[str, Any]]
    tokens_estimate: int
    used_summary_id: str | None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> RetrievalResult:
        return cls(
            snippets=data.get("snippets", []),
            tokens_estimate=data.get("tokens_estimate", 0),
            used_summary_id=data.get("used_summary_id"),
        )


class MemoryClient:
    """Client for Memory Server (Iceberg).

    TODO: Replace stub implementations with actual HTTP calls.

    Endpoints:
    - POST /memory/start_session
    - POST /memory/auto_retrieve
    - POST /memory/ingest_interaction
    - POST /memory/reinforce
    - POST /memory/federated_query
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        # TODO: Initialize HTTP client (httpx, aiohttp, etc.)

    async def start_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        include_branch_tree: bool = False,
        max_branch_fragments: int = 20,
    ) -> dict[str, Any]:
        """Load conscious bundle for conversation start.

        Returns:
            {
                "conscious": [
                    {
                        "id": "F123",
                        "excerpt": "User prefers...",
                        "timestamp": "...",
                        "privacy": "personal",
                        "type": "knowledge",
                        "branch_label": "main"
                    },
                    ...
                ],
                "token_estimate": 450,
                "active_branch_label": "main"
            }
        """
        # TODO: Implement actual HTTP call
        # POST {base_url}/memory/start_session
        # Body: { tenant_id, user_id, session_id, include_branch_tree, max_branch_fragments }

        # Stub response for development
        return {
            "conscious": [],
            "token_estimate": 0,
            "active_branch_label": "main",
        }

    async def auto_retrieve(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        prompt: str,
        k: int = 5,
        include_branches: list[str] | None = None,
        exclude_branches: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve relevant memories for current message.

        Returns:
            {
                "snippets": [
                    {
                        "id": "F456",
                        "excerpt": "...",
                        "score": 0.87,
                        "timestamp": "...",
                        "branch_label": "main"
                    },
                    ...
                ],
                "tokens_estimate": 280
            }
        """
        # TODO: Implement actual HTTP call
        # POST {base_url}/memory/auto_retrieve

        # Stub response
        return {
            "snippets": [],
            "tokens_estimate": 0,
        }

    async def ingest_interaction(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_prompt: str,
        agent_response: str,
        source: str = "chat",
        privacy: str = "personal",
        parent_interaction_id: str | None = None,
        branch_label: str = "main",
    ) -> dict[str, Any]:
        """Store conversation turn for future retrieval.

        Returns:
            {"id": "F789"}
        """
        # TODO: Implement actual HTTP call
        # POST {base_url}/memory/ingest_interaction

        # Stub response
        return {"id": "stub-fragment-id"}

    async def reinforce(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_id: str,
        delta: int,
    ) -> dict[str, Any]:
        """Update memory based on feedback.

        Args:
            delta: Reinforcement signal
                   +5 for COMMIT (strong positive)
                   +3 for like
                   +1 for EXPLORE (mild positive)
                   -2 for CORRECTION
                   -3 for dislike
        """
        # TODO: Implement actual HTTP call
        # POST {base_url}/memory/reinforce

        return {"success": True}

    async def federated_query(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        include_scopes: list[str] | None = None,
        k: int = 6,
    ) -> dict[str, Any]:
        """Query across personal/team/org scopes.

        Args:
            include_scopes: ["personal", "team", "org"]
        """
        # TODO: Implement actual HTTP call
        # POST {base_url}/memory/federated_query

        return {
            "snippets": [],
            "scope_breakdown": {"personal": 0, "team": 0, "org": 0},
        }
```

### 4. Lighthouse Client Stub (clients/lighthouse.py)

```python
"""Lighthouse API client stub for RAG operations.

Lighthouse Endpoints:
1. POST /v1/query - Main retrieval + answer generation
2. POST /v1/files - Upload documents
3. POST /v1/ingest - Trigger indexing
4. GET /v1/ingest/{job_id} - Check ingestion status

Typical ReAct Flow:
1. Upload documents → POST /v1/files
2. Ingest to index → POST /v1/ingest with file_ids
3. Poll until ready → GET /v1/ingest/{job_id}
4. Query knowledge → POST /v1/query
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class QueryResponse:
    """Response from Lighthouse query."""

    final_answer: str
    citations: list[dict[str, Any]]
    sub_questions: list[dict[str, Any]]
    persona: str
    trace_id: str


@dataclass
class IngestionJob:
    """Ingestion job status."""

    job_id: str
    status: str  # "pending", "processing", "completed", "failed"
    document_ids: list[str]


class LighthouseClient:
    """Client for Lighthouse RAG API.

    TODO: Replace stub implementations with actual HTTP calls.
    """

    def __init__(self, base_url: str, tenant_id: str) -> None:
        self.base_url = base_url
        self.tenant_id = tenant_id

    async def query(
        self,
        *,
        query: str,
        persona: str | None = None,
        topk: int = 10,
    ) -> QueryResponse:
        """Main RAG query - retrieves and generates answer.

        POST /v1/query
        {
            "query": "What is our vacation policy?",
            "tenant_id": "...",
            "persona": "hr",
            "topk": 10
        }
        """
        # TODO: Implement actual HTTP call

        # Stub response
        return QueryResponse(
            final_answer="[TODO: Implement Lighthouse query]",
            citations=[],
            sub_questions=[],
            persona=persona or "general",
            trace_id="stub-trace-id",
        )

    async def upload_files(
        self,
        files: list[tuple[str, bytes]],  # (filename, content)
    ) -> list[dict[str, Any]]:
        """Upload documents to be indexed.

        POST /v1/files (multipart/form-data)
        Header: X-Tenant-ID
        """
        # TODO: Implement actual HTTP call

        return [{"file_id": f"stub-{i}", "filename": name} for i, (name, _) in enumerate(files)]

    async def ingest(
        self,
        *,
        file_ids: list[str],
        topics: list[str] | None = None,
        facets: dict[str, str] | None = None,
    ) -> IngestionJob:
        """Trigger indexing of uploaded files.

        POST /v1/ingest
        {
            "file_ids": ["uuid-1", "uuid-2"],
            "topics": ["policies"],
            "facets": {"department": "hr"}
        }
        """
        # TODO: Implement actual HTTP call

        return IngestionJob(
            job_id="stub-job-id",
            status="pending",
            document_ids=[],
        )

    async def get_ingestion_status(self, job_id: str) -> IngestionJob:
        """Poll job completion.

        GET /v1/ingest/{job_id}
        """
        # TODO: Implement actual HTTP call

        return IngestionJob(
            job_id=job_id,
            status="completed",
            document_ids=["stub-doc-1"],
        )
```

### 5. Wayfinder Client Stub (clients/wayfinder.py)

```python
"""Wayfinder API client stub for NLQ-to-SQL operations.

Wayfinder Endpoints:
1. POST /v1/nlq/preflight - Fast confidence check
2. POST /v1/nlq/agent-query - Plan query (main endpoint)
3. POST /v1/nlq/agent-query:execute - Execute planned query
4. POST /v1/nlq/agent-query:refine - Refine with clarifications
5. POST /v1/nlq/run:self-curate - One-shot plan+execute

Typical ReAct Flow:
1. Preflight check → confidence assessment
2. Plan query → get SQL + action_recommendation
3. Based on recommendation:
   - "execute" → execute query
   - "clarify" → show suggested_clarifications, then refine
   - "review" → human review
   - "fail" → handle error
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ActionRecommendation = Literal["execute", "clarify", "review", "fail"]


@dataclass
class Confidence:
    """Confidence breakdown by stage."""

    span_extraction: float
    semantic_routing: float
    sql_generation: float
    validation: float
    overall: float


@dataclass
class Ambiguity:
    """Detected ambiguity needing clarification."""

    type: str  # "metric_choice", "time_range", "entity_scope", etc.
    description: str
    options: list[str]


@dataclass
class QueryPlan:
    """Response from agent-query."""

    query_id: str
    session_id: str
    sql: str | None
    action_recommendation: ActionRecommendation
    confidence: Confidence
    ambiguities: list[Ambiguity]
    assumptions: list[dict[str, Any]]
    risk_level: str  # "safe", "caution", "high_risk"
    suggested_clarifications: list[str]


@dataclass
class QueryResult:
    """Response from execute."""

    query_id: str
    executed: bool
    row_count: int
    elapsed_ms: float
    columns: list[str]
    rows: list[list[Any]]
    sql_was_fixed: bool
    execution_error: str | None


class WayfinderClient:
    """Client for Wayfinder NLQ-to-SQL API.

    TODO: Replace stub implementations with actual HTTP calls.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def preflight(
        self,
        *,
        query: str,
        topic_filters: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fast routing + context check (~100-500ms).

        POST /v1/nlq/preflight

        Use for real-time validation before full planning.
        """
        # TODO: Implement actual HTTP call

        return {
            "routing": {"success": True},
            "context_summary": {
                "primary_topic": "sales",
                "evidence_card_count": 5,
            },
            "feasibility": {
                "can_route": True,
                "confidence_level": 0.8,
                "recommended_action": "proceed",
            },
        }

    async def plan_query(
        self,
        *,
        query: str,
        session_id: str | None = None,
        topic_filters: list[str] | None = None,
        min_confidence: float = 0.7,
    ) -> QueryPlan:
        """Plan NLQ → SQL without executing.

        POST /v1/nlq/agent-query

        Returns rich signals for agent decision-making.
        """
        # TODO: Implement actual HTTP call

        return QueryPlan(
            query_id="stub-query-id",
            session_id=session_id or "stub-session",
            sql="SELECT * FROM stub_table LIMIT 10",
            action_recommendation="execute",
            confidence=Confidence(
                span_extraction=0.9,
                semantic_routing=0.85,
                sql_generation=0.8,
                validation=0.9,
                overall=0.85,
            ),
            ambiguities=[],
            assumptions=[],
            risk_level="safe",
            suggested_clarifications=[],
        )

    async def execute_query(
        self,
        *,
        query_id: str,
        session_id: str | None = None,
        max_rows: int = 1000,
        timeout_seconds: int = 30,
    ) -> QueryResult:
        """Execute a previously planned query.

        POST /v1/nlq/agent-query:execute

        Includes self-healing SQL repair.
        """
        # TODO: Implement actual HTTP call

        return QueryResult(
            query_id=query_id,
            executed=True,
            row_count=0,
            elapsed_ms=50.0,
            columns=["id", "name", "value"],
            rows=[],
            sql_was_fixed=False,
            execution_error=None,
        )

    async def refine_query(
        self,
        *,
        query_id: str,
        clarifications: list[dict[str, Any]],
    ) -> QueryPlan:
        """Refine query based on user clarifications.

        POST /v1/nlq/agent-query:refine

        Clarification types:
        - disambiguate_metric: {"type": "...", "selected_option": "gross_revenue"}
        - disambiguate_dimension: {...}
        - confirm_time_range: {"type": "...", "time_range": "2024-01-01 to 2024-03-31"}
        - add_filter: {...}
        - provide_context: {...}
        """
        # TODO: Implement actual HTTP call

        return QueryPlan(
            query_id="stub-refined-query-id",
            session_id="stub-session",
            sql="SELECT * FROM refined_table",
            action_recommendation="execute",
            confidence=Confidence(
                span_extraction=0.95,
                semantic_routing=0.9,
                sql_generation=0.9,
                validation=0.95,
                overall=0.92,
            ),
            ambiguities=[],
            assumptions=[],
            risk_level="safe",
            suggested_clarifications=[],
        )
```

### 6. Test Patterns (tests/test_orchestrator.py)

```python
"""Integration tests for {ProjectName}Orchestrator.

Following PenguiFlow testing best practices:
- run_one() is used HERE in tests (never in production)
- Tests verify envelope preservation
- Tests cover error scenarios
- Tests verify node sequences
"""

from __future__ import annotations

import pytest

from penguiflow.testkit import run_one, assert_node_sequence
from penguiflow.types import Message, Headers

from {project_name}.orchestrator import {ProjectName}Orchestrator
from {project_name}.config import Config


class MockMemoryClient:
    """Mock memory client for testing."""

    async def start_session(self, **kwargs):
        return {"conscious": [], "token_estimate": 0}

    async def auto_retrieve(self, **kwargs):
        return {"snippets": [], "tokens_estimate": 0}

    async def ingest_interaction(self, **kwargs):
        return {"id": "test-fragment-id"}


@pytest.fixture
def config() -> Config:
    """Test configuration."""
    return Config(
        memory_base_url="http://localhost:8000",
        # Add other config fields
    )


@pytest.fixture
def mock_memory() -> MockMemoryClient:
    """Mock memory client."""
    return MockMemoryClient()


class TestOrchestratorIntegration:
    """Integration tests using run_one (testing utility)."""

    @pytest.mark.asyncio
    async def test_basic_execution(self, config: Config) -> None:
        """Test basic query execution."""
        orchestrator = {ProjectName}Orchestrator(config)
        orchestrator._memory = MockMemoryClient()

        response = await orchestrator.execute(
            query="Hello, world!",
            tenant_id="test-tenant",
            user_id="test-user",
            session_id="test-session",
        )

        assert response.answer is not None
        assert response.trace_id is not None

        await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_memory_integration(self, config: Config) -> None:
        """Test that memory client is called correctly."""
        orchestrator = {ProjectName}Orchestrator(config)

        # Track calls
        calls = []

        class TrackedMemoryClient(MockMemoryClient):
            async def start_session(self, **kwargs):
                calls.append(("start_session", kwargs))
                return await super().start_session(**kwargs)

            async def auto_retrieve(self, **kwargs):
                calls.append(("auto_retrieve", kwargs))
                return await super().auto_retrieve(**kwargs)

            async def ingest_interaction(self, **kwargs):
                calls.append(("ingest_interaction", kwargs))
                return await super().ingest_interaction(**kwargs)

        orchestrator._memory = TrackedMemoryClient()

        await orchestrator.execute(
            query="Test query",
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
        )

        # Verify lifecycle
        assert len(calls) == 3
        assert calls[0][0] == "start_session"
        assert calls[1][0] == "auto_retrieve"
        assert calls[2][0] == "ingest_interaction"

        await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, config: Config) -> None:
        """Test that stop() cleans up properly."""
        orchestrator = {ProjectName}Orchestrator(config)
        orchestrator._memory = MockMemoryClient()

        assert orchestrator._started is True

        await orchestrator.stop()

        assert orchestrator._started is False


class TestToolsUnit:
    """Unit tests for individual tools.

    These test tools in isolation without the full orchestrator.
    """

    @pytest.mark.asyncio
    async def test_search_tool_basic(self) -> None:
        """Test search tool with basic input."""
        # TODO: Import your tool and test it
        # from {project_name}.tools.search import search_tool, SearchArgs
        #
        # result = await search_tool(
        #     SearchArgs(query="test"),
        #     mock_context,
        # )
        #
        # assert result.results is not None
        pass

    @pytest.mark.asyncio
    async def test_search_tool_empty_query(self) -> None:
        """Test search tool handles empty query."""
        # TODO: Test edge cases
        pass
```

---

## Implementation Phases

### Phase 2.1: Core Infrastructure

**Goal**: Build the `new` command framework

**Tasks**:
1. Create `penguiflow/cli/new.py` with Click command
2. Add Jinja2 as optional dependency (`cli` extra)
3. Implement template discovery and rendering
4. Add `--dry-run` support
5. Add tests for the new command

**Deliverables**:
- `penguiflow new my-agent` creates minimal template
- `penguiflow new my-agent --dry-run` shows what would be created

### Phase 2.2: Tier 1 Templates

**Goal**: Implement core templates

**Tasks**:
1. Create `minimal` template
2. Create `react` template (default)
3. Create `parallel` template
4. All templates include:
   - Orchestrator with emit/fetch pattern
   - Telemetry middleware
   - Memory client stub
   - Test files

**Deliverables**:
- All Tier 1 templates runnable
- Tests pass for each template

### Phase 2.3: Enhancement Flags

**Goal**: Add capability flags that work with ALL templates

**Tasks**:
1. Implement `--with-streaming` (adds StreamChunk + status_publisher pattern from `roadmap_status_updates`)
2. Implement `--with-hitl` (adds PlannerPause handling, resume tokens)
3. Implement `--with-a2a` (adds A2A server setup)
4. Implement `--no-memory` (removes memory integration)
5. Ensure flags compose with Tier 1, 3, and 4 templates

**Deliverables**:
- Flags can be combined with ANY template (minimal, react, lighthouse, wayfinder, enterprise, etc.)
- Each flag adds well-defined, self-contained functionality
- Template rendering merges base template + flag additions

### Phase 2.4: Tier 3 Pengui-Specific Templates

**Goal**: Implement service-specific templates

**Tasks**:
1. Create `lighthouse` template (RAG)
2. Create `wayfinder` template (NLQ-to-SQL)
3. Create `analyst` template (A2A remote agent)
4. Include service client stubs with full interface

**Deliverables**:
- Each template demonstrates service integration pattern
- Tools map to actual API endpoints

### Phase 2.5: Enterprise Template

**Goal**: Production-ready template

**Tasks**:
1. Create `enterprise` template matching `examples/planner_enterprise_agent`
2. Remove fake business logic, replace with placeholders
3. Include full observability setup
4. Include A2A server option
5. Comprehensive test suite

**Deliverables**:
- Template matches production patterns
- Clear `# TODO:` markers for customization

---

## Success Criteria

1. **Immediate Runnable**: `uv sync && uv run python -m my_agent` works
2. **Best Practices Embedded**: Observability, error handling, memory integration
3. **Clear Customization Points**: `# TODO:` markers guide developers
4. **Test Coverage**: Every template includes passing tests
5. **No External Dependencies**: Only penguiflow + standard library
6. **Documentation**: README in each generated project

---

## Open Questions

1. **Jinja2 dependency**: Add to `cli` extra or make core?
   - Recommendation: `cli` extra (`penguiflow[cli]`)

2. **Memory integration default**: Always include or opt-in?
   - Recommendation: Always include, `--no-memory` to opt-out

3. **Template versioning**: How to handle template updates?
   - Recommendation: Templates tied to penguiflow version

4. **Interactive mode**: Prompt for choices if no flags?
   - Recommendation: No for v2.5, consider for v2.6

---

## Appendix: Service API Reference

### Memory Server (Iceberg)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/memory/start_session` | POST | Load conscious bundle |
| `/memory/auto_retrieve` | POST | Get relevant memories |
| `/memory/ingest_interaction` | POST | Store conversation turn |
| `/memory/reinforce` | POST | Update based on feedback |
| `/memory/federated_query` | POST | Query across scopes |

### Lighthouse API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/query` | POST | Main RAG query |
| `/v1/files` | POST | Upload documents |
| `/v1/ingest` | POST | Trigger indexing |
| `/v1/ingest/{job_id}` | GET | Check ingestion status |

### Wayfinder API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/nlq/preflight` | POST | Fast confidence check |
| `/v1/nlq/agent-query` | POST | Plan query |
| `/v1/nlq/agent-query:execute` | POST | Execute planned query |
| `/v1/nlq/agent-query:refine` | POST | Refine with clarifications |
| `/v1/nlq/run:self-curate` | POST | One-shot plan+execute |
| `/v1/jobs/{job_id}` | GET | Check job status |
