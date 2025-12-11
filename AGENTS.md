# PenguiFlow Guide

## Snapshot
- Built the entire core library in under 20 days; no downstream consumers right now, so risk is mostly examples/back-compat.
- Baseline: v2.3 ReAct planner is in place on top of the v2.1 distributed/A2A core.
- **In Progress**: v2.6.5+ ToolNode v2 Phase 4 — CLI Integration (optional).
- **Just Completed**: ToolNode v2 Phases 1-3 (Foundation, Multi-Protocol + Auth, Documentation).
- Completed: v2.6 Streaming support, v2.5 CLI scaffolding, v2.4 API refinement.

## Completed
- v2.1 Distributed & A2A: StateStore, MessageBus, RemoteTransport, A2A server adapter; streaming/cancel propagation; telemetry/durability hooks.
- v2.2 Adjustments and polishing of core API.
- v2.3 ReAct planner baseline: JSON-only LiteLLM loop + dspy adaptor for other llm providers, typed catalog from nodes/recipes, validation/repair, pause/resume with summaries, streaming/cancel propagation across nodes, parallel fan-out + join, structured trajectory logging.
- v2.4 API Refinement: Context split (llm_context vs tool_context), ToolContext protocol, explicit join.inject mapping, modularized planner, doc/example parity.
- v2.5 CLI Scaffolding: Full `penguiflow new` command with 9 project templates, enhancement flags (--with-streaming, --with-hitl, --with-a2a, --no-memory), adaptive re-plan, token-aware trajectory compression, parallel fan-out + joins.
- v2.6 Streaming Support: `JSONLLMClient` protocol with `stream` and `on_stream_chunk` parameters, all templates updated, spec generation for tool documentation, baseline Playground UI for quick verification of spec agents.

## v2.6.5+ ToolNode v2 (next target)
**Plan:** See `docs/proposals/TOOLNODE_V2_PLAN.md`

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
- `fastmcp>=2.13.0` — MCP client/server, eliminates ~800 lines of JSON-RPC code
- `utcp>=1.1.0`, `utcp-http>=1.0.0` — Multi-protocol abstraction, OpenAPI discovery
- `tenacity>=9.0.0` — Battle-tested async retry with proper cancellation handling
- `aiohttp>=3.9.0` — OAuth callback HTTP client

**Implementation Phases:**
1. ✅ Foundation: Add deps, create `penguiflow/tools/` package, implement `ExternalToolConfig`, `ToolNode` with MCP support, error classification + retry wiring
2. ✅ Multi-Protocol + Auth: UTCP support (manual_url/base_url modes), `OAuthManager` + `TokenStore`, wire OAuth to HITL pause/resume
3. ✅ Documentation + Polish: Presets for popular MCP servers, docs (`docs/tools/statestore-guide.md`, `concurrency-guide.md`, `configuration-guide.md`), examples
4. 🔲 CLI Integration (optional): `penguiflow tools list/connect` commands, template integration, Playground UI HITL

**File Structure (~760 LOC new code):**
```
penguiflow/tools/
├── __init__.py      # Exports: ToolNode, ExternalToolConfig, etc.
├── config.py        # Configuration models (~100 lines)
├── node.py          # ToolNode implementation (~350 lines)
├── auth.py          # OAuthManager + TokenStore (~150 lines)
├── errors.py        # Error types + categories (~80 lines)
├── adapters.py      # Transport-aware error adapters (~80 lines)
└── presets.py       # Popular MCP server configs (~50 lines)
```

**Deferred to v2.7+:**
- MCP Resources/Prompts (focus on tools first)
- MCP Sampling (rare use case)
- Expose Penguiflow as MCP server (A2A feature) and update of A2A protocol adherence
- Built-in Postgres StateStore (user should implement) adaptor

## Non-Goals
- No heavy dependencies; remain asyncio + Pydantic v2; no built-in endpoints/UI/storage. Each new dependency has to go to its dependency group (e.g. penguiflow[planner]).

## Risks & Mitigations
- Refactor risk: add snapshot tests for prompts/trajectory serialization before and after module moves.
- Join change risk: warn on implicit injection; migrate examples in lockstep; document prompt changes.
- Back-compat risk: re-export internals in planner.__init__ and react.py shim; add import tests; keep deprecation warnings.
- With no external dependents, speed is fine; primary break surface is examples—update them alongside code.

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
