# PenguiFlow Templating Quickguide

> **Version**: 2.8 | **Last Updated**: December 2025
>
> **v2.8 Features**: Artifact Store for binary content (PDFs, images, large files) with spec-driven configuration. Artifacts are accessible via the Playground UI and REST API.
>
> **v2.7 Features**: Interactive Playground (`penguiflow dev`), External Tool Integration (ToolNode for MCP/UTCP/HTTP), Short-Term Memory with multi-tenant isolation. Planner now returns structured `FinalPayload` with `raw_answer` field. See [Extracting Answers from Payload](#4-extracting-answers-from-payload) for the recommended pattern.

The PenguiFlow CLI scaffolds production-ready agent projects with best practices baked in. This guide covers every template, flag, and pattern you need to ship agents fast.

**Two ways to create agents:**
- `penguiflow new` — Interactive scaffolding with templates
- `penguiflow generate` — Declarative YAML spec → generated project (v2.6+)

> **Recommendation (Jan 2026)**: Prefer the **Spec engine** (`penguiflow generate`) for downstream teams.
> It’s the most stable interface for reproducible scaffolding, feature flags, and future migrations.

> **Important (Jan 2026)**: New projects scaffold with a **Stub/Scripted LLM** by default so tests pass out-of-the-box.
> To run a real model, you must switch the planner to either:
> - **LiteLLM** (`llm="openrouter/..."`, `llm="openai/..."`, etc.), or
> - **Native LLM layer** (`llm="openrouter/..."`, `use_native_llm=True`) for direct provider adapters.
>
> See [LLM Setup: Stub → Real Model](#llm-setup-stub--real-model) for the exact edit.

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
   - [rag_server](#rag_server-template)
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
   - [--with-rich-output](#--with-rich-output)
   - [--with-background-tasks](#--with-background-tasks)
   - [--no-memory](#--no-memory)
9. [External Tool Integration (ToolNode v2)](#external-tool-integration-toolnode-v2)
   - [Overview](#overview)
   - [Quick Start with Presets](#quick-start-with-presets)
   - [Available Presets](#available-presets)
   - [Custom Configuration](#custom-configuration)
   - [Production Considerations](#production-considerations)
   - [OAuth Integration](#oauth-integration)
   - [Documentation Links](#documentation-links)
10. [Agent Spec & Generator (v2.6+)](#agent-spec--generator-v26)
   - [Spec Format](#spec-format)
   - [Generator Command](#generator-command)
   - [Example Spec](#example-spec)
   - [Tested Template Examples](#tested-template-examples)
   - [Memory Configuration](#memory-configuration)
   - [Artifact Store Configuration](#artifact-store-configuration)
11. [Project Structure](#project-structure)
12. [Configuration](#configuration)
13. [Running Your Agent](#running-your-agent)
14. [Testing](#testing)
15. [Best Practices](#best-practices)
16. [Troubleshooting](#troubleshooting)

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
cp .env.example .env
uv run python -m my_agent
```

**Note**: This runs with a Stub/Scripted LLM by default (deterministic, no network calls). For a real model, follow the next section.

---

## LLM Setup: Stub → Real Model

Scaffolds intentionally use a deterministic stub LLM so:
- `uv run python -m <package>` works immediately
- tests can run without provider credentials

To use a real LLM, edit `src/<your_package>/planner.py` and switch to one of the supported backends below.

### Option A: LiteLLM (recommended for broad provider parity)

1. Edit `src/<your_package>/planner.py`:
   - Remove `llm_client=ScriptedLLM(...)`
   - Set `llm=config.llm_model`

2. Set `LLM_MODEL` to a LiteLLM model string in your `.env` (examples):
   - `LLM_MODEL=openrouter/anthropic/claude-3-5-sonnet`
   - `LLM_MODEL=openai/gpt-4o-mini`

3. Provide the corresponding provider credentials in your environment (LiteLLM conventions).

### Option B: Native LLM Layer (direct provider adapters)

If you want to bypass LiteLLM and use PenguiFlow’s native providers:

1. Edit `src/<your_package>/planner.py`:
   - Set `llm=config.llm_model`
   - Add `use_native_llm=True`

2. Set provider env vars (provider-specific). Example families:
   - OpenRouter: `OPENROUTER_API_KEY`
   - Databricks: `DATABRICKS_HOST`, `DATABRICKS_TOKEN`

3. If you want final-answer token streaming, set `stream_final_response=True` on `ReactPlanner` (see [--with-streaming](#--with-streaming)).

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
| **Tier 2** | `rag_server`, `wayfinder`, `analyst` | Internal service integrations |
| **Tier 3** | `enterprise` | Production-grade full stack |
| **Bonus** | `flow`, `controller` | Alternative architectural patterns |
| **Flags** | `--with-streaming`, `--with-hitl`, `--with-a2a`, `--with-rich-output`, `--with-background-tasks`, `--no-memory` | Add capabilities to any template |

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
├─ RAG Application → RAG Service
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

### `rag_server` Template

**Best for**: RAG (Retrieval-Augmented Generation) applications using a dedicated RAG server API.

```bash
penguiflow new my-rag --template=rag_server
```

#### What You Get

```
my-rag/
├── src/my_rag/
│   ├── orchestrator.py
│   ├── planner.py
│   ├── tools/
│   │   └── rag.py           # RAG server tools
│   └── clients/
│       ├── memory.py
│       └── rag_server.py    # RAG server client stub
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

#### RAG Tools

| Tool | RAG Endpoint | Description |
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

# Add rich output UI components
penguiflow new my-agent --template=react --with-rich-output

# Add background tasks / subagent orchestration
penguiflow new my-agent --template=enterprise --with-background-tasks

# RAG without memory
penguiflow new my-rag --template=rag_server --no-memory

# Everything
penguiflow new my-agent --template=react --with-streaming --with-hitl --with-a2a --with-rich-output --with-background-tasks
```

---

### `--with-streaming`

**Adds**: Tool-level streaming capture + optional final-answer token streaming hooks.

```bash
penguiflow new my-agent --with-streaming
```

#### What It Adds

**1) Tool streaming via `ctx.emit_chunk()`**

Tools can emit incremental progress (useful for long-running calls):

```python
await ctx.emit_chunk(stream_id="search", seq=0, text="Starting search…")
...
await ctx.emit_chunk(stream_id="search", seq=1, text="Done", done=True)
```

Those chunks are captured into `PlannerFinish.metadata["steps"][...]["streams"]` for you to surface in your API/UI.

**2) Optional LLM final-answer streaming via planner events**

If you construct `ReactPlanner(..., stream_final_response=True, event_callback=...)`, the planner emits `llm_stream_chunk` events during the terminal answer generation/revision.

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
- Real-time “final answer” token streaming (via event callback)
- SSE/WebSocket integrations (you forward tool chunks + LLM chunks)

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
            pause_token=result.resume_token,
            reason=result.reason,
            payload=dict(result.payload),
        )

    return AgentResponse(...)

async def resume(self, pause_token: str, user_input: str) -> AgentResponse:
    """Resume after user input/approval."""
    result = await self._planner.resume(
        token=pause_token,
        user_input=user_input,
    )
    return AgentResponse(...)
```

```python
# In tools - request approval
@tool(desc="Execute dangerous operation", tags=["sensitive"])
async def dangerous_operation(args: Args, ctx: ToolContext) -> Result:
    # Request human approval before proceeding
    if args.requires_approval:
        await ctx.pause(
            "approval_required",
            {
                "reason": "This will delete production data",
                "proposed_action": f"DELETE FROM {args.table}",
            },
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

**Adds**: A minimal A2A scaffold (placeholder).

> **Status (Jan 2026)**: The A2A scaffold is **being re-worked** and is currently **out of spec** for production use.
> Treat `--with-a2a` as a stub to help teams align on project structure only.

```bash
penguiflow new my-agent --with-a2a
```

#### What It Adds

```python
# In a2a.py
class A2AServer:
    """Placeholder A2A server for remote agent communication."""
    ...
```

#### Use Cases

- Project scaffolding for teams planning A2A adoption
- Not production-ready as of Jan 2026

---

### `--with-rich-output`

**Adds**: Rich output tooling for UI components (charts, reports, grids) as planner tools.

This flag wires in the `penguiflow.rich_output` runtime and nodes and adds configuration toggles for:
- Allowlisting component types
- Payload size limits
- Optional prompt catalog / examples

Use this when you want the model to return structured UI artifacts (and keep heavy payloads out of the main LLM context).

---

### `--with-background-tasks`

**Adds**: Background task/subagent scaffolding (session manager, task service, task telemetry).

This is used to support patterns like:
- “Spawn 3 subagents to research in parallel, then merge”
- long-running background jobs
- user-gated result merges (HUMAN_GATED)

It also enables additional planner prompt guidance and validation, plus runtime limits (max concurrent tasks, timeouts, etc.).

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

## External Tool Integration (ToolNode v2)

**New in v2.6+**: Seamlessly integrate external tools into your agents without writing wrapper code.

ToolNode v2 enables zero-wrapper integration with external tools and APIs through two transport types:
- **MCP (Model Context Protocol)**: Integration with FastMCP servers (via stdio or SSE/HTTP)
- **UTCP (Universal Tool Communication Protocol)**: Direct HTTP API integration

This allows you to add capabilities like GitHub operations, filesystem access, database queries, and cloud APIs to your agents with minimal configuration.

---

### Overview

ToolNode v2 provides a unified interface for external tools:

- **Zero-wrapper integration**: No need to write Python wrappers for external tools
- **Popular presets**: Pre-configured MCP servers for GitHub, Slack, PostgreSQL, etc.
- **Custom configuration**: Define your own MCP or HTTP-based tool integrations
- **Automatic discovery**: Tools are automatically discovered and added to the planner catalog
- **Type safety**: Full Pydantic validation for tool inputs and outputs
- **Auth support**: Built-in support for API keys, Bearer tokens, and OAuth2

Two transport types:

| Transport | Use Case | Example |
|-----------|----------|---------|
| **MCP** | FastMCP servers, stdio commands | GitHub CLI via `@modelcontextprotocol/server-github` |
| **UTCP** | Direct HTTP APIs | REST APIs, custom endpoints |

---

### Quick Start with Presets

The fastest way to add external tools is using popular MCP server presets:

```python
from penguiflow.tools import ToolNode, get_preset, POPULAR_MCP_SERVERS
from penguiflow.registry import ModelRegistry

# Initialize registry
registry = ModelRegistry()

# Get GitHub MCP server preset
github = ToolNode(config=get_preset("github"), registry=registry)
await github.connect()

# Get all available tools
github_tools = github.get_tools()

# Add to your planner catalog
from penguiflow.planner import build_planner

catalog = [
    *native_tools,      # Your Python tools
    *github_tools,      # External GitHub tools
]

planner = build_planner(registry=registry, catalog=catalog, ...)
```

**That's it!** Your agent now has access to GitHub operations (create issues, PRs, search code, etc.) without writing any wrapper code.

---

### Available Presets

| Preset Name | MCP Server Package | Auth Type | Capabilities |
|-------------|-------------------|-----------|-------------|
| `github` | `@modelcontextprotocol/server-github` | API_KEY | Create issues/PRs, search code, manage repos |
| `filesystem` | `@modelcontextprotocol/server-filesystem` | NONE | Read/write files, list directories |
| `postgres` | `@modelcontextprotocol/server-postgres` | API_KEY | Query databases, execute SQL |
| `slack` | `@modelcontextprotocol/server-slack` | BEARER | Send messages, read channels |
| `google-drive` | `@modelcontextprotocol/server-google-drive` | OAUTH2_USER | Read/write documents, manage files |

**View all presets:**

```python
from penguiflow.tools import POPULAR_MCP_SERVERS

for name, config in POPULAR_MCP_SERVERS.items():
    print(f"{name}: {config.description}")
```

**Environment setup for presets:**

```bash
# GitHub preset requires GITHUB_PERSONAL_ACCESS_TOKEN
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx...

# PostgreSQL preset requires DATABASE_URL
export DATABASE_URL=postgresql://user:pass@localhost:5432/db

# Slack preset requires SLACK_BOT_TOKEN
export SLACK_BOT_TOKEN=xoxb-xxx...
```

---

### Custom Configuration

For custom MCP servers or direct HTTP APIs, define an `ExternalToolConfig`:

#### MCP Transport (stdio command)

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, AuthType
from penguiflow.registry import ModelRegistry

registry = ModelRegistry()

# Custom MCP server via stdio
custom_mcp = ToolNode(
    config=ExternalToolConfig(
        name="my-custom-mcp",
        description="Custom MCP integration",
        transport_type="mcp",
        command="npx -y @my-org/custom-mcp-server",
        auth_type=AuthType.API_KEY,
        env_vars={
            "CUSTOM_API_KEY": "your-key-here",
        },
    ),
    registry=registry,
)

await custom_mcp.connect()
tools = custom_mcp.get_tools()
```

#### UTCP Transport (HTTP API with manual URL)

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, AuthType

# Direct HTTP API integration
weather_api = ToolNode(
    config=ExternalToolConfig(
        name="weather-api",
        description="Weather data API",
        transport_type="utcp",
        base_url="https://api.weather.com/v1",
        auth_type=AuthType.API_KEY,
        auth_config={
            "header_name": "X-API-Key",
            "env_var": "WEATHER_API_KEY",
        },
        endpoints=[
            {
                "name": "get_current_weather",
                "method": "GET",
                "path": "/weather/current",
                "description": "Get current weather for a location",
                "params": {
                    "location": "str",
                    "units": "Optional[str]",
                },
            },
        ],
    ),
    registry=registry,
)

await weather_api.connect()
tools = weather_api.get_tools()
```

**Supported auth types:**

```python
from penguiflow.tools import AuthType

AuthType.NONE           # No authentication
AuthType.API_KEY        # API key in header or query param
AuthType.BEARER         # Bearer token in Authorization header
AuthType.OAUTH2_USER    # User-level OAuth2 (requires HITL)
```

---

### Production Considerations

**Important notes for production deployments:**

1. **Presets use `npx -y` which requires Node.js runtime**
   - Default presets run MCP servers via `npx -y <package>`
   - This requires Node.js to be installed in your container

2. **For production containers, consider these alternatives:**

   **Option 1: Run MCP servers as sidecars with SSE/HTTP**
   ```yaml
   # docker-compose.yml
   services:
     agent:
       image: my-agent
       environment:
         GITHUB_MCP_URL: http://mcp-github:3000

     mcp-github:
       image: modelcontextprotocol/server-github
       ports:
         - "3000:3000"
       environment:
         GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_TOKEN}
   ```

   ```python
   # Connect to sidecar MCP server via HTTP
   github = ToolNode(
       config=ExternalToolConfig(
           name="github",
           transport_type="mcp",
           url="http://mcp-github:3000",  # SSE/HTTP transport
           auth_type=AuthType.API_KEY,
       ),
       registry=registry,
   )
   ```

   **Option 2: Use UTCP for direct API access**
   ```python
   # Skip MCP layer, call GitHub API directly
   github = ToolNode(
       config=ExternalToolConfig(
           name="github-api",
           transport_type="utcp",
           base_url="https://api.github.com",
           auth_type=AuthType.BEARER,
           auth_config={
               "env_var": "GITHUB_TOKEN",
           },
       ),
       registry=registry,
   )
   ```

   **Option 3: Include Node.js in your container**
   ```dockerfile
   FROM python:3.12-slim

   # Install Node.js for MCP presets
   RUN apt-get update && apt-get install -y nodejs npm

   # ... rest of Dockerfile
   ```

3. **Performance considerations:**
   - MCP stdio servers have process startup overhead (~100-500ms)
   - For high-throughput scenarios, use HTTP-based MCP or UTCP
   - Consider connection pooling for UTCP integrations

4. **Security best practices:**
   - Never hardcode API keys in `ExternalToolConfig`
   - Use environment variables via `env_vars` or `auth_config.env_var`
   - For OAuth2, use user-level auth with HITL (see below)
   - Review MCP server packages before deploying to production

---

### OAuth Integration

For tools requiring user-level OAuth (e.g., Google Drive, Microsoft Graph):

```python
from penguiflow.tools import ToolNode, OAuthManager, OAuthProviderConfig, AuthType
from penguiflow.planner import PlannerPause

# Configure OAuth providers
oauth_manager = OAuthManager(
    providers={
        "google": OAuthProviderConfig(
            client_id="your-client-id",
            client_secret="your-client-secret",
            authorization_url="https://accounts.google.com/o/oauth2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        ),
    }
)

# Create ToolNode with OAuth
google_drive = ToolNode(
    config=ExternalToolConfig(
        name="google-drive",
        transport_type="mcp",
        command="npx -y @modelcontextprotocol/server-google-drive",
        auth_type=AuthType.OAUTH2_USER,
    ),
    registry=registry,
    auth_manager=oauth_manager,
)

await google_drive.connect()
```

**HITL flow for OAuth:**

```python
# In your orchestrator
async def execute(self, query: str, user_id: str, ...) -> AgentResponse | PauseRequest:
    result = await self._planner.run(...)

    # Check if OAuth is needed
    # Note: PenguiFlow pause reasons are a fixed set; encode domain-specific
    # pause intent (like OAuth) in the payload.
    if isinstance(result, PlannerPause) and result.reason == "await_input":
        # Return auth URL to user if your tool placed it in payload
        return PauseRequest(
            pause_token=result.resume_token,
            reason="oauth_required",
            auth_url=result.payload.get("auth_url"),
            provider=result.payload.get("provider"),
        )

    return AgentResponse(...)

async def resume(self, pause_token: str, auth_code: str) -> AgentResponse:
    """Resume after user completes OAuth."""
    # Exchange auth code for tokens
    await self._oauth_manager.exchange_code(auth_code)

    # Resume planner
    result = await self._planner.resume(token=pause_token, user_input=auth_code)
    return AgentResponse(...)
```

---

### Documentation Links

For detailed implementation guides:

- **StateStore Guide**: [docs/tools/statestore-guide.md](/Users/santiagobenvenuto/Repos/Penguiflow/penguiflow/docs/tools/statestore-guide.md)
  - State management for stateful tools
  - Transaction patterns and isolation

- **Concurrency Guide**: [docs/tools/concurrency-guide.md](/Users/santiagobenvenuto/Repos/Penguiflow/penguiflow/docs/tools/concurrency-guide.md)
  - Parallel tool execution patterns
  - Deadlock prevention strategies

- **Configuration Guide**: [docs/tools/configuration-guide.md](/Users/santiagobenvenuto/Repos/Penguiflow/penguiflow/docs/tools/configuration-guide.md)
  - Advanced configuration options
  - Transport-specific settings

- **ToolNode v2 Plan**: [docs/proposals/TOOLNODE_V2_PLAN.md](/Users/santiagobenvenuto/Repos/Penguiflow/penguiflow/docs/proposals/TOOLNODE_V2_PLAN.md)
  - Full technical specification
  - Architecture diagrams

---

## Agent Spec & Generator (v2.6+)

**New in v2.6**: Define your agent declaratively in YAML and generate production-ready code.

Instead of manually creating tools and wiring the planner, describe what you want in a spec file and let the generator create everything for you.

### When to Use Generate vs New

| Approach | Best For |
|----------|----------|
| `penguiflow generate` (recommended) | Spec-driven development, reproducible scaffolding, team handoff, CI consistency |
| `penguiflow new` | Quick start, exploring templates, learning patterns (then migrate to spec) |

### Generator Command

```bash
penguiflow generate --spec=agent.yaml [--output-dir=.] [--dry-run] [--force] [--verbose]
```

To scaffold a new spec workspace:

```bash
penguiflow generate --init my-agent [--output-dir=.] [--force]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--spec`, `-s` | Path to the agent spec YAML file (required) |
| `--init` | Create a new spec workspace (mutually exclusive with `--spec`) |
| `--output-dir` | Directory for the project (default: current directory) |
| `--dry-run` | Preview files without creating them |
| `--force` | Overwrite existing files |
| `--verbose`, `-v` | Show detailed generation progress |
| `--quiet`, `-q` | Suppress output messages |

### Spec Format

The spec YAML has these sections:

```yaml
# ─────────────────────────────────────────────────────────────
# AGENT DEFINITION
# ─────────────────────────────────────────────────────────────
agent:
  name: my-agent                    # Project name (required)
  description: My awesome agent     # Agent purpose
  template: react                   # Base template to use
  flags:
    streaming: true                 # --with-streaming
    hitl: false                     # --with-hitl
    a2a: false                      # --with-a2a
    memory: true                    # default true, false = --no-memory
    background_tasks: false         # --with-background-tasks

# ─────────────────────────────────────────────────────────────
# TOOLS (ReactPlanner catalog)
# ─────────────────────────────────────────────────────────────
tools:
  - name: search_documents          # Function name (snake_case)
    description: Search indexed documents by query
    side_effects: read              # pure|read|write|external|stateful
    tags: ["search", "rag"]         # For ToolPolicy filtering
    background:                     # Optional tool-level background execution (Jan 2026)
      enabled: false
      mode: job                     # job|subagent
      default_merge_strategy: HUMAN_GATED
      notify_on_complete: true
    args:
      query: str
      limit: Optional[int]
    result:
      documents: list[str]
      total_count: int

# ─────────────────────────────────────────────────────────────
# FLOWS (Linear DAGs only in v2.6)
# ─────────────────────────────────────────────────────────────
flows:
  - name: process_pipeline
    description: Document processing pipeline
    nodes:
      - name: fetch
        policy:
          timeout_s: 30
      - name: transform
      - name: store
    steps: [fetch, transform, store]  # Linear: fetch→transform→store

# ─────────────────────────────────────────────────────────────
# SERVICES (External integrations)
# ─────────────────────────────────────────────────────────────
services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000
  rag_server:
    enabled: false
  wayfinder:
    enabled: false

# ─────────────────────────────────────────────────────────────
# LLM CONFIGURATION
# ─────────────────────────────────────────────────────────────
llm:
  primary:
    model: gpt-4o                   # Required
    provider: openai                # Optional hint
  summarizer:
    enabled: true
    model: gpt-4o-mini              # Defaults to primary.model if omitted
  reflection:
    enabled: true
    quality_threshold: 0.8
    max_revisions: 2
    criteria:                       # Custom reflection criteria
      completeness: "Fully answers the query"
      accuracy: "Uses verified information"
      clarity: "Response is actionable"

# ─────────────────────────────────────────────────────────────
# PLANNER CONFIGURATION
# ─────────────────────────────────────────────────────────────
planner:
  max_iters: 12
  hop_budget: 8
  absolute_max_parallel: 5

  # Required: Agent identity & purpose
  system_prompt_extra: |
    You are a document search assistant.

    Your mission is to help users find relevant information
    in their document collection quickly and accurately.

    Always cite sources and indicate confidence levels.

  # Required if memory enabled
  memory_prompt: |
    You have access to the user's memory context:
    - conscious_memories: Recent session context
    - retrieved_memories: Relevant historical information

    Use memories to personalize responses and avoid
    asking for information already provided.

  hints:
    ordering: [search_documents, analyze_results]
    parallel_groups: [[fetch_a, fetch_b]]

  # Artifact Store (v2.8+) - for binary/large content
  artifact_store:
    enabled: true
    retention:
      ttl_seconds: 3600
      max_artifact_bytes: 52428800       # 50MB per artifact
      max_session_bytes: 524288000       # 500MB per session
      cleanup_strategy: lru              # lru, fifo, none

  # Rich output UI components (optional)
  rich_output:
    enabled: true
    allowlist: ["markdown", "echarts", "datagrid"]

  # Background tasks / subagent orchestration (optional)
  background_tasks:
    enabled: false
    allow_tool_background: false
    max_concurrent_tasks: 5
    max_tasks_per_session: 50
```

### Supported Types

The generator supports these type annotations:

| Type | Example | Generated Code |
|------|---------|----------------|
| `str` | `query: str` | `query: str` |
| `int` | `count: int` | `count: int` |
| `float` | `score: float` | `score: float` |
| `bool` | `enabled: bool` | `enabled: bool` |
| `list[T]` | `items: list[str]` | `items: list[str]` |
| `Optional[T]` | `limit: Optional[int]` | `limit: int \| None` |
| `dict[K,V]` | `meta: dict[str,str]` | `meta: dict[str, str]` |

### Example Spec

Here's a complete example for a RAG agent:

```yaml
agent:
  name: rag-assistant
  description: RAG-powered document assistant
  template: react
  flags:
    streaming: true
    memory: true

tools:
  - name: search_documents
    description: Search documents using semantic similarity
    side_effects: read
    tags: ["search", "rag"]
    args:
      query: str
      top_k: Optional[int]
    result:
      documents: list[str]
      scores: list[float]

  - name: summarize_results
    description: Summarize search results into a coherent answer
    side_effects: pure
    tags: ["synthesis"]
    args:
      documents: list[str]
      query: str
    result:
      summary: str
      citations: list[str]

services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000
  rag_server:
    enabled: true
    base_url: http://localhost:8081

llm:
  primary:
    model: gpt-4o
  reflection:
    enabled: true
    quality_threshold: 0.85

planner:
  max_iters: 10
  hop_budget: 6
  system_prompt_extra: |
    You are a helpful RAG assistant that searches documents
    and provides accurate, well-cited answers.

    Always search before answering factual questions.
    Cite your sources with document references.
  memory_prompt: |
    Use the user's memory context to:
    - Remember their preferences and past queries
    - Avoid repeating information already discussed
    - Build on previous conversations
```

### Generated Output

Running `penguiflow generate --spec=agent.yaml` creates:

```
rag-assistant/
├── src/rag_assistant/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py              # With LLM inheritance
│   ├── orchestrator.py
│   ├── planner.py             # Tool catalog + prompts
│   ├── models.py
│   ├── telemetry.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── search_documents.py   # Generated tool stub
│   │   └── summarize_results.py  # Generated tool stub
│   └── clients/
│       └── memory.py
├── tests/
│   ├── conftest.py
│   ├── test_orchestrator.py
│   └── test_tools/
│       ├── test_search_documents.py
│       └── test_summarize_results.py
├── pyproject.toml
├── .env.example               # Minimal env template (add provider keys as needed)
└── .vscode/
```

### Generated Tool Example

Each tool in the spec generates a file like this:

```python
# src/rag_assistant/tools/search_documents.py
"""Tool: Search documents using semantic similarity"""

from pydantic import BaseModel
from penguiflow.catalog import tool
from penguiflow.planner import ToolContext


class SearchDocumentsArgs(BaseModel):
    """Search documents using semantic similarity input."""
    query: str
    top_k: int | None


class SearchDocumentsResult(BaseModel):
    """Search documents using semantic similarity output."""
    documents: list[str]
    scores: list[float]


@tool(desc="Search documents using semantic similarity", tags=["search", "rag"], side_effects="read")
async def search_documents(args: SearchDocumentsArgs, ctx: ToolContext) -> SearchDocumentsResult:
    """TODO: Implement search_documents logic."""
    del ctx  # avoid unused-variable lint warnings until implemented
    raise NotImplementedError("Implement search_documents")
```

### Error Messages

The generator provides actionable errors with file:line references:

```bash
$ penguiflow generate --spec=agent.yaml

agent.yaml:15 tools[0].name - Invalid tool name 'SearchDocs': must be snake_case
  Suggestion: Use 'search_docs' instead

agent.yaml:23 tools[1].args.query - Unsupported type 'Query': use str, int, float, bool, list[T], Optional[T], or dict[K,V]
  Suggestion: Did you mean 'str'?

agent.yaml:45 planner.system_prompt_extra - Required field is empty
  Suggestion: Define who your agent is and what it does
```

---

### Tested Template Examples

The following specs have been tested and verified to generate valid, runnable projects.

#### React Template (Recommended Start)

A simple conversational agent with a greeting tool:

```yaml
# hello_world.yaml
name: hello-agent
description: Hello world demo agent
template: react

# Shorthand format (equivalent to agent.flags.memory: false)
memory: false

llm:
  provider: openrouter
  model: anthropic/claude-sonnet-4

tools:
  - name: greet
    description: Greet a user by name
    args:
      - name: name
        type: str
    result:
      - name: greeting
        type: str

planner:
  system_prompt_extra: |
    You are a friendly assistant that greets users.
    Use the greet tool to generate personalized greetings.
```

Generate and run:

```bash
penguiflow generate --spec=hello_world.yaml
cd hello-agent
uv sync && cp .env.example .env
# Edit .env to add your API key
uv run python -m hello_agent
```

---

#### Parallel Template (Multi-Source)

Batch processing agent that fetches from multiple sources:

```yaml
# parallel_agent.yaml
name: parallel-agent
description: Parallel data fetching agent
template: parallel

memory: true  # Enable memory integration

llm:
  provider: openrouter
  model: anthropic/claude-sonnet-4

tools:
  - name: fetch_data
    description: Fetch data from a specified source
    tags: ["fetch", "parallel"]
    side_effects: read
    args:
      - name: source_id
        type: str
    result:
      - name: data
        type: dict

  - name: process_data
    description: Process and transform fetched data
    tags: ["process"]
    side_effects: pure
    args:
      - name: data
        type: dict
    result:
      - name: processed
        type: dict

services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000

planner:
  absolute_max_parallel: 4
  system_prompt_extra: |
    You are a data aggregation assistant.
    Fetch from multiple sources in parallel and combine results.
  memory_prompt: |
    Use retrieved memories to understand user preferences
    for data sources and processing options.
```

---

#### Enterprise Template (Production-Ready)

Full-featured enterprise agent with observability and resilience:

```yaml
# enterprise_agent.yaml
name: enterprise-agent
description: Production-grade enterprise support agent
template: enterprise

memory: true

llm:
  provider: openrouter
  model: anthropic/claude-sonnet-4
  reflection:
    enabled: true
    quality_threshold: 0.85
    max_revisions: 2

tools:
  - name: audit_log
    description: Log audit events for compliance tracking
    tags: ["audit", "compliance"]
    side_effects: write
    args:
      - name: event_type
        type: str
      - name: details
        type: dict
    result:
      - name: log_id
        type: str

  - name: validate_permissions
    description: Validate user permissions for a resource
    tags: ["security", "permissions"]
    side_effects: read
    args:
      - name: user_id
        type: str
      - name: resource
        type: str
    result:
      - name: allowed
        type: bool

services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000

planner:
  max_iters: 15
  hop_budget: 10
  system_prompt_extra: |
    You are an enterprise support agent with full audit capabilities.
    Always validate permissions before accessing sensitive resources.
    Log all significant actions for compliance.
  memory_prompt: |
    Consider the user's role, recent interactions, and permission history
    when determining access levels and providing assistance.
```

---

#### Flow Template (DAG Pipelines)

Agent with PenguiFlow DAG for data processing pipelines:

```yaml
# flow_agent.yaml
name: flow-agent
description: ETL pipeline agent with DAG orchestration
template: react  # Use react as base, flows add DAG capability

memory: true

llm:
  provider: openrouter
  model: anthropic/claude-sonnet-4

tools:
  - name: input
    description: Receive and validate input data
    args:
      - name: source
        type: str
    result:
      - name: data
        type: dict

  - name: process
    description: Transform and enrich data
    args:
      - name: data
        type: dict
    result:
      - name: processed
        type: dict

  - name: output
    description: Store processed results
    args:
      - name: processed
        type: dict
    result:
      - name: status
        type: str

# Define PenguiFlow DAG
flows:
  - name: data_pipeline
    description: Simple data processing pipeline
    nodes:
      - name: input
        next: [process]
      - name: process
        next: [output]
      - name: output
        next: []

services:
  memory_iceberg:
    enabled: true
    base_url: http://localhost:8000

planner:
  system_prompt_extra: |
    You are a data pipeline orchestrator.
    Use the flow tools to process data through the pipeline.
  memory_prompt: |
    Reference past pipeline executions to optimize processing.
```

This generates both tools and a `flows/` directory with PenguiFlow DAG definitions:

```
flow-agent/
├── src/flow_agent/
│   ├── tools/
│   │   ├── input.py
│   │   ├── process.py
│   │   └── output.py
│   └── flows/
│       ├── __init__.py
│       └── data_pipeline.py   # PenguiFlow DAG
└── ...
```

The generated flow can be used as a subflow within the planner or run independently.

---

#### Memory Configuration

Memory can be configured at two levels:

**1. Shorthand (top-level):**
```yaml
memory: false  # Disable memory
```

**2. Full flags:**
```yaml
agent:
  name: my-agent
  flags:
    memory: true
    streaming: true
    hitl: false
    a2a: false
```

When memory is enabled, the generator:
- Adds `memory_enabled: bool = True` to `config.py`
- Creates `clients/memory.py` with `MemoryClient` stub
- Wires memory integration in `orchestrator.py`
- Adds `MEMORY_PROMPT` handling in `planner.py`

When memory is disabled:
- `memory_enabled: bool = False` in `config.py`
- No memory client initialization
- `llm_context` passed as empty dict

---

#### Artifact Store Configuration

**New in v2.8**: Store binary content (PDFs, images, large files) from MCP tools and make them accessible via the Playground UI.

```yaml
planner:
  artifact_store:
    enabled: true                        # Enable artifact storage
    retention:
      ttl_seconds: 3600                  # Time-to-live (1 hour default)
      max_artifact_bytes: 52428800       # 50MB per artifact
      max_session_bytes: 524288000       # 500MB per session
      max_trace_bytes: 104857600         # 100MB per trace
      max_artifacts_per_trace: 100
      max_artifacts_per_session: 1000
      cleanup_strategy: lru              # lru, fifo, none
```

**When artifact store is enabled:**
- `InMemoryArtifactStore` is created with retention config
- Artifacts stored by MCP tools (e.g., Tableau PDFs) are accessible
- Playground UI shows artifacts in the sidebar
- REST endpoint `/artifacts/{id}` serves binary content

**Generated config fields:**
```python
# In config.py
artifact_store_enabled: bool = True
artifact_store_ttl_seconds: int = 3600
artifact_store_max_artifact_bytes: int = 52428800
artifact_store_max_session_bytes: int = 524288000
artifact_store_cleanup_strategy: str = "lru"
```

**Generated planner setup:**
```python
# In planner.py
def _build_artifact_store(config: Config) -> InMemoryArtifactStore | None:
    if not config.artifact_store_enabled:
        return None

    return InMemoryArtifactStore(
        retention=ArtifactRetentionConfig(
            ttl_seconds=config.artifact_store_ttl_seconds,
            max_artifact_bytes=config.artifact_store_max_artifact_bytes,
            max_session_bytes=config.artifact_store_max_session_bytes,
            cleanup_strategy=config.artifact_store_cleanup_strategy,
        ),
    )
```

**Environment variables:**
```bash
# .env
ARTIFACT_STORE_ENABLED=true
ARTIFACT_STORE_TTL_SECONDS=3600
ARTIFACT_STORE_MAX_ARTIFACT_BYTES=52428800
ARTIFACT_STORE_MAX_SESSION_BYTES=524288000
ARTIFACT_STORE_CLEANUP_STRATEGY=lru
```

**Use cases:**
- MCP tools that export PDFs (e.g., Tableau)
- Image generation tools
- Document processing outputs
- Large text files (logs, reports)

---

### Workflow

1. **Start with the reference spec:**
   ```bash
   # Copy the reference spec
   cp $(python -c "import penguiflow; print(penguiflow.__path__[0])")/templates/spec.template.yaml agent.yaml
   ```

2. **Edit the spec** with your agent's requirements

3. **Preview the generation:**
   ```bash
   penguiflow generate --spec=agent.yaml --dry-run
   ```

4. **Generate the project:**
   ```bash
   penguiflow generate --spec=agent.yaml
   ```

5. **Implement the tools:**
   ```bash
   cd my-agent
   # Edit src/my_agent/tools/*.py to add your logic
   ```

6. **Run and test:**
   ```bash
   uv sync
   cp .env.example .env
   uv run pytest
   uv run python -m my_agent
   ```

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
│       ├── a2a.py               # A2A placeholder scaffold (Jan 2026: not production-ready)
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

Copy `.env.example` to `.env` and configure.

By default, templates include only a **minimal** environment surface (so projects run without provider credentials).
If you switch to a real LLM backend (LiteLLM or native), you must add the relevant provider API keys.

```bash
# Required: model identifier (stub by default)
LLM_MODEL=stub-llm

# Memory Server (if enabled)
MEMORY_BASE_URL=http://localhost:8000

# Optional planner knobs (template defaults are usually fine)
PLANNER_MULTI_ACTION_SEQUENTIAL=false
PLANNER_MULTI_ACTION_READ_ONLY_ONLY=true
PLANNER_MULTI_ACTION_MAX_TOOLS=2

# Provider credentials (examples, required only when using a real model)
# OPENROUTER_API_KEY=...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...
# DATABRICKS_HOST=...
# DATABRICKS_TOKEN=...

# Rich output (when --with-rich-output)
# RICH_OUTPUT_ENABLED=true
# RICH_OUTPUT_ALLOWLIST=markdown,echarts,datagrid

# Background tasks (when --with-background-tasks)
# BACKGROUND_TASKS_ENABLED=true
# BACKGROUND_TASKS_MAX_CONCURRENT_TASKS=5
```

### Config Class

All templates include a `config.py` with type-safe configuration:

```python
from dataclasses import dataclass
import os

@dataclass
class Config:
    """Agent configuration loaded from environment."""

    memory_base_url: str
    llm_model: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            memory_base_url=os.getenv("MEMORY_BASE_URL", "http://localhost:8000"),
            llm_model=os.getenv("LLM_MODEL", "stub-llm"),
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
# If you switched to a real model backend, add provider API keys to `.env`

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

### 4. Extracting Answers from Payload

**v2.7+ uses structured `FinalPayload` with `raw_answer` field:**

```python
# DO: Extract raw_answer from FinalPayload
def _extract_answer(payload: dict) -> str:
    """Extract answer text from planner payload.

    Tries keys in priority order for backward compatibility:
    1. raw_answer (v2.7+)
    2. answer (legacy)
    3. text (legacy)
    4. result (legacy)
    """
    for key in ("raw_answer", "answer", "text", "result"):
        if key in payload:
            return str(payload.get(key))

    # Fallback: serialize entire payload
    return str(payload)

# Usage in orchestrator
async def execute(self, query: str, ...) -> AgentResponse:
    result = await self._planner.run(...)

    payload = result.payload
    answer_text = _extract_answer(payload)

    return AgentResponse(
        answer=answer_text,
        artifacts=payload.get("artifacts", {}),
        confidence=payload.get("confidence"),
        sources=payload.get("sources", []),
    )
```

**Why this pattern?**
- Handles both v2.7+ (`raw_answer`) and legacy formats
- Gracefully degrades if planner doesn't use structured output
- Centralizes extraction logic for maintainability

### 5. Memory Integration

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

    # 4. Extract answer (v2.7+ uses raw_answer)
    payload = result.payload
    answer_text = _extract_answer(payload)

    # 5. Ingest interaction
    await self._memory.ingest_interaction(
        user_prompt=query,
        agent_response=answer_text,
        ...
    )
```

### 6. Production Checklist

- [ ] Configure proper `LLM_MODEL` for production
- [ ] Set `LOG_LEVEL=INFO` (not DEBUG)
- [ ] Enable `TELEMETRY_BACKEND` (mlflow, logging)
- [ ] Set `ReactPlanner(token_budget=...)` to control trajectory size
- [ ] Configure `ReactPlanner(llm_max_retries=..., llm_timeout_s=...)` for resilience
- [ ] Decide LLM backend: LiteLLM vs `use_native_llm=True`
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

# If using a real model backend, ensure provider API key is set
echo $OPENROUTER_API_KEY  # or OPENAI_API_KEY / ANTHROPIC_API_KEY / DATABRICKS_TOKEN ...
```

**Memory client connection refused**

```bash
# If you don't want memory, scaffold without it:
penguiflow new my-agent --no-memory
```

**Planner loops forever**

Set safety limits in `src/<your_package>/planner.py`:

- `ReactPlanner(max_iters=...)`
- `ReactPlanner(deadline_s=...)`
- `ReactPlanner(hop_budget=...)`

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
| rag_server | `penguiflow new NAME --template=rag_server` | RAG applications |
| wayfinder | `penguiflow new NAME --template=wayfinder` | NLQ-to-SQL |
| analyst | `penguiflow new NAME --template=analyst` | Analysis/reporting patterns |
| enterprise | `penguiflow new NAME --template=enterprise` | Production platforms |

### All Flags

| Flag | Effect |
|------|--------|
| `--with-streaming` | Add real-time streaming + status updates |
| `--with-hitl` | Add human-in-the-loop pause/resume |
| `--with-a2a` | Add A2A placeholder scaffold (Jan 2026: WIP) |
| `--with-rich-output` | Add rich output component tooling |
| `--with-background-tasks` | Add background tasks/subagent scaffolding |
| `--no-memory` | Remove Memory Server integration |
| `--force` | Overwrite existing files |
| `--dry-run` | Preview without creating files |

### Common Combinations

```bash
# Standard agent with streaming
penguiflow new my-agent --with-streaming

# RAG with human approval for sensitive queries
penguiflow new my-rag --template=rag_server --with-hitl

# Stateless NLQ service
penguiflow new my-nlq --template=wayfinder --no-memory

# Full enterprise with everything
penguiflow new my-platform --template=enterprise --with-streaming --with-hitl --with-rich-output --with-background-tasks
```

---

**Happy building!** 🐧
