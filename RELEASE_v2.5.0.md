# Release v2.5.0 - CLI Scaffolding & Extended Planner

## Major Features

### CLI Scaffolding System - `penguiflow new`

Complete project scaffolding system with 9 production-ready templates and enhancement flags.

**Tier 1 - Core Templates:**
- `minimal` — Lightweight ReactPlanner without memory (quick prototyping)
- `react` — Full ReactPlanner with memory integration and tool catalog
- `parallel` — Multi-shard parallel execution with automatic join handling

**Tier 2 - Service Templates:**
- `lighthouse` — Query router with domain classification and tool selection
- `wayfinder` — Goal-oriented planner with checkpoint persistence
- `analyst` — Data analysis agent with visualization support

**Tier 3 - Enterprise Template:**
- `enterprise` — Multi-tenant setup with RBAC, quotas, audit trails, and compliance hooks

**Additional Templates:**
- `flow` — Traditional PenguiFlow with typed nodes and message passing
- `controller` — Dynamic multi-hop agent loop with working memory

**Enhancement Flags:**
- `--with-streaming` — Adds SSE streaming infrastructure with `format_sse_event`
- `--with-hitl` — Human-in-the-loop approval gates with `ctx.pause()`
- `--with-a2a` — Agent-to-Agent protocol server with FastAPI surface
- `--no-memory` — Disables memory server integration for stateless operation

**Usage:**
```bash
penguiflow new my-agent --template react
penguiflow new my-agent --template enterprise --with-streaming --with-hitl
penguiflow new my-agent --template parallel --with-a2a
```

### Extended ReactPlanner (v2.4 Refinements)

**Context Separation:**
- Explicit `llm_context` vs `tool_context` split for cleaner data flow
- `llm_context` must be JSON-serializable (fail-fast validation)
- `tool_context` carries runtime state (memory server, external clients)

**ToolContext Protocol:**
- Typed tool execution with `ctx.pause()`, `ctx.emit_chunk()`, `ctx.tool_context`
- Full IDE autocompletion support via protocol definition
- Consistent interface across all template types

**Parallel Execution Improvements:**
- Explicit join injection for parallel plans
- Auto-populated join args from shard results
- New `examples/react_parallel_join` demonstrating patterns

## New Documentation

### TEMPLATING_QUICKGUIDE.md
Comprehensive ~1,500 line guide covering:
- All 9 templates with architecture diagrams
- Enhancement flag combinations and use cases
- Tool implementation patterns
- Memory server integration
- Telemetry and observability configuration
- Testing patterns for each template tier
- Best practices and anti-patterns

### Integration Guides
- `REACT_PLANNER_INTEGRATION_GUIDE.md` — Complete ReactPlanner setup
- `docs/MIGRATION_V24.md` — Migration guide for v2.4 context changes

## New Files

**CLI Implementation:**
- `penguiflow/cli/new.py` — `penguiflow new` command implementation
- `penguiflow/cli/main.py` — Updated CLI entrypoint

**Templates:**
- `penguiflow/templates/new/minimal/` — Minimal template files
- `penguiflow/templates/new/react/` — React template files
- `penguiflow/templates/new/parallel/` — Parallel template files
- `penguiflow/templates/new/flow/` — Flow template files
- `penguiflow/templates/new/controller/` — Controller template files
- `penguiflow/templates/new/lighthouse/` — Lighthouse template files
- `penguiflow/templates/new/wayfinder/` — Wayfinder template files
- `penguiflow/templates/new/analyst/` — Analyst template files
- `penguiflow/templates/new/enterprise/` — Enterprise template files

## API Changes

### New CLI Commands
```bash
penguiflow new <name> [OPTIONS]
  --template TEXT      Template name (default: react)
  --with-streaming     Add SSE streaming support
  --with-hitl          Add human-in-the-loop gates
  --with-a2a           Add A2A server surface
  --no-memory          Disable memory integration
  --output-dir PATH    Custom output directory
```

### New Dependencies (Optional)
```toml
[project.optional-dependencies]
cli = [
    "jinja2>=3.1",  # Template rendering
]
```

## Breaking Changes

**None** - All changes are additive. Existing PenguiFlow v2.4.x code continues to work unchanged.

## Migration Guide

No migration needed. To use CLI scaffolding:

1. Install CLI extras:
   ```bash
   pip install penguiflow[cli]
   ```

2. Scaffold a new project:
   ```bash
   penguiflow new my-agent --template react
   cd my-agent
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

4. Run the demo:
   ```bash
   uv sync
   uv run python demo.py
   ```

## Template Comparison Matrix

| Template | Memory | Tools | Streaming | Parallel | HITL | A2A | Multi-tenant |
|----------|--------|-------|-----------|----------|------|-----|--------------|
| minimal | - | + | flag | - | flag | flag | - |
| react | + | + | flag | - | flag | flag | - |
| parallel | + | + | flag | + | flag | flag | - |
| flow | - | - | flag | - | flag | flag | - |
| controller | - | - | flag | - | flag | flag | - |
| lighthouse | + | + | flag | - | flag | flag | - |
| wayfinder | + | + | flag | - | + | flag | - |
| analyst | + | + | flag | + | flag | flag | - |
| enterprise | + | + | + | + | + | + | + |

## Resources

- **Templating Guide**: [TEMPLATING_QUICKGUIDE.md](TEMPLATING_QUICKGUIDE.md)
- **Planner Integration**: [REACT_PLANNER_INTEGRATION_GUIDE.md](REACT_PLANNER_INTEGRATION_GUIDE.md)
- **Migration**: [docs/MIGRATION_V24.md](docs/MIGRATION_V24.md)
- **Examples**: `examples/react_*/`

## Stats

- **New Templates**: 9 production-ready project scaffolds
- **Enhancement Flags**: 4 composable feature toggles
- **Documentation**: ~1,500 lines in TEMPLATING_QUICKGUIDE.md
- **Test Coverage**: 86% (above 85% threshold)

---

**Full Changelog**: v2.4.0...v2.5.0
