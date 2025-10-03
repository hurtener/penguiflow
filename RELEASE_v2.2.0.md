# Release v2.2.0 - React Planner & Documentation Overhaul

## Major Features

### React Planner - LLM-Driven Orchestration
Complete implementation of the ReAct (Reasoning + Acting) pattern for autonomous multi-step workflows.

**Core Components:**
- `ReactPlanner` - Main orchestrator with LiteLLM integration
- `PlannerAction` - LLM decision model (sequential/parallel execution)
- `PlannerFinish` / `PlannerPause` - Terminal states for completion and human-in-the-loop
- `Trajectory` / `TrajectoryStep` - Execution history tracking
- `@tool` decorator - Metadata-rich tool registration for LLM consumption
- `NodeSpec` - Tool catalog with JSON schema generation
- `build_catalog()` - Automatic catalog construction from nodes + registry

**Key Capabilities:**
- ✅ **Autonomous Tool Selection** - LLM decides which tool to execute based on context
- ✅ **Parallel Execution** - Built-in fan-out with automatic join and result merging
- ✅ **Pause/Resume Workflows** - Human approval gates with durable state persistence
- ✅ **Constraint Enforcement** - Hop budgets, deadlines, and token budget summarization
- ✅ **Adaptive Replanning** - Structured error feedback with suggestion propagation
- ✅ **JSON Schema Validation** - Auto-generated schemas from Pydantic models
- ✅ **LiteLLM Integration** - Support for 100+ models (OpenAI, Anthropic, Azure, etc.)
- ✅ **Planning Hints** - Developer guidance for safe execution (ordering, parallelism, constraints)
- ✅ **JSON Repair Loop** - Automatic retry for malformed LLM responses

**New Modules:**
- `penguiflow/planner/react.py` (1,340 lines) - ReactPlanner implementation
- `penguiflow/planner/prompts.py` (244 lines) - Prompt engineering utilities
- `penguiflow/catalog.py` (147 lines) - Tool catalog and @tool decorator

**Example Files:**
- `examples/react_minimal/` - Basic sequential flow with stub LLM
- `examples/react_parallel/` - Parallel fan-out with join node
- `examples/react_replan/` - Adaptive replanning after failures
- `examples/react_pause_resume/` - Pause/resume with planning hints

**Test Coverage:**
- `tests/test_react_planner.py` (846 lines, 25+ tests) - Comprehensive test suite
- `tests/test_planner_prompts.py` (56 lines) - Prompt rendering tests

## 📚 Documentation Overhaul

### Manual Section 19 - React Planner (Complete Reference)
Added comprehensive 16-subsection documentation covering all React Planner features:

**Sections Added:**
1. **19.1** - What is React Planner? (comparison table, architecture diagram)
2. **19.2** - Core Concepts (PlannerAction, Trajectory, NodeSpec, terminal states)
3. **19.3** - Basic Usage (minimal working example)
4. **19.4** - Tool Definition and Registration (@tool decorator, catalog building)
5. **19.5** - JSON Schema Generation (Deep Dive) - schema design best practices
6. **19.6** - LiteLLM Integration - model configuration, providers, env vars
7. **19.7** - Parallel Execution - fan-out, join nodes, auto-populated args
8. **19.8** - Pause and Resume - approval workflows, StateStore integration
9. **19.9** - Constraint Enforcement - hop budgets, deadlines, token budgets
10. **19.10** - Error Handling and Adaptive Replanning - structured error feedback
11. **19.11** - Planning Hints - complete reference (ordering, parallelism, filtering)
12. **19.12** - Decision Matrix - vs. Controller Loops, Routing Patterns, Playbooks
13. **19.13** - Testing React Planners - StubClient patterns, deterministic testing
14. **19.14** - Common Mistakes - 8 pitfalls with fixes
15. **19.15** - Complete Example - 100+ line multi-shard retrieval with approval
16. **19.16** - See Also - cross-references to related sections

### llm.txt - AI Assistant Reference Updates
Integrated React Planner across all relevant sections:

**Section A (Quick Overview):**
- Added "LLM-driven orchestration with React Planner" to key use cases

**Section D (Pattern Decision Matrix):**
- Added `ReactPlanner` row to decision matrix table

**Section E (Common Code Snippets):**
- **Snippet 9** - React Planner basic usage
- **Snippet 10** - Parallel execution with join nodes
- **Snippet 11** - Pause/resume for approval workflows

**Section G (Decision Trees):**
- Added "When to Use React Planner?" decision tree
- Comparison vs Controller Loops with practical guidance

**Section H (Common Mistakes):**
- **Mistake 11** - Missing @tool decorator
- **Mistake 12** - Not handling PlannerPause
- **Mistake 13** - Forgetting to register tool types

**Section J (Cross-Reference Map):**
- 3 new task index entries (LLM-driven agent, autonomous tool selection, approval workflows)
- Added Section 19 to manual quick links
- 4 example files added to map
- 2 test files added to map

## 🔧 API Changes

### New Public Exports (`penguiflow/__init__.py`)
```python
from penguiflow import (
    ReactPlanner,        # Main planner class
    PlannerAction,       # LLM decision model
    PlannerFinish,       # Completion state
    Trajectory,          # Execution history
    TrajectoryStep,      # Single step record
    build_catalog,       # Catalog builder
    tool,                # Tool decorator
    NodeSpec,            # Tool metadata
    SideEffect,          # Type alias
)
```

### New Dependencies (Optional)
```toml
[project.optional-dependencies]
planner = [
    "litellm>=1.77.3",  # LLM integration
]
```

Install with: `pip install penguiflow[planner]`

## 📈 Improvements

### Type Safety
- Full Pydantic v2 integration for all planner models
- Automatic JSON schema generation from type hints
- Runtime validation with detailed error messages

### Developer Experience
- `@tool` decorator for clean tool definition
- Auto-populated join args for parallel execution
- StubClient for deterministic testing
- Clear error messages with replanning suggestions

### Observability
- FlowEvent integration for planner operations
- Trajectory tracking for full execution history
- Metadata propagation (parallel_success_count, parallel_failures, etc.)

## 🐛 Bug Fixes

None - this is a new feature release with no breaking changes.

## ⚠️ Breaking Changes

**None** - All changes are additive. Existing PenguiFlow v2.1.x code continues to work unchanged.

## 📦 Migration Guide

No migration needed. To use React Planner:

1. Install planner extras:
   ```bash
   pip install penguiflow[planner]
   ```

2. Set LLM API key:
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

3. Define tools and run planner:
   ```python
   from penguiflow import ReactPlanner, tool, build_catalog

   @tool(desc="Your tool description")
   async def my_tool(args: InputModel, ctx) -> OutputModel:
       return OutputModel(...)

   planner = ReactPlanner(llm="gpt-4", catalog=build_catalog(nodes, registry))
   result = await planner.run("Your query")
   ```

## 🔗 Resources

- **Documentation**: See `manual.md` Section 19
- **Examples**: `examples/react_*/`
- **Tests**: `tests/test_react_planner.py`
- **AI Reference**: `llm.txt` (updated across 6 sections)

## 📊 Stats

- **Lines Added**: ~2,500+ (implementation + docs)
- **New Files**: 7 (3 implementation, 4 examples)
- **Test Coverage**: 25+ tests covering all React Planner features
- **Documentation**: 16 new subsections in manual.md

---

**Full Changelog**: v2.1.0...v2.2.0
