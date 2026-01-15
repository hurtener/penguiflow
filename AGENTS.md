# PenguiFlow Guide

## Snapshot

## Completed
- v2.1 Distributed & A2A: StateStore, MessageBus, RemoteTransport, A2A server adapter; streaming/cancel propagation; telemetry/durability hooks.
- v2.2 Adjustments and polishing of core API.
- v2.3 ReAct planner baseline: JSON-only LiteLLM loop + dspy adaptor for other llm providers, typed catalog from nodes/recipes, validation/repair, pause/resume with summaries, streaming/cancel propagation across nodes, parallel fan-out + join, structured trajectory logging.
- v2.4 API Refinement: Context split (llm_context vs tool_context), ToolContext protocol, explicit join.inject mapping, modularized planner, doc/example parity.
- v2.5 CLI Scaffolding: Full `penguiflow new` command with 9 project templates, enhancement flags (--with-streaming, --with-hitl, --with-a2a, --no-memory), adaptive re-plan, token-aware trajectory compression, parallel fan-out + joins.
- v2.6 Streaming Support: `JSONLLMClient` protocol with `stream` and `on_stream_chunk` parameters, all templates updated, spec generation for tool documentation, baseline Playground UI for quick verification of spec agents.
- v2.7 Platform Features: Interactive Playground (`penguiflow dev`), External Tool Integration (ToolNode for MCP/UTCP/HTTP with auth and resilience), Short-Term Memory with multi-tenant isolation and rolling summary strategies.
- V2.8
- V2.9

## Future: 


**Core Value Proposition:**
- Zero wrapper code for MCP ecosystem (230+ servers) via FastMCP
- Any REST API with OpenAPI/UTCP auto-discovery
- User-level OAuth through existing HITL primitives
- Production-ready resilience (retries, timeouts, circuit breakers)

**Design Principles:**
- FastMCP for MCP: All MCP server communication goes through FastMCP (handles framing, OAuth, transport detection)
- UTCP for everything else: HTTP APIs, CLI tools, WebSocket - UTCP provides unified multi-protocol access
- No bridge mixing: Each library handles its native protocols directly

**New Dependencies (planner group):**


**Implementation Phases:**


**File Structure ():**
```

```

**Deferred to v2.7+:**


## Non-Goals


## Risks & Mitigations


## Coverage Policy
Target: >=85% line coverage (hard minimum in CI). Every new feature needs at least one negative/error-path test. CI produces coverage XML and uploads to Codecov/Coveralls; badges track trends.

## CI/CD Policy
Matrix:
- Python: 3.11, 3.12, 3.13
- OS: Ubuntu

Checks enforced before merge:
- Ruff (lint)
- Mypy (types)
- Pytest with coverage (>=85%)

Artifacts:
- Store .coverage.xml
- Badges: Add CI status + coverage badge in README.

Optional:
- Performance benchmarks (pytest-benchmark)
- Upload coverage to Codecov/Coveralls

## Examples Policy
- Each example must be runnable directly: `uv run python examples/<name>/flow.py`
- Include a short README.md inside the example folder.
- Example must cover at least one integration test scenario.
- Examples should demonstrate real usage but remain domain-agnostic.
