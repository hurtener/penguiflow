# PenguiFlow Templating Quickguide

> **Version**: 2.5 | **Last Updated**: December 2025

The `penguiflow new` command scaffolds production-ready agent projects with best practices baked in. This guide covers every template, flag, and pattern you need to ship agents fast.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [Template Tiers Overview](#template-tiers-overview)
4. [Tier 1: Core Templates](#tier-1-core-templates)
   - [minimal](#minimal-template)
   - [react](#react-template-default)
   - [parallel](#parallel-template)
5. [Tier 2: Service Templates](#tier-2-service-templates)
   - [lighthouse](#lighthouse-template)
   - [wayfinder](#wayfinder-template)
   - [analyst](#analyst-template)
6. [Tier 3: Enterprise Template](#tier-3-enterprise-template)
7. [Bonus: Additional Templates](#bonus-additional-templates)
   - [flow](#flow-template)
   - [controller](#controller-template)
8. [Enhancement Flags](#enhancement-flags)
   - [--with-streaming](#--with-streaming)
   - [--with-hitl](#--with-hitl)
   - [--with-a2a](#--with-a2a)
   - [--no-memory](#--no-memory)
9. [Project Structure](#project-structure)
10. [Configuration](#configuration)
11. [Running Your Agent](#running-your-agent)
12. [Testing](#testing)
13. [Best Practices](#best-practices)
14. [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# Install penguiflow with CLI support
pip install penguiflow[cli]

# Create your first agent (uses 'react' template by default)
penguiflow new my-agent

# Navigate and run
cd my-agent
uv sync
cp .env.example .env  # Configure your LLM API keys
uv run python -m my_agent
```

**30 seconds to a working agent.**

---

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Install PenguiFlow

```bash
# With uv (recommended)
uv pip install penguiflow[cli]

# With pip
pip install penguiflow[cli]
```

The `[cli]` extra includes Jinja2 for template rendering.

---

## Template Tiers Overview

| Tier | Templates | Use Case |
|------|-----------|----------|
| **Tier 1** | `minimal`, `react`, `parallel` | Core patterns for most agents |
| **Tier 2** | `lighthouse`, `wayfinder`, `analyst` | Pengui service integrations |
| **Tier 3** | `enterprise` | Production-grade full stack |
| **Bonus** | `flow`, `controller` | Alternative architectural patterns |
| **Flags** | `--with-streaming`, `--with-hitl`, `--with-a2a`, `--no-memory` | Add capabilities to any template |

### Decision Tree

```
What are you building?
│
├─ Learning/Prototyping → minimal
│
├─ Standard Agent (recommended) → react
│
├─ Batch Processing / Multi-source → parallel
│
├─ RAG Application → lighthouse
│
├─ NLQ-to-SQL Application → wayfinder
│
├─ Remote Analysis Service → analyst
│
├─ Production Platform → enterprise
│
├─ Simple Linear Pipeline → flow
│
└─ Iterative Refinement Loop → controller
```

---

## Tier 1: Core Templates

### `minimal` Template

**Best for**: Learning PenguiFlow, quick prototypes, single-tool agents.

```bash
penguiflow new my-agent --template=minimal
```

#### What You Get

```
my-agent/
├── src/my_agent/
│   ├── __main__.py          # Entry point
│   ├── config.py            # Environment configuration
│   ├── orchestrator.py      # ReactPlanner orchestrator
│   ├── tools.py             # Single demo tool
│   ├── telemetry.py         # Observability middleware
│   └── clients/
│       └── memory.py        # Memory Server stub
├── tests/
│   ├── test_orchestrator.py
│   └── test_tools.py
├── pyproject.toml
└── .env.example
```

#### Architecture

```
┌─────────────────────────────────────────────────────┐
│                  ReactPlanner                        │
│  ┌─────────────────────────────────────────────┐    │
│  │  LLM decides: call tool or finish           │    │
│  └─────────────────────────────────────────────┘    │
│                       │                              │
│                       ▼                              │
│  ┌─────────────────────────────────────────────┐    │
│  │           answer_question tool              │    │
│  │  (Your single tool - expand from here)      │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

#### Key Files

**`tools.py`** - Define your tool:
```python
from penguiflow.catalog import tool
from penguiflow.planner import ToolContext

@tool(desc="Respond to user questions", tags=["demo"])
async def answer_question(args: Question, ctx: ToolContext) -> Answer:
    # Access tenant context
    tenant_id = ctx.tool_context.get("tenant_id")

    # TODO: Add your logic here
    return Answer(answer=f"[{tenant_id}] {args.text}")
```

---

### `react` Template (Default)

**Best for**: Standard agent pattern, multi-tool reasoning, most production use cases.

```bash
penguiflow new my-agent                    # Default
penguiflow new my-agent --template=react   # Explicit
```

#### What You Get

```
my-agent/
├── src/my_agent/
│   ├── __main__.py
│   ├── config.py
│   ├── orchestrator.py      # Full ReactPlanner setup
│   ├── planner.py           # Planner configuration + catalog
│   ├── models.py            # Shared Pydantic models
│   ├── telemetry.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── search.py        # Example: search tool
│   │   └── analyze.py       # Example: analysis tool
│   └── clients/
│       └── memory.py
├── tests/
│   ├── test_orchestrator.py
│   └── test_tools/
│       ├── test_search.py
│       └── test_analyze.py
└── ...
```

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ReactPlanner                            │
│                                                              │
│   Query → [LLM Reasoning] → Tool Selection → Execute         │
│                    ↑              │                          │
│                    └──────────────┘ (iterate until done)     │
│                                                              │
│   Tools:                                                     │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│   │  search  │  │ analyze  │  │  (add    │                  │
│   │   tool   │  │   tool   │  │  more!)  │                  │
│   └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

#### Adding New Tools

1. Create `src/my_agent/tools/my_tool.py`:

```python
from pydantic import BaseModel
from penguiflow.catalog import tool
from penguiflow.planner import ToolContext

class MyToolArgs(BaseModel):
    """Input for my tool."""
    query: str

class MyToolResult(BaseModel):
    """Output from my tool."""
    data: str

@tool(desc="What this tool does - be specific for LLM", tags=["category"])
async def my_tool(args: MyToolArgs, ctx: ToolContext) -> MyToolResult:
    # Access shared context
    tenant_id = ctx.tool_context.get("tenant_id")

    # Your implementation
    result = await do_something(args.query)

    return MyToolResult(data=result)
```

2. Register in `src/my_agent/tools/__init__.py`:

```python
from .my_tool import my_tool, MyToolArgs, MyToolResult

__all__ = [..., "my_tool", "MyToolArgs", "MyToolResult"]
```

3. Add to planner in `src/my_agent/planner.py`:

```python
from .tools import my_tool, MyToolArgs, MyToolResult

def build_planner(config: Config) -> PlannerBundle:
    registry = ModelRegistry()
    registry.register("my_tool", MyToolArgs, MyToolResult)

    nodes = [
        ...,
        Node(my_tool, name="my_tool"),
    ]
```

---

### `parallel` Template

**Best for**: Batch processing, multi-source queries, fan-out/fan-in patterns.

```bash
penguiflow new my-agent --template=parallel
```

#### What You Get

```
my-agent/
├── src/my_agent/
│   ├── orchestrator.py      # Parallel execution orchestrator
│   ├── planner.py           # Parallel plan configuration
│   ├── tools/
│   │   ├── fetch.py         # Parallel fetch tools
│   │   └── merge.py         # Result merging tool
│   └── ...
└── ...
```

#### Architecture

```
                    ┌─────────────┐
                    │   Query     │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ fetch_a  │ │ fetch_b  │ │ fetch_c  │  ← Parallel
        └────┬─────┘ └────┬─────┘ └────┬─────┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────▼──────┐
                    │    merge    │  ← Join results
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Result    │
                    └─────────────┘
```

#### Key Concepts

**Parallel Fetch Tools** (`tools/fetch.py`):
```python
@tool(desc="Fetch from primary source", tags=["fetch", "parallel"])
async def fetch_primary(args: FetchArgs, ctx: ToolContext) -> FetchResult:
    # Fetches run in parallel
    return FetchResult(source="primary", data=await fetch_source_a(args.query))

@tool(desc="Fetch from secondary source", tags=["fetch", "parallel"])
async def fetch_secondary(args: FetchArgs, ctx: ToolContext) -> FetchResult:
    return FetchResult(source="secondary", data=await fetch_source_b(args.query))
```

**Merge Tool** (`tools/merge.py`):
```python
@tool(desc="Merge parallel results", tags=["merge"])
async def merge_results(args: MergeArgs, ctx: ToolContext) -> MergeResult:
    # Receives results from parallel execution via join injection
    parallel_results = ctx.tool_context.get("parallel_results", [])

    combined = combine_results(parallel_results)
    return MergeResult(combined=combined)
```

---

## Tier 2: Service Templates

### `lighthouse` Template

**Best for**: RAG (Retrieval-Augmented Generation) applications using Lighthouse API.

```bash
penguiflow new my-rag --template=lighthouse
```

#### What You Get

```
my-rag/
├── src/my_rag/
│   ├── orchestrator.py
│   ├── planner.py
│   ├── tools/
│   │   └── rag.py           # Lighthouse API tools
│   └── clients/
│       ├── memory.py
│       └── lighthouse.py    # Lighthouse client stub
└── ...
```

#### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      ReactPlanner                             │
│                                                               │
│   "Upload this PDF and find vacation policy"                  │
│                           │                                   │
│                           ▼                                   │
│   ┌────────┐    ┌────────┐    ┌────────┐    ┌────────┐       │
│   │ upload │ → │ ingest │ → │  poll  │ → │ query  │        │
│   │ files  │    │  job   │    │ status │    │  RAG   │        │
│   └────────┘    └────────┘    └────────┘    └────────┘        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

#### Lighthouse Tools

| Tool | Lighthouse Endpoint | Description |
|------|---------------------|-------------|
| `upload_files` | `POST /v1/files` | Upload documents |
| `ingest` | `POST /v1/ingest` | Trigger indexing |
| `poll_status` | `GET /v1/ingest/{job_id}` | Check ingestion status |
| `query` | `POST /v1/query` | RAG query with citations |

#### Example Flow

```python
# LLM reasoning trace:
# 1. "User wants to search vacation policy, but first needs to upload the PDF"
# 2. Call upload_files(files=["handbook.pdf"])
# 3. Call ingest(file_ids=["uuid-123"])
# 4. Call poll_status(job_id="job-456") → "processing"
# 5. Call poll_status(job_id="job-456") → "completed"
# 6. Call query(query="What is the vacation policy?")
# 7. Return answer with citations
```

---

### `wayfinder` Template

**Best for**: Natural Language Query to SQL applications using Wayfinder API.

```bash
penguiflow new my-nlq --template=wayfinder
```

#### What You Get

```
my-nlq/
├── src/my_nlq/
│   ├── orchestrator.py
│   ├── planner.py
│   ├── tools/
│   │   └── nlq.py           # Wayfinder API tools
│   └── clients/
│       ├── memory.py
│       └── wayfinder.py     # Wayfinder client stub
└── ...
```

#### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      ReactPlanner                             │
│                                                               │
│   "Show me customers who churned last quarter"                │
│                           │                                   │
│                           ▼                                   │
│   ┌───────────┐    ┌───────────┐    ┌───────────┐            │
│   │ preflight │ → │   plan    │ → │  execute  │             │
│   │  (fast)   │    │  query    │    │   query   │             │
│   └───────────┘    └─────┬─────┘    └───────────┘            │
│                          │                                    │
│                          ▼ (if ambiguous)                     │
│                    ┌───────────┐                              │
│                    │  clarify  │ → User provides input        │
│                    └───────────┘                              │
└──────────────────────────────────────────────────────────────┘
```

#### Wayfinder Tools

| Tool | Wayfinder Endpoint | Description |
|------|---------------------|-------------|
| `preflight` | `POST /v1/nlq/preflight` | Fast confidence check |
| `plan_query` | `POST /v1/nlq/agent-query` | Generate SQL plan |
| `execute_query` | `POST /v1/nlq/agent-query:execute` | Run SQL |
| `refine_query` | `POST /v1/nlq/agent-query:refine` | Refine with clarifications |

#### Handling Clarifications

```python
@tool(desc="Plan SQL query from natural language", tags=["nlq"])
async def plan_query(args: PlanQueryArgs, ctx: ToolContext) -> PlanQueryResult:
    publisher = ctx.tool_context.get("status_publisher")

    result = await wayfinder.plan_query(query=args.query)

    # Handle action_recommendation
    if result.action_recommendation == "clarify":
        if publisher:
            publisher(StatusUpdate(
                status="paused",
                message=f"Need clarification: {result.suggested_clarifications}"
            ))
        # Return clarification request for HITL flow
        return PlanQueryResult(
            needs_clarification=True,
            clarifications=result.suggested_clarifications,
        )

    return PlanQueryResult(sql=result.sql, confidence=result.confidence.overall)
```

---

### `analyst` Template

**Best for**: Remote analysis agents callable by other agents via A2A.

```bash
penguiflow new my-analyst --template=analyst
```

#### What You Get

```
my-analyst/
├── src/my_analyst/
│   ├── orchestrator.py
│   ├── planner.py
│   ├── a2a.py               # A2A server setup
│   ├── tools/
│   │   └── analysis.py      # Analysis tools
│   └── clients/
│       ├── memory.py
│       └── analyst.py       # Self-reference client
└── ...
```

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Analyst Agent                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   A2A Server                         │    │
│  │   Exposes: analyze_code, analyze_data, summarize    │    │
│  └─────────────────────────────────────────────────────┘    │
│                           │                                  │
│                           ▼                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  ReactPlanner                        │    │
│  │   Tools: code_analysis, data_analysis, summarize    │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                            │
              Called by other agents via A2A
                            │
┌───────────────────────────┼───────────────────────────┐
│ Enterprise Agent          │          RAG Agent        │
│ (calls analyst for        │    (calls analyst for     │
│  code review)             │     data insights)        │
└───────────────────────────┴───────────────────────────┘
```

#### Analysis Tools

```python
@tool(desc="Analyze code quality and patterns", tags=["analysis"])
async def code_analysis(args: CodeAnalysisArgs, ctx: ToolContext) -> CodeAnalysisResult:
    # Analyze repository, file, or snippet
    return CodeAnalysisResult(
        summary="Analysis complete",
        issues=[...],
        recommendations=[...],
    )

@tool(desc="Analyze data patterns and anomalies", tags=["analysis"])
async def data_analysis(args: DataAnalysisArgs, ctx: ToolContext) -> DataAnalysisResult:
    # Statistical analysis, trend detection, etc.
    return DataAnalysisResult(
        insights=[...],
        visualizations=[...],
    )
```

---

## Tier 3: Enterprise Template

**Best for**: Production-grade platforms with full observability, resilience, and service integration.

```bash
penguiflow new my-platform --template=enterprise
```

#### What You Get

```
my-platform/
├── src/my_platform/
│   ├── __main__.py
│   ├── config.py            # Full production config
│   ├── orchestrator.py      # Production orchestrator
│   ├── planner.py           # Full planner setup
│   ├── models.py
│   ├── telemetry.py         # Full observability
│   ├── resilience.py        # Circuit breakers, retries
│   ├── tools/
│   │   ├── diagnostics.py   # System diagnostics
│   │   ├── validate.py      # Validation tools
│   │   └── resolve.py       # Resolution tools
│   ├── clients/
│   │   ├── memory.py
│   │   └── registry.py      # Service registry client
│   ├── server/
│   │   ├── __init__.py
│   │   └── handlers.py      # HTTP/A2A handlers
│   └── a2a.py
├── tests/
│   ├── test_orchestrator.py
│   ├── test_tools.py
│   ├── test_telemetry.py
│   ├── test_resilience.py
│   ├── test_server.py
│   ├── test_registry.py
│   └── test_validation.py
└── ...
```

#### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Enterprise Agent                              │
│                                                                      │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐         │
│  │  HTTP Server   │  │  A2A Server    │  │  Health Check  │         │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘         │
│          │                   │                   │                   │
│          └───────────────────┼───────────────────┘                   │
│                              │                                       │
│  ┌───────────────────────────▼───────────────────────────────┐      │
│  │                     Orchestrator                           │      │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │      │
│  │  │ Resilience  │  │  Telemetry  │  │   Memory    │        │      │
│  │  │  (retry,    │  │  (metrics,  │  │  (context,  │        │      │
│  │  │  breaker)   │  │   tracing)  │  │  retrieval) │        │      │
│  │  └─────────────┘  └─────────────┘  └─────────────┘        │      │
│  └───────────────────────────┬───────────────────────────────┘      │
│                              │                                       │
│  ┌───────────────────────────▼───────────────────────────────┐      │
│  │                     ReactPlanner                           │      │
│  │                                                            │      │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐           │      │
│  │  │ diagnose   │  │  validate  │  │  resolve   │           │      │
│  │  └────────────┘  └────────────┘  └────────────┘           │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### Key Features

**Resilience** (`resilience.py`):
```python
from penguiflow.policies import CircuitBreaker, RetryPolicy

class ResilientOrchestrator:
    def __init__(self, config: Config):
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
        )
        self._retry_policy = RetryPolicy(
            max_attempts=3,
            backoff_factor=2.0,
        )

    async def execute_with_resilience(self, ...):
        async with self._circuit_breaker:
            return await self._retry_policy.execute(
                self._orchestrator.execute, ...
            )
```

**Service Registry** (`clients/registry.py`):
```python
class ServiceRegistry:
    """Dynamic service discovery for microservice architecture."""

    async def get_service(self, name: str) -> ServiceEndpoint:
        # Discover service endpoint
        return await self._discover(name)

    async def register(self, name: str, endpoint: str) -> None:
        # Register this agent as a service
        await self._register(name, endpoint)
```

**Full Telemetry** (`telemetry.py`):
```python
class EnterpriseTelemetry:
    """Production-grade observability."""

    def __init__(self, config: Config):
        self._metrics = MetricsCollector(config.metrics_backend)
        self._tracer = Tracer(config.tracing_backend)

    async def record_planner_event(self, event: FlowEvent) -> FlowEvent:
        # Record metrics
        self._metrics.increment(f"node.{event.event_type}")

        # Add trace span
        with self._tracer.span(f"node.{event.node_name}"):
            # Full error extraction
            if event.event_type == "node_error":
                self._extract_and_log_error(event)

        return event
```

---

## Bonus: Additional Templates

### `flow` Template

**Best for**: Simple linear pipelines, graph-based processing without LLM planning.

```bash
penguiflow new my-flow --template=flow
```

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PenguiFlow Graph                          │
│                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│   │  input  │ → │ process │ → │ output  │                  │
│   │  node   │    │  node   │    │  node   │                  │
│   └─────────┘    └─────────┘    └─────────┘                  │
│                                                              │
│   No LLM - direct node-to-node message passing               │
└─────────────────────────────────────────────────────────────┘
```

#### Use Cases

- ETL pipelines
- Data transformation flows
- Deterministic processing
- When LLM reasoning isn't needed
- When using a triage node that passes the execution graph to a subflow.

---

### `controller` Template

**Best for**: Iterative refinement loops, multi-pass processing.

```bash
penguiflow new my-controller --template=controller
```

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Controller Loop                           │
│                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│   │  start  │ → │ iterate │ → │  check  │                  │
│   └─────────┘    └────┬────┘    └────┬────┘                  │
│                       │              │                       │
│                       │    ┌─────────┘                       │
│                       │    │ (if not done)                   │
│                       │    ▼                                 │
│                       └────┤                                 │
│                            │ (loop until max_iterations      │
│                            │  or convergence)                │
│                            ▼                                 │
│                    ┌─────────┐                               │
│                    │  done   │                               │
│                    └─────────┘                               │
└─────────────────────────────────────────────────────────────┘
```

#### Use Cases

- Iterative refinement (draft → review → revise)
- Convergence algorithms
- Multi-pass analysis
- Quality improvement loops

---

## Enhancement Flags

Enhancement flags add capabilities to **any template**. Combine freely:

```bash
# Add streaming to react
penguiflow new my-agent --template=react --with-streaming

# Add HITL + A2A to enterprise
penguiflow new my-agent --template=enterprise --with-hitl --with-a2a

# Lighthouse without memory
penguiflow new my-rag --template=lighthouse --no-memory

# Everything
penguiflow new my-agent --template=react --with-streaming --with-hitl --with-a2a
```

---

### `--with-streaming`

**Adds**: Real-time token streaming and status updates.

```bash
penguiflow new my-agent --with-streaming
```

#### What It Adds

```python
# In orchestrator.py
from penguiflow.streaming import StreamChunk

# Status publisher pattern
async def execute(self, query: str, ...) -> AgentResponse:
    # Publish status updates
    self._telemetry.publish_status(StatusUpdate(
        status="thinking",
        message="Planning response..."
    ))

    # Stream chunks as they arrive
    async for chunk in self._planner.run_streaming(...):
        if isinstance(chunk, StreamChunk):
            yield chunk  # Forward to client
```

```python
# In telemetry.py
@dataclass
class StatusUpdate:
    status: str      # "thinking", "ok", "error", "paused"
    message: str
    timestamp: datetime

class AgentTelemetry:
    def publish_status(self, update: StatusUpdate) -> None:
        # Callback to SSE/WebSocket handler
        if self._status_callback:
            self._status_callback(update)
```

#### Use Cases

- Chat UIs with typing indicators
- Progress bars for long operations
- Real-time agent thought process display
- SSE/WebSocket integrations

---

### `--with-hitl`

**Adds**: Human-in-the-loop approval flows with pause/resume.

```bash
penguiflow new my-agent --with-hitl
```

#### What It Adds

```python
# In orchestrator.py
from penguiflow.planner import PlannerPause

async def execute(self, query: str, ...) -> AgentResponse | PauseRequest:
    result = await self._planner.run(...)

    # Check if planner needs human approval
    if isinstance(result, PlannerPause):
        return PauseRequest(
            pause_token=result.token,
            reason=result.reason,
            proposed_action=result.proposed_action,
        )

    return AgentResponse(...)

async def resume(self, pause_token: str, approved: bool) -> AgentResponse:
    """Resume after human decision."""
    result = await self._planner.resume(
        token=pause_token,
        approved=approved,
    )
    return AgentResponse(...)
```

```python
# In tools - request approval
@tool(desc="Execute dangerous operation", tags=["sensitive"])
async def dangerous_operation(args: Args, ctx: ToolContext) -> Result:
    # Request human approval before proceeding
    if args.requires_approval:
        raise PlannerPause(
            reason="This will delete production data",
            proposed_action=f"DELETE FROM {args.table}",
        )

    return await execute_operation(args)
```

#### Use Cases

- High-stakes operations (deletions, payments)
- Compliance/audit workflows
- Multi-step approvals
- Agent supervision

---

### `--with-a2a`

**Adds**: Agent-to-Agent communication server.

```bash
penguiflow new my-agent --with-a2a
```

#### What It Adds

```python
# In a2a.py
from penguiflow.remote import A2AServer, RemoteCapability

class MyAgentA2AServer:
    """Expose agent as A2A-callable service."""

    def __init__(self, orchestrator: MyAgentOrchestrator):
        self._orchestrator = orchestrator
        self._server = A2AServer(
            capabilities=[
                RemoteCapability(
                    name="process_query",
                    description="Process user query with full context",
                    input_schema=QueryInput.model_json_schema(),
                    output_schema=QueryOutput.model_json_schema(),
                ),
            ]
        )

    async def handle_request(self, request: A2ARequest) -> A2AResponse:
        result = await self._orchestrator.execute(
            query=request.input.query,
            tenant_id=request.context.tenant_id,
            ...
        )
        return A2AResponse(output=result)
```

```python
# In __main__.py - start A2A server
async def main():
    orchestrator = MyAgentOrchestrator(config)
    a2a_server = MyAgentA2AServer(orchestrator)

    # Start both HTTP API and A2A server
    await asyncio.gather(
        start_http_server(orchestrator),
        a2a_server.start(port=config.a2a_port),
    )
```

#### Use Cases

- Microservice agent architecture
- Agent composition (agents calling agents)
- Distributed agent systems
- Service mesh integration

---

### `--no-memory`

**Removes**: Memory Server (Iceberg) integration.

```bash
penguiflow new my-agent --no-memory
```

#### What It Changes

```python
# WITHOUT --no-memory (default):
async def execute(self, query: str, ...) -> AgentResponse:
    # Load memory context
    conscious = await self._memory.start_session(...)
    retrieval = await self._memory.auto_retrieve(...)

    llm_context = {
        "conscious_memories": conscious.get("conscious", []),
        "retrieved_memories": retrieval.get("snippets", []),
    }

    result = await self._planner.run(query=query, llm_context=llm_context, ...)

    # Store interaction
    await self._memory.ingest_interaction(...)

# WITH --no-memory:
async def execute(self, query: str, ...) -> AgentResponse:
    # No memory integration
    result = await self._planner.run(query=query, llm_context={}, ...)
```

#### Use Cases

- Stateless agents
- Testing without memory dependency
- Simple single-turn interactions
- External memory management

---

## Project Structure

All templates follow a consistent structure:

```
my-agent/
├── src/
│   └── my_agent/
│       ├── __init__.py          # Package init
│       ├── __main__.py          # Entry point: python -m my_agent
│       ├── config.py            # Environment configuration
│       ├── orchestrator.py      # Main orchestrator class
│       ├── planner.py           # Planner/catalog setup (if applicable)
│       ├── models.py            # Shared Pydantic models
│       ├── telemetry.py         # Observability middleware
│       ├── a2a.py               # A2A server (if --with-a2a)
│       ├── tools/               # Tool definitions
│       │   ├── __init__.py
│       │   └── *.py
│       └── clients/             # External service clients
│           ├── __init__.py
│           ├── memory.py        # Memory Server stub
│           └── *.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Pytest fixtures
│   ├── test_orchestrator.py
│   └── test_tools/
├── pyproject.toml               # Dependencies and metadata
├── .env.example                 # Environment template
├── .gitignore
├── .vscode/                     # VS Code settings
│   ├── settings.json
│   ├── launch.json
│   └── tasks.json
└── README.md
```

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Required: LLM Provider
LLM_MODEL=openrouter/openai/gpt-4o              # Or: anthropic/claude-3-5-sonnet
OPENROUTER_API_KEY=sk-or-v1-...                  # Or: ANTHROPIC_API_KEY, OPENAI_API_KEY

# Planner Settings
LLM_TEMPERATURE=0.3                              # 0.0 = deterministic
LLM_MAX_RETRIES=3                                # Retry on transient failures
LLM_TIMEOUT_S=60.0                               # Per-call timeout
PLANNER_MAX_ITERS=12                             # Max planning iterations
PLANNER_TOKEN_BUDGET=8000                        # Trajectory compression budget

# Memory Server (if enabled)
MEMORY_BASE_URL=http://localhost:8000

# Observability
LOG_LEVEL=INFO                                   # DEBUG, INFO, WARNING, ERROR
ENABLE_TELEMETRY=true
TELEMETRY_BACKEND=logging                        # logging, mlflow

# Application
AGENT_ENVIRONMENT=development                    # development, staging, production
AGENT_NAME=my_agent
```

### Config Class

All templates include a `config.py` with type-safe configuration:

```python
from dataclasses import dataclass
import os

@dataclass
class Config:
    """Agent configuration loaded from environment."""

    llm_model: str
    llm_temperature: float
    llm_max_retries: int
    llm_timeout_s: float
    planner_max_iters: int
    planner_token_budget: int
    memory_base_url: str
    log_level: str
    agent_name: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            llm_model=os.getenv("LLM_MODEL", "openrouter/openai/gpt-4o"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60.0")),
            planner_max_iters=int(os.getenv("PLANNER_MAX_ITERS", "12")),
            planner_token_budget=int(os.getenv("PLANNER_TOKEN_BUDGET", "8000")),
            memory_base_url=os.getenv("MEMORY_BASE_URL", "http://localhost:8000"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            agent_name=os.getenv("AGENT_NAME", "my_agent"),
        )
```

---

## Running Your Agent

### Development

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run the agent
uv run python -m my_agent
```

### With Hot Reload (Development)

```bash
# Install watchfiles
uv pip install watchfiles

# Run with auto-reload
watchfiles "uv run python -m my_agent" src/
```

### Production

```bash
# Install production dependencies only
uv sync --no-dev

# Run with production settings
AGENT_ENVIRONMENT=production \
LOG_LEVEL=INFO \
uv run python -m my_agent
```

### Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
RUN uv sync --no-dev

# Run
CMD ["uv", "run", "python", "-m", "my_agent"]
```

---

## Testing

### Run Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=my_agent --cov-report=term-missing

# Single test file
uv run pytest tests/test_orchestrator.py

# Single test
uv run pytest tests/test_tools.py -k "test_search_basic"
```

### Test Patterns

**Unit Tests** (test tools in isolation):

```python
# tests/test_tools/test_search.py
import pytest
from my_agent.tools.search import search, SearchArgs, SearchResult

class TestSearchTool:
    @pytest.mark.asyncio
    async def test_search_basic(self, mock_context):
        args = SearchArgs(query="test")
        result = await search(args, mock_context)

        assert isinstance(result, SearchResult)
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_search_empty_query(self, mock_context):
        args = SearchArgs(query="")
        result = await search(args, mock_context)

        assert result.results == []
```

**Integration Tests** (test full orchestrator):

```python
# tests/test_orchestrator.py
import pytest
from my_agent.orchestrator import MyAgentOrchestrator
from my_agent.config import Config

class TestOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_basic_execution(self, config, mock_memory):
        orchestrator = MyAgentOrchestrator(config)
        orchestrator._memory = mock_memory

        response = await orchestrator.execute(
            query="Hello, world!",
            tenant_id="test-tenant",
            user_id="test-user",
            session_id="test-session",
        )

        assert response.answer is not None
        assert response.trace_id is not None

        await orchestrator.stop()
```

**Fixtures** (`tests/conftest.py`):

```python
import pytest
from my_agent.config import Config

@pytest.fixture
def config() -> Config:
    return Config(
        llm_model="test-model",
        memory_base_url="http://localhost:8000",
        ...
    )

@pytest.fixture
def mock_memory():
    class MockMemoryClient:
        async def start_session(self, **kwargs):
            return {"conscious": [], "token_estimate": 0}

        async def auto_retrieve(self, **kwargs):
            return {"snippets": [], "tokens_estimate": 0}

        async def ingest_interaction(self, **kwargs):
            return {"id": "test-id"}

    return MockMemoryClient()
```

---

## Best Practices

### 1. Tool Design

```python
# DO: Clear, specific descriptions
@tool(desc="Search product catalog by name, category, or SKU", tags=["search", "products"])
async def search_products(args: SearchProductsArgs, ctx: ToolContext) -> SearchProductsResult:
    ...

# DON'T: Vague descriptions
@tool(desc="Search stuff", tags=["search"])
async def search(args: Args, ctx: ToolContext) -> Result:
    ...
```

### 2. Error Handling

```python
# DO: Convert to domain errors
from my_agent.errors import MyAgentError

@tool(desc="Fetch user data", tags=["users"])
async def fetch_user(args: FetchUserArgs, ctx: ToolContext) -> FetchUserResult:
    try:
        user = await api.get_user(args.user_id)
        return FetchUserResult(user=user)
    except APIError as e:
        raise MyAgentError(
            code="USER_FETCH_FAILED",
            message=f"Failed to fetch user {args.user_id}: {e}",
            original=e,
        )
```

### 3. Telemetry

```python
# DO: Always attach telemetry middleware
orchestrator = MyAgentOrchestrator(config)
orchestrator._planner.event_callback = telemetry.record_planner_event

# DO: Extract full error details
async def record_planner_event(self, event: FlowEvent) -> FlowEvent:
    if event.event_type == "node_error":
        error_payload = event.error_payload or {}
        self.logger.error(
            "node_error",
            extra={
                "node": event.node_name,
                "error_class": error_payload.get("error_class"),
                "error_message": error_payload.get("error_message"),
                "error_traceback": error_payload.get("error_traceback"),
            },
        )
    return event
```

### 4. Memory Integration

```python
# DO: Follow the lifecycle
async def execute(self, query: str, ...) -> AgentResponse:
    # 1. Start session (load conscious)
    conscious = await self._memory.start_session(...)

    # 2. Retrieve relevant memories
    retrieval = await self._memory.auto_retrieve(prompt=query, ...)

    # 3. Execute with context
    result = await self._planner.run(
        llm_context={
            "conscious_memories": conscious.get("conscious", []),
            "retrieved_memories": retrieval.get("snippets", []),
        },
        ...
    )

    # 4. Ingest interaction
    await self._memory.ingest_interaction(
        user_prompt=query,
        agent_response=result.answer,
        ...
    )
```

### 5. Production Checklist

- [ ] Configure proper `LLM_MODEL` for production
- [ ] Set `LOG_LEVEL=INFO` (not DEBUG)
- [ ] Enable `TELEMETRY_BACKEND` (mlflow, logging)
- [ ] Set `PLANNER_TOKEN_BUDGET` to control costs
- [ ] Configure `LLM_MAX_RETRIES` for resilience
- [ ] Implement health checks
- [ ] Set up alerting for error rates
- [ ] Review and secure all API keys
- [ ] Test graceful shutdown (`orchestrator.stop()`)

---

## Troubleshooting

### Common Issues

**Import Error: `cannot import name 'ToolContext'`**

```bash
# Ensure you have the latest penguiflow
uv pip install --upgrade penguiflow
```

**`Jinja2 is required for penguiflow new`**

```bash
# Install CLI extras
pip install penguiflow[cli]
```

**Template project won't start**

```bash
# Check .env is configured
cat .env | grep -v "^#" | grep -v "^$"

# Ensure API key is set
echo $OPENROUTER_API_KEY  # or OPENAI_API_KEY, ANTHROPIC_API_KEY
```

**Memory client connection refused**

```bash
# Memory is stubbed by default - implement the client or use --no-memory
penguiflow new my-agent --no-memory
```

**Planner loops forever**

```bash
# Set iteration limit in .env
PLANNER_MAX_ITERS=10

# Or set deadline
PLANNER_DEADLINE_S=30.0
```

### Getting Help

- Check the [PenguiFlow documentation](../README.md)
- Review [examples/](../examples/) for working patterns
- File issues at [GitHub Issues](https://github.com/clear-tech-labs/penguiflow/issues)

---

## Quick Reference

### All Templates

| Template | Command | Best For |
|----------|---------|----------|
| minimal | `penguiflow new NAME --template=minimal` | Learning, prototypes |
| react | `penguiflow new NAME` | Standard agents (default) |
| parallel | `penguiflow new NAME --template=parallel` | Batch processing |
| flow | `penguiflow new NAME --template=flow` | Linear pipelines |
| controller | `penguiflow new NAME --template=controller` | Iterative loops |
| lighthouse | `penguiflow new NAME --template=lighthouse` | RAG applications |
| wayfinder | `penguiflow new NAME --template=wayfinder` | NLQ-to-SQL |
| analyst | `penguiflow new NAME --template=analyst` | A2A analysis service |
| enterprise | `penguiflow new NAME --template=enterprise` | Production platforms |

### All Flags

| Flag | Effect |
|------|--------|
| `--with-streaming` | Add real-time streaming + status updates |
| `--with-hitl` | Add human-in-the-loop pause/resume |
| `--with-a2a` | Add Agent-to-Agent server |
| `--no-memory` | Remove Memory Server integration |
| `--force` | Overwrite existing files |
| `--dry-run` | Preview without creating files |

### Common Combinations

```bash
# Standard agent with streaming
penguiflow new my-agent --with-streaming

# RAG with human approval for sensitive queries
penguiflow new my-rag --template=lighthouse --with-hitl

# Stateless NLQ service
penguiflow new my-nlq --template=wayfinder --no-memory

# Full enterprise with everything
penguiflow new my-platform --template=enterprise --with-streaming --with-hitl --with-a2a
```

---

**Happy building!** 🐧
