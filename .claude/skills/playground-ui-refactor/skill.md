# Playground UI Refactor Skill

## Overview

The penguiflow CLI includes a playground UI (`penguiflow/cli/playground_ui/`) - a Svelte 5 web application for testing agent logic and integrations. We are refactoring this from a monolithic 900-line `App.svelte` into a well-structured component architecture.

## Current State

**Location**: `penguiflow/cli/playground_ui/src/`

**Problem**: Single `App.svelte` file contains:
- 7 TypeScript type definitions
- 15+ reactive state variables
- 20+ functions (API, EventSource, utilities)
- All UI markup (~300 lines of Svelte template)
- No tests

**Tech Stack**:
- Svelte 5.43.8 (uses `$state`, `$derived`, `$effect` runes)
- Vite 7.2.4
- marked (markdown rendering)

## Target Architecture

See `docs/PLAYGROUND_UI_REFACTOR_PLAN.md` for the complete plan.

**Directory Structure**:
```
src/lib/
├── types/           # TypeScript type definitions
├── stores/          # Svelte 5 rune-based stores (.svelte.ts)
├── services/        # API calls, EventSource managers
├── utils/           # Pure utility functions
└── components/
    ├── ui/              # Reusable primitives (Pill, Tabs, Empty)
    ├── layout/          # Page, Card, Column
    ├── sidebar-left/    # ProjectCard, SpecCard, GeneratorCard
    ├── center/          # ChatCard, SetupTab, TrajectoryCard
    └── sidebar-right/   # EventsCard, ConfigCard
```

## Refactoring Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Extract Types & Utilities | Pending |
| 2 | Create Stores | Pending |
| 3 | Extract Services | Pending |
| 4 | Component Extraction | Pending |
| 5 | CSS Migration | Pending |
| 6 | Testing Infrastructure | Pending |

## UI Functionality (Must Preserve)

### Three-Column Layout
- **Left**: Agent info, Spec YAML viewer, Generator controls
- **Center**: Chat interface with Setup tab, Execution trajectory
- **Right**: Planner events stream, Config/Services/Tools catalog

### Chat Features
- Send messages via EventSource streaming
- Display user/agent message bubbles
- Collapsible "Thinking" panel for observations
- Streaming text with typing indicator
- HITL pause state with auth links
- Auto-scroll on new messages

### Setup Tab
- Session ID management (with "New" button)
- Tenant ID, User ID inputs
- Tool Context JSON textarea
- LLM Context JSON textarea
- Validation errors display

### Spec & Generator
- YAML content display
- Validation status (pending/valid/error)
- Validate and Generate buttons
- 7-step progress stepper

### Events Panel
- Real-time event stream via EventSource
- Pause/resume toggle
- Event type filter dropdown
- Artifact streams display

### Trajectory
- Timeline visualization of execution steps
- Step name, thought, latency
- Expandable args/result JSON

## Key Types

```typescript
type ChatMessage = {
  id: string;
  role: 'user' | 'agent';
  text: string;
  observations?: string;
  showObservations?: boolean;
  isStreaming?: boolean;
  isThinking?: boolean;
  pause?: { reason?: string; payload?: Record<string, unknown>; resume_token?: string };
  ts: number;
};

type TimelineStep = {
  id: string;
  name: string;
  thought?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  latencyMs?: number;
  status?: 'ok' | 'error';
};

type PlannerEventPayload = {
  id: string;
  event: string;
  node?: string;
  thought?: string;
  latency_ms?: number;
  // ... more fields
};
```

## API Endpoints Used

- `GET /ui/meta` - Agent metadata, config, services, tools
- `GET /ui/spec` - Spec YAML content and validation
- `POST /ui/validate` - Validate spec
- `POST /ui/generate` - Generate project from spec
- `GET /chat/stream` - SSE chat endpoint
- `GET /events` - SSE event follow endpoint
- `GET /trajectory/:trace_id` - Fetch execution trajectory

## Store Pattern (Svelte 5)

```typescript
function createStore() {
  let value = $state<Type>(initial);

  return {
    get value() { return value; },
    set value(v) { value = v; },
    get computed() { return derive(value); },
    action() { /* mutate value */ }
  };
}

export const store = createStore();
```

## Testing Stack

- **Vitest** - Unit tests
- **@testing-library/svelte** - Component tests
- **Playwright** - E2E tests

## When to Use This Skill

Use this skill when:
- Working on `penguiflow/cli/playground_ui/`
- Implementing any phase of the refactor
- Adding features to the playground UI
- Debugging UI issues
- Writing tests for UI components

## Key Files to Read

1. `docs/PLAYGROUND_UI_REFACTOR_PLAN.md` - Complete refactoring plan
2. `penguiflow/cli/playground_ui/src/App.svelte` - Current implementation
3. `penguiflow/cli/playground_ui/src/app.css` - Current styles
4. `penguiflow/cli/playground_ui/package.json` - Dependencies

## Commands

```bash
# Navigate to playground UI
cd penguiflow/cli/playground_ui

# Install dependencies
npm install

# Run dev server
npm run dev

# Build
npm run build

# Run tests (after Phase 6)
npm run test
npm run test:e2e
```
