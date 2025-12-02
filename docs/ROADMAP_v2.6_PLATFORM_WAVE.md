# PenguiFlow v2.6 — Platform Wave

## Vision

Transform PenguiFlow from "excellent framework for those who know it" to "platform where anyone can ship agents predictably."

**v2.6 Deliverables:**
1. Agent Spec & Generator (`penguiflow generate`)
2. Dev Playground (`penguiflow dev`)

*Golden Templates deferred to v2.6.5+*

---

## 1. Agent Spec & Generator

### Problem

Going from idea → running agent requires:
- Knowing which template to pick
- Hand-writing tool definitions
- Manually wiring planner registrations
- Understanding orchestrator patterns (emit/fetch, FlowBundle, etc.)
- Copy-pasting patterns from guides

### Solution

Declarative YAML spec → generated project following best practices automatically.

### Spec Format

```yaml
# ─────────────────────────────────────────────────────────────
# AGENT DEFINITION
# ─────────────────────────────────────────────────────────────
agent:
  name: string                      # Project name (required)
  description: string               # Agent purpose (for docs)
  template: enum                    # minimal|react|parallel|lighthouse|wayfinder|analyst|enterprise
  flags:
    streaming: bool                 # --with-streaming
    hitl: bool                      # --with-hitl
    a2a: bool                       # --with-a2a
    memory: bool                    # default true, false = --no-memory

# ─────────────────────────────────────────────────────────────
# TOOLS (ReactPlanner catalog)
# ─────────────────────────────────────────────────────────────
tools:
  - name: string                    # Function name (snake_case)
    description: string             # LLM-visible description
    side_effects: enum              # pure|read|write|external|stateful
    tags: [string]                  # Filtering tags for ToolPolicy
    group: string                   # Optional grouping
    args:                           # Input schema (Pydantic model)
      field_name: type              # str, int, float, bool, list[T], Optional[T], dict[K,V]
    result:                         # Output schema (Pydantic model)
      field_name: type

# ─────────────────────────────────────────────────────────────
# FLOWS (PenguiFlow DAGs - tool can invoke a flow)
# Linear DAGs only in v2.6
# ─────────────────────────────────────────────────────────────
flows:
  - name: string                    # Flow identifier
    description: string             # Purpose
    nodes:                          # Linear DAG definition
      - name: string                # Node name (matches registry)
        description: string         # What this step does
        policy:                     # Optional NodePolicy overrides
          validate: in|out|both|none
          timeout_s: float
          max_retries: int
          backoff_base: float
    # Shorthand for linear flows (implies sequential edges):
    steps: [node_a, node_b, node_c] # a→b→c

# ─────────────────────────────────────────────────────────────
# SERVICES (External integrations)
# ─────────────────────────────────────────────────────────────
services:
  memory_iceberg:
    enabled: bool
    base_url: string                # MEMORY_BASE_URL
  lighthouse:
    enabled: bool
    base_url: string                # LIGHTHOUSE_BASE_URL
  wayfinder:
    enabled: bool
    base_url: string                # WAYFINDER_BASE_URL

# ─────────────────────────────────────────────────────────────
# LLM CONFIGURATION
# ─────────────────────────────────────────────────────────────
llm:
  primary:
    model: string                   # e.g., "gpt-4o", "claude-sonnet-4-20250514"
    provider: string                # openai|anthropic|litellm (optional)

  # Optional specialized LLMs (default to primary.model if omitted)
  summarizer:
    enabled: bool
    model: string                   # defaults to primary.model

  reflection:
    enabled: bool
    model: string                   # defaults to primary.model
    quality_threshold: float        # 0.0-1.0, default 0.80
    max_revisions: int              # default 2
    criteria:                       # Custom evaluation criteria (optional)
      completeness: string          # default: "Addresses all parts of the query"
      accuracy: string              # default: "Factually correct based on observations"
      clarity: string               # default: "Well-explained and coherent"

# ─────────────────────────────────────────────────────────────
# PLANNER CONFIGURATION
# ─────────────────────────────────────────────────────────────
planner:
  max_iters: int                    # default 12
  hop_budget: int                   # default 8
  absolute_max_parallel: int        # default 5

  # Agent identity & purpose (REQUIRED - defines WHO the agent is)
  system_prompt_extra: |
    You are a [role] specialized in [domain].

    Your mission is to [primary objective].

    Voice & tone:
    - [communication style guidelines]
    - [formality level]
    - [any domain-specific language preferences]

    Key behaviors:
    - [behavior 1]
    - [behavior 2]

  # Memory consumption guidance (required if memory enabled)
  memory_prompt: |
    You have access to the user's memory context:
    - conscious_memories: Recent session context and prior interactions
    - retrieved_memories: Relevant historical information for this query

    Use memories to:
    - Personalize responses based on user history
    - Avoid asking for information already provided
    - Reference previous conversations when relevant
    - Build on established context

  # Planning hints (optional)
  hints:
    ordering: [string]              # Preferred tool order
    parallel_groups: [[string]]     # Tools that can run together
    sequential_only: [string]       # Tools that must run alone
    disallow: [string]              # Forbidden tools
```

### CLI Command

```bash
penguiflow generate --spec=agent.yaml [--output-dir=.] [--dry-run] [--force]
```

### Generator Behavior

#### Phase 1: Validation
- Parse YAML against Pydantic schema
- Validate tool names (snake_case, unique)
- Validate flow node references exist
- Validate type annotations are supported
- Emit actionable errors with file:line references

#### Phase 2: Scaffold
- Call `penguiflow new {name} --template={template} {flags}`
- Leverages existing template system

#### Phase 3: Generate Tools
For each tool in spec, generate `tools/{name}.py`:

```python
"""Tool: {description}"""

from pydantic import BaseModel
from penguiflow.planner import tool, ToolContext


class {Name}Args(BaseModel):
    """{description} input."""
    {field}: {type}


class {Name}Result(BaseModel):
    """{description} output."""
    {field}: {type}


@tool(desc="{description}", tags=[{tags}], side_effects="{side_effects}")
async def {name}(args: {Name}Args, ctx: ToolContext) -> {Name}Result:
    """TODO: Implement {name} logic."""
    raise NotImplementedError("Implement {name}")
```

#### Phase 4: Generate Flows
For each flow in spec, generate `flows/{name}.py` following best practices from `PENGUIFLOW_BEST_PRACTICES.md`:

```python
"""Flow: {description}"""

from dataclasses import dataclass
from typing import Any

from penguiflow import ModelRegistry, Node, NodePolicy, create
from penguiflow.types import Message


@dataclass(slots=True)
class _{Name}FlowBundle:
    flow: Any
    registry: ModelRegistry
    {node_name}_node: Node


def _build_{name}_flow(*, {dependencies}) -> _{Name}FlowBundle:
    """Create PenguiFlow DAG for {description}."""

    def _make_{node_name}_node() -> Node:
        async def _{node_name}(message: Message, _ctx: Any) -> Message:
            base_message = (
                message if isinstance(message, Message)
                else Message.model_validate(message)
            )
            payload = base_message.payload

            # TODO: Implement {node_name} logic
            result = payload

            return base_message.model_copy(update={"payload": result})

        return Node(
            _{node_name},
            name="{node_name}",
            policy=NodePolicy({policy}),
        )

    # Create nodes
    {node_name}_node = _make_{node_name}_node()

    # Build flow (linear DAG)
    flow = create(
        {edges}
    )

    # Register models
    registry = ModelRegistry()
    {registrations}

    return _{Name}FlowBundle(
        flow=flow,
        registry=registry,
        {node_name}_node={node_name}_node,
    )
```

#### Phase 5: Generate Planner
Generate `planner.py` with catalog registration, LLM config inheritance, and system prompts:

```python
"""Planner configuration for {agent_name}."""

from penguiflow.planner import ReactPlanner, ReflectionConfig
from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry

from .config import Config
from .tools import {tool_imports}


# Agent identity & purpose (from spec)
SYSTEM_PROMPT_EXTRA = """
{system_prompt_extra}
"""

# Memory consumption guidance (from spec, if memory enabled)
MEMORY_PROMPT = """
{memory_prompt}
"""


def build_planner(config: Config, *, event_callback=None) -> PlannerBundle:
    """Build ReactPlanner with tool catalog."""

    nodes = [
        Node({tool_name}, name="{tool_name}"),
    ]

    registry = ModelRegistry()
    {tool_registrations}

    catalog = build_catalog(nodes, registry)

    # LLM config with inheritance
    reflection_model = config.reflection_model or config.llm_model
    summarizer_model = config.summarizer_model or config.llm_model

    # Combine system prompt with memory guidance if enabled
    full_system_extra = SYSTEM_PROMPT_EXTRA
    if config.memory_enabled:
        full_system_extra += "\n\n" + MEMORY_PROMPT

    planner = ReactPlanner(
        llm=config.llm_model,
        catalog=catalog,
        system_prompt_extra=full_system_extra,
        reflection_config=ReflectionConfig(
            enabled=config.reflection_enabled,
            quality_threshold=config.reflection_quality_threshold,
            max_revisions=config.reflection_max_revisions,
        ) if config.reflection_enabled else None,
        reflection_llm=reflection_model if config.reflection_enabled else None,
        summarizer_llm=summarizer_model if config.summarizer_enabled else None,
        event_callback=event_callback,
    )

    return PlannerBundle(planner=planner, registry=registry)
```

#### Phase 6: Generate Tests
For each tool, generate `tests/test_tools/test_{name}.py`:

```python
"""Tests for {name} tool."""

import pytest
from {package}.tools.{name} import {name}, {Name}Args


@pytest.mark.asyncio
async def test_{name}_not_implemented() -> None:
    """Test {name} raises NotImplementedError until implemented."""
    args = {Name}Args({sample_args})

    with pytest.raises(NotImplementedError):
        await {name}(args, ctx=None)
```

#### Phase 7: Generate Config
Update `config.py` with LLM inheritance and service URLs.

Generate `.env.example`:

```bash
# LLM Configuration
LLM_MODEL={primary_model}
# SUMMARIZER_MODEL=  # defaults to LLM_MODEL
# REFLECTION_MODEL=  # defaults to LLM_MODEL

# Services
{MEMORY_BASE_URL=...}
{LIGHTHOUSE_BASE_URL=...}
{WAYFINDER_BASE_URL=...}

# Planner
PLANNER_MAX_ITERS={max_iters}
PLANNER_HOP_BUDGET={hop_budget}
```

### Scope Boundaries

**In scope (v2.6):**
- Spec validation with actionable errors
- Tool stub generation (Pydantic models + async functions)
- Flow stub generation (FlowBundle pattern, linear DAGs)
- Planner wiring with LLM config inheritance
- Test scaffolding
- Config and .env generation

**Out of scope (v2.6):**
- Tool implementation (user writes logic)
- Complex nested types (nested models, unions)
- Multi-agent orchestration specs
- Flow branching/routing logic (linear DAGs only)

---

## 2. Dev Playground

### Problem

Testing agents requires terminal + manual curl/httpie. No visual feedback on planner behavior during development.

### Solution

Minimal local dev server with trajectory visualization.

### CLI Command

```bash
penguiflow dev [project_dir] [--port=8080] [--no-browser]
```

### Behavior

1. **Discovery**: Find agent in `project_dir`
   - Look for `orchestrator.py` with class matching `*Orchestrator`
   - Or `__main__.py` with `main()` function
   - Or `planner.py` with `build_planner()`

2. **Wrap**: Create lightweight HTTP adapter
   - Dynamic import of orchestrator/planner
   - Hook into `event_callback` for telemetry
   - In-memory StateStore (protocol-based for easy swapping)

3. **Serve**: Start FastAPI server
   - `POST /chat` — Send query, get response
   - `GET /chat/stream` — SSE for streaming responses
   - `GET /trajectory/{trace_id}` — Fetch trajectory steps
   - `GET /events` — SSE for real-time PlannerEvents
   - `GET /health` — Health check

4. **UI**: Serve pre-built Svelte 5 SPA (bundled in CLI, no npm required)

5. **Browser**: Open automatically (unless `--no-browser`)

### StateStore Protocol

In-memory default, tied to user/session to avoid context leaking. Protocol-based for easy downstream swapping:

```python
class PlaygroundStateStore(Protocol):
    """Protocol for playground state storage."""

    async def save_trajectory(
        self,
        trace_id: str,
        session_id: str,
        trajectory: TrajectorySnapshot,
    ) -> None: ...

    async def get_trajectory(
        self,
        trace_id: str,
        session_id: str,
    ) -> TrajectorySnapshot | None: ...

    async def list_traces(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[str]: ...

    async def save_event(
        self,
        trace_id: str,
        event: PlannerEvent,
    ) -> None: ...

    async def get_events(
        self,
        trace_id: str,
    ) -> list[PlannerEvent]: ...
```

Downstream teams can implement this protocol with Redis, Kafka, PostgreSQL, etc.

### API Surface

**POST /chat**
```json
// Request
{
  "query": "string",
  "session_id": "string (optional, auto-generated if omitted)",
  "context": {}
}

// Response
{
  "trace_id": "string",
  "session_id": "string",
  "answer": "string",
  "metadata": {
    "steps": [...],
    "cost": {...}
  }
}
```

**GET /chat/stream**
```
event: chunk
data: {"trace_id": "...", "seq": 0, "text": "token", "done": false}

event: step
data: {"trace_id": "...", "node": "search", "status": "complete", "latency_ms": 123}

event: done
data: {"trace_id": "...", "answer": "..."}
```

**GET /trajectory/{trace_id}?session_id={session_id}**
```json
{
  "trace_id": "string",
  "query": "string",
  "steps": [
    {
      "node": "string",
      "thought": "string",
      "args": {},
      "result": {},
      "latency_ms": 123,
      "reflection": {"score": 0.85, "passed": true}
    }
  ],
  "answer": "string",
  "total_latency_ms": 456
}
```

**GET /events?session_id={session_id}**
```
event: planner_event
data: {"event_type": "step_complete", "node_name": "search", "latency_ms": 123, ...}
```

### UI Components

**Chat Panel**
- Query input with send button
- Streaming response display
- Session history sidebar
- Clear/new session button

**Trajectory Panel**
- Step-by-step timeline view
- Each step shows:
  - Node name + thought
  - Args (collapsible JSON)
  - Result (collapsible JSON)
  - Latency badge
  - Reflection score (if enabled)
- Parallel execution shown as grouped steps
- Error steps highlighted red

**Events Panel**
- Real-time PlannerEvent stream
- Filter by event_type dropdown
- Latency highlights (>p95 yellow, >p99 red)
- Pause/resume stream button

**Config Panel** (read-only)
- Current planner config
- Tool catalog summary
- Active flags

### Technical Approach

**Backend** (`penguiflow/cli/playground.py`)
- Single module, ~500 lines
- Dynamic orchestrator import via `importlib`
- Wraps any orchestrator with standard interface
- In-memory StateStore with session isolation
- Protocol-based for downstream customization

**Frontend** (`penguiflow/cli/playground_ui/`)
- Svelte 5 SPA
- Pre-compiled to static assets
- Bundled in package (no npm required for end users)
- Served directly by FastAPI `StaticFiles`
- ~3 components: Chat, Trajectory, Events

### Scope Boundaries

**In scope (v2.6):**
- Single-agent interaction
- Trajectory visualization
- Event streaming
- Basic chat interface
- In-memory state with session isolation
- Protocol for downstream state store swapping

**Out of scope (v2.6):**
- Multi-agent visualization
- Memory/knowledge base browsing
- Evaluation running from UI
- Config editing from UI
- Auth/multi-user
- Persistent state (Redis, SQL, etc.)

---

## Delivery Phases

| Phase | Deliverable | Effort | Dependencies |
|-------|-------------|--------|--------------|
| **v2.6.0-alpha** | Spec schema + validation | 1 week | None |
| **v2.6.0-beta** | Generator: tools + planner | 1 week | alpha |
| **v2.6.0-rc** | Generator: flows + tests + docs | 1 week | beta |
| **v2.6.0** | Generator release | — | rc |
| **v2.6.1** | Playground backend + StateStore protocol | 1-2 weeks | None (parallel) |
| **v2.6.2** | Playground Svelte UI (pre-built) | 2 weeks | v2.6.1 |
| **v2.6.5+** | Golden Templates | TBD | v2.6.0 |

**Total: ~6-7 weeks**

---

## Success Criteria

### Agent Spec & Generator
- [x] New dev: spec → running agent in <5 minutes
- [x] Spec validation catches mistakes with actionable errors (file:line)
- [x] Generated code passes `ruff check` + `mypy`
- [x] Generated tests pass (with NotImplementedError)
- [x] `spec.template.yaml` documents all options clearly
- [x] Flows follow FlowBundle + emit/fetch best practices
- [x] LLM configs inherit from primary when omitted

### Dev Playground
- [ ] `penguiflow dev .` works on any generated project
- [ ] Works on any existing ReactPlanner project
- [ ] Trajectory visible within 1 second of step completion
- [ ] Events stream in real-time
- [ ] Session isolation prevents context leaking
- [ ] StateStore protocol documented for downstream swapping
- [ ] Svelte UI loads without npm/build step for end user

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Flow complexity | Linear DAGs only | Simplicity for v2.6; branching in v2.7+ |
| StateStore default | In-memory with session isolation | Simple, no external deps; protocol enables swapping |
| Svelte bundling | Pre-built in CLI package | No npm required for end users |
| LLM config inheritance | reflection/summarizer → primary.model | Reduces boilerplate, sensible default |
| Spec versioning | None | No backward compatibility; adapt guides as needed |

---

## Non-Goals (v2.6)

- Multi-agent orchestration specs
- Persistent state storage (Redis, SQL)
- Flow branching/routing in spec
- Backward compatibility guarantees for spec format
- Production deployment mode for playground
- Authentication/authorization

---

## Development Phases

### Agent Spec & Generator — Development Breakdown

#### Phase G1: Spec Schema & Validation (Week 1)

**Objective**: Define and validate the YAML spec format.

**Tasks**:
- [x] Define Pydantic models for spec schema
  - `AgentSpec`, `ToolSpec`, `FlowSpec`, `ServiceSpec`, `LLMSpec`, `PlannerSpec`
- [x] Implement YAML parser with schema validation
- [x] Create error formatting with file:line references
- [x] Validate tool names (snake_case, unique, no reserved words)
- [x] Validate type annotations (supported types only)
- [x] Validate flow node references exist in tools
- [x] Validate `system_prompt_extra` is non-empty (required)
- [x] Validate `memory_prompt` is present if memory enabled
- [x] Write unit tests for validation edge cases

**Deliverables**:
- `penguiflow/cli/spec.py` — Spec models and validation
- `penguiflow/cli/spec_errors.py` — Error formatting
- `tests/cli/test_spec_validation.py` — Validation tests

**Exit Criteria**:
- [x] Invalid specs produce actionable errors
- [x] Valid specs parse to typed Pydantic models
- [x] 100% test coverage on validation logic

---

#### Phase G2: Tool & Planner Generation (Week 2)

**Objective**: Generate tools and planner wiring from spec.

**Tasks**:
- [x] Implement type annotation → Python type mapping
  - `str`, `int`, `float`, `bool`, `list[T]`, `Optional[T]`, `dict[K,V]`
- [x] Create Jinja2 templates for tool generation
  - `{Name}Args` model
  - `{Name}Result` model
  - `@tool` decorated function stub
- [x] Create Jinja2 template for planner.py
  - Import all tools
  - Build catalog
  - LLM config with inheritance logic
- [x] Wire `penguiflow generate` CLI command
- [x] Integrate with existing `penguiflow new` for scaffolding
- [x] Write integration tests

**Deliverables**:
- `penguiflow/cli/generate.py` — Generator logic
- `penguiflow/cli/templates/tool.py.jinja` — Tool template
- `penguiflow/cli/templates/planner.py.jinja` — Planner template
- `tests/cli/test_generate_tools.py` — Tool generation tests

**Exit Criteria**:
- [x] Tools generate with correct Pydantic models
- [x] Planner wires all tools correctly
- [x] LLM config inheritance works
- [x] Generated code passes ruff + mypy

---

#### Phase G3: Flow & Test Generation (Week 3)

**Objective**: Generate flows and test scaffolding.

**Tasks**:
- [x] Create Jinja2 template for flow generation
  - FlowBundle dataclass
  - Node factory functions
  - Linear edge wiring
  - ModelRegistry registration
- [x] Create Jinja2 template for tool tests
  - Basic NotImplementedError test per tool
- [x] Create Jinja2 template for flow tests
  - Basic flow execution test
- [x] Generate config.py with LLM inheritance
- [x] Generate .env.example with all variables
- [x] Create `spec.template.yaml` reference file
- [x] Write end-to-end tests

**Deliverables**:
- `penguiflow/cli/templates/flow.py.jinja` — Flow template
- `penguiflow/cli/templates/test_tool.py.jinja` — Tool test template
- `penguiflow/cli/templates/config.py.jinja` — Config template
- `penguiflow/templates/spec.template.yaml` — Reference spec
- `tests/cli/test_generate_e2e.py` — End-to-end tests
- `penguiflow/cli/templates/test_flow.py.jinja` — Flow test template
- `penguiflow/cli/templates/env.example.jinja` — Env template
- `penguiflow/cli/templates/flows_init.py.jinja` — Flows package bootstrap

**Exit Criteria**:
- [x] Flows follow FlowBundle best practices
- [x] Tests scaffold correctly
- [x] spec.template.yaml documents all options
- [x] Full generate → run cycle works

---

#### Phase G4: Documentation & Polish (Week 4)

**Objective**: Documentation, error messages, and release prep.

**Tasks**:
- [x] Write `penguiflow generate` section in docs
- [x] Add examples to TEMPLATING_QUICKGUIDE.md
- [x] Improve error messages with suggestions
- [x] Add `--dry-run` output formatting (already implemented)
- [x] Add `--verbose` flag for debugging
- [x] Performance optimization (if needed) — not required
- [x] Final test pass and coverage check

**Deliverables**:
- Updated documentation (TEMPLATING_QUICKGUIDE.md section 9)
- Polished CLI output (verbose flag, suggestions in errors)
- Release notes for v2.6.0 — pending

**Exit Criteria**:
- [x] New dev can use generator with only docs
- [x] Error messages are actionable
- [x] All tests pass, coverage ≥85% (86.95%)

---

### Dev Playground — Development Breakdown

#### Phase P1: Backend Foundation (Week 1-2)

**Objective**: FastAPI server with agent discovery and wrapping.

**Tasks**:
- [ ] Implement agent discovery logic
  - Find `*Orchestrator` classes
  - Find `build_planner()` functions
  - Handle `__main__.py` entry points
- [ ] Create `PlaygroundStateStore` protocol
- [ ] Implement `InMemoryStateStore` with session isolation
- [ ] Create agent wrapper interface
  - Adapt orchestrators to common interface
  - Hook into event_callback
- [ ] Implement FastAPI endpoints
  - `POST /chat`
  - `GET /health`
- [ ] Write unit tests for discovery and wrapping

**Deliverables**:
- `penguiflow/cli/playground.py` — Main module
- `penguiflow/cli/playground_state.py` — StateStore protocol + in-memory impl
- `penguiflow/cli/playground_wrapper.py` — Agent wrapper
- `tests/cli/test_playground_backend.py` — Backend tests

**Exit Criteria**:
- [ ] Agent discovery works on generated projects
- [ ] StateStore isolates sessions correctly
- [ ] `/chat` endpoint returns responses

---

#### Phase P2: Streaming & Events (Week 2)

**Objective**: SSE streaming for responses and events.

**Tasks**:
- [ ] Implement SSE endpoint for chat streaming
  - `GET /chat/stream`
  - Chunk events, step events, done event
- [ ] Implement SSE endpoint for planner events
  - `GET /events`
  - Real-time PlannerEvent forwarding
- [ ] Implement trajectory endpoint
  - `GET /trajectory/{trace_id}`
  - Requires StateStore
- [ ] Add session_id parameter handling
- [ ] Write streaming tests

**Deliverables**:
- SSE endpoints in playground.py
- `penguiflow/cli/playground_sse.py` — SSE utilities
- `tests/cli/test_playground_streaming.py` — Streaming tests

**Exit Criteria**:
- [ ] Streaming responses work end-to-end
- [ ] Events stream in real-time
- [ ] Trajectory retrieval works

---

#### Phase P3: Svelte UI Development (Week 3-4)

**Objective**: Build and bundle Svelte 5 frontend.

**Tasks**:
- [ ] Set up Svelte 5 project structure
- [ ] Implement Chat component
  - Query input
  - Streaming response display
  - Session history sidebar
- [ ] Implement Trajectory component
  - Step timeline
  - Collapsible JSON for args/results
  - Latency badges
  - Reflection scores
- [ ] Implement Events component
  - Real-time event stream
  - Filter dropdown
  - Latency highlighting
- [ ] Implement Config panel (read-only)
- [ ] Style with minimal CSS (no heavy frameworks)
- [ ] Build and bundle as static assets
- [ ] Integrate with FastAPI StaticFiles

**Deliverables**:
- `penguiflow/cli/playground_ui/` — Svelte source
- `penguiflow/cli/playground_ui/dist/` — Pre-built assets
- Build script for regenerating assets

**Exit Criteria**:
- [ ] UI loads without npm for end user
- [ ] All panels functional
- [ ] Responsive on desktop

---

#### Phase P4: CLI Integration & Polish (Week 4)

**Objective**: Wire CLI command and polish experience.

**Tasks**:
- [ ] Implement `penguiflow dev` CLI command
  - Project directory argument
  - `--port` flag
  - `--no-browser` flag
- [ ] Auto-open browser on start
- [ ] Add startup banner with URLs
- [ ] Handle graceful shutdown
- [ ] Add hot-reload hint (manual refresh)
- [ ] Write end-to-end tests
- [ ] Documentation

**Deliverables**:
- CLI command in `penguiflow/cli/main.py`
- Updated documentation
- Release notes for v2.6.1/v2.6.2

**Exit Criteria**:
- [ ] `penguiflow dev .` works on any project
- [ ] Browser opens automatically
- [ ] Clean shutdown on Ctrl+C

---

## Implementation Checklist

### Pre-Development
- [ ] Create feature branch `v2.6/platform-wave`
- [ ] Set up CI for new modules
- [ ] Review spec format with team

### Generator (v2.6.0)
- [x] G1: Spec schema & validation
- [x] G2: Tool & planner generation
- [x] G3: Flow & test generation
- [x] G4: Documentation & polish
- [ ] Tag v2.6.0 release

### Playground (v2.6.1-v2.6.2)
- [ ] P1: Backend foundation
- [ ] P2: Streaming & events
- [ ] P3: Svelte UI development
- [ ] P4: CLI integration & polish
- [ ] Tag v2.6.1 (backend) and v2.6.2 (UI) releases

### Post-Release
- [ ] Gather feedback from early adopters
- [ ] Plan Golden Templates for v2.6.5+
- [ ] Identify v2.7 enhancements (branching flows, persistent state, etc.)
