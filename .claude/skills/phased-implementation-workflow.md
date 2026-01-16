# Skill: Phased Implementation Workflow

> **Scope:** Multi-phase implementation workflow for pengui_canvas using subagents
> **Applies to:** Implementing features in test_generation/pengui_canvas/pengui_canvas

---

## Workflow Overview

This skill defines the procedure for implementing pengui_canvas phases using subagents, ensuring consistency, context reuse, and adherence to the penguiflow library patterns.

### Phase Execution Process

1. **Select Phase Group**: Choose a batch of phases to implement (e.g., phases 8-16)
2. **Sequential Execution**: Run phases one at a time via subagents
3. **Documentation Sync**: After each phase, update architecture docs
4. **Group Sweep**: After completing the group, perform adherence sweep
5. **Summary Report**: Generate summary before moving to next group

---

## Context Handoff to Subagents

### Essential Context Files (Must Read)

Subagents MUST be provided with or read these context files:

```
# Pengui Canvas Context
test_generation/pengui_canvas/pengui_canvas/docs/PHASED_IMPLEMENTATION_PLAN_V01.md
test_generation/pengui_canvas/pengui_canvas/docs/architecture/SYSTEM_OVERVIEW.md
test_generation/pengui_canvas/pengui_canvas/docs/architecture/SUBSYSTEMS.md
test_generation/pengui_canvas/pengui_canvas/docs/architecture/DATA_MODELS.md

# PenguiFlow Skills (Pattern Guidance)
test_generation/pengui_canvas/pengui_canvas/docs/skills/SKILL_REACT_PLANNER.md
test_generation/pengui_canvas/pengui_canvas/docs/skills/SKILL_STATESTORE.md
test_generation/pengui_canvas/pengui_canvas/docs/skills/SKILL_TEMPLATING.md
```

### PenguiFlow Reference Components

When implementing features, subagents should reference these penguiflow patterns:

| Feature Area | Reference Location | Description |
|--------------|-------------------|-------------|
| **Chat Interface** | `penguiflow/cli/playground_ui/src/lib/components/features/chat/` | Message components, input, streaming indicator |
| **SSE/Event Streaming** | `penguiflow/cli/playground_ui/src/lib/services/event-stream.ts` | EventSource management, event parsing |
| **AG-UI Provider** | `penguiflow/cli/playground_ui/src/lib/components/features/agui/AGUIProvider.svelte` | State management for streaming |
| **AG-UI Adapter** | `penguiflow/agui_adapter/penguiflow.py` | Server-side AG-UI event generation |
| **Artifact Renderers** | `penguiflow/cli/playground_ui/src/lib/renderers/` | Markdown, Code, Chart, DataGrid renderers |
| **Domain Stores** | `penguiflow/cli/playground_ui/src/lib/stores/domain/` | Chat, session, artifacts stores |
| **ReactPlanner** | `penguiflow/planner/` | Planner integration patterns |
| **StateStore** | `penguiflow/state/` | Protocol-based state persistence |

---

## Subagent Prompt Template

When launching a subagent for a phase, use this template:

```
You are implementing Phase {N}: {Phase Name} for pengui_canvas.

## Context
Read these files first:
- PHASED_IMPLEMENTATION_PLAN_V01.md (your phase requirements)
- architecture/SYSTEM_OVERVIEW.md (current architecture)
- architecture/SUBSYSTEMS.md (subsystem details)

## Phase Requirements
{Copy the specific phase deliverables from the plan}

## Pattern Guidance
For this phase, reference these penguiflow patterns:
{List relevant reference locations from table above}

## Implementation Rules
1. Follow modularization limits (backend files ≤300 lines, frontend ≤200 lines)
2. Use protocol-based stores (duck-typed, not inheritance)
3. For Svelte: use runes ($state, $derived, $effect)
4. For backend: use Pydantic v2, async SQLAlchemy
5. Write tests alongside code (≥85% coverage)

## After Implementation
1. Update architecture docs with any structural changes
2. Mark deliverables complete in PHASED_IMPLEMENTATION_PLAN_V01.md
3. Report what was implemented and any deviations
```

---

## Architecture Documentation Updates

After each phase, these docs MUST be kept in sync:

| Document | Update When |
|----------|-------------|
| `SYSTEM_OVERVIEW.md` | New subsystems, technology changes |
| `SUBSYSTEMS.md` | New components, updated data flows |
| `DATA_MODELS.md` | New models, schema changes |
| `DEPENDENCIES.md` | New package dependencies |
| Phase Plan | Mark checkboxes, add learnings |

### Documentation Update Pattern

```markdown
## Subsystem: {Name} ({Status: ✅ Complete / 🔄 In Progress})

### Phase {N} Additions
- Component X: `path/to/file.py` (~N lines)
- Component Y: `path/to/file.svelte` (~N lines)

### Data Flow
{Update mermaid diagram if needed}
```

---

## Phase Group Sweep Checklist

After completing a phase group (e.g., 8-16), perform this sweep:

### 1. Plan Adherence
- [ ] All deliverables from phases implemented
- [ ] CI gates would pass (lint, types, tests)
- [ ] Modularization limits respected

### 2. Pattern Consistency
- [ ] Protocol-based stores used (not inheritance)
- [ ] Context separation (llm_context vs tool_context) where applicable
- [ ] Svelte 5 runes used (not legacy reactive statements)
- [ ] AG-UI protocol events match penguiflow adapter format

### 3. Resource Reuse
- [ ] Existing playground_ui components referenced/adapted (not reinvented)
- [ ] SSE/streaming patterns match penguiflow's event-stream.ts
- [ ] Type definitions align with penguiflow's chat.ts types

### 4. Documentation
- [ ] Architecture docs updated and accurate
- [ ] Phase plan checkboxes marked
- [ ] Any deviations documented

---

## Drift Detection

When sweeping, look for these common drifts:

### Planner/Orchestrator Drifts
- Using direct LLM calls instead of ReactPlanner
- Missing context separation (llm_context/tool_context)
- Not using pause/resume for HITL flows
- Missing streaming callbacks

### Store Pattern Drifts
- Using inheritance instead of Protocols
- Missing async on store methods
- Not using pg0 for local development
- Missing JSONB for flexible metadata

### Frontend Drifts
- Using legacy `$:` reactive statements instead of runes
- Not using shadcn-svelte components
- Custom SSE implementation instead of event-stream pattern
- Missing AG-UI event types

### AG-UI Adapter Drifts
- Different event type names than PenguiFlowAdapter emits
- Missing state_update events
- Different pause/resume flow than penguiflow

---

## Example Usage

```
# Starting implementation of phases 8-16
1. Read all context (plan, architecture, skills)
2. Write this skill (done!)
3. For each phase 8-16:
   a. Launch subagent with template above
   b. Review implementation
   c. Update architecture docs
4. Perform group sweep with checklist
5. Generate summary report
```

---

## Related Resources

- **Background Tasks Agent Pattern**: `test_generation/background-tasks-agent-test/`
- **PenguiFlow CLAUDE.md**: `/CLAUDE.md` (project coding standards)
- **Svelte MCP**: Use for Svelte 5 syntax validation
- **Serena Memories**: Store learnings from each phase group
