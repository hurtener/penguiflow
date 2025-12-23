# Playground UI Refactoring Plan

> **Approach**: Big Bang (phased execution)
> **Testing**: Vitest + @testing-library/svelte (unit) + Playwright (E2E)
> **State Management**: Svelte 5 native runes
> **Styling**: Custom CSS with CSS variables (no utility frameworks)

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Target Architecture](#2-target-architecture)
3. [Phase 1: Extract Types & Utilities](#3-phase-1-extract-types--utilities)
4. [Phase 2: Create Stores](#4-phase-2-create-stores)
5. [Phase 3: Extract Services](#5-phase-3-extract-services)
6. [Phase 4: Component Extraction](#6-phase-4-component-extraction)
7. [Phase 5: CSS Migration](#7-phase-5-css-migration)
8. [Phase 6: Testing Infrastructure](#8-phase-6-testing-infrastructure)
9. [Phase 7: Svelte 5 Slot Migration](#9-phase-7-svelte-5-slot-migration)
10. [Implementation Checklists](#10-implementation-checklists)
11. [Validation Criteria](#11-validation-criteria)

---

## 1. Current State Analysis

### 1.1 File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `src/App.svelte` | ~900 | Everything: types, state, logic, UI |
| `src/app.css` | ~700 | Global styles |
| `src/main.js` | ~10 | Entry point |
| `src/lib/Counter.svelte` | ~20 | Unused template file |

### 1.2 Identified Concerns in App.svelte

#### Types (Lines ~1-70)
```
- ChatMessage
- TimelineStep
- PlannerEventPayload
- SpecError
- ServiceInfo
- ToolInfo
- ConfigItem
- TrajectoryStep (interface inside function)
```

#### State Variables (Lines ~70-130)
```
Session:      sessionId, activeTraceId, isSending
Chat:         chatInput, chatMessages
Events:       plannerEvents, eventFilter, pauseEvents, artifactStreams
Timeline:     timeline
Agent:        agentMeta, plannerConfig, services, catalog
Spec:         specContent, specValid, specErrors, validationStatus
Setup:        setupTenantId, setupUserId, setupToolContextRaw, setupLlmContextRaw, setupError
UI:           centerTab, chatBodyEl
EventSources: chatEventSource, followEventSource
```

#### Functions (Lines ~130-600)
```
Utilities:
  - formatTime(ts)
  - randomId()
  - safeParse(raw)
  - parseJsonObject(raw, options)
  - renderMarkdown(text)

API Calls:
  - loadMeta()
  - loadSpec()
  - validateSpec()
  - generateProject()
  - fetchTrajectory(traceId, session)

EventSource Management:
  - resetChatStream()
  - resetFollowStream()
  - sendChat()              // ~200 lines, most complex
  - startEventFollow()

Data Parsing:
  - parseTrajectory(payload)
```

#### UI Sections (Lines ~600-900)
```
Left Column:
  - Project Card (agent meta, stats)
  - Spec Card (YAML viewer, errors)
  - Generator Card (validate/generate buttons, stepper)

Center Column:
  - Chat Card
    - Header (agent pill)
    - Tabs (Chat/Setup)
    - Setup Tab (form fields)
    - Chat Body (messages)
    - Chat Input (textarea + send)
  - Trajectory Card (timeline)

Right Column:
  - Events Card (event list, filter)
  - Config Card
    - Planner Config (tile grid)
    - Services (status list)
    - Tool Catalog (tool list)
    - Artifact Streams (when present)
```

### 1.3 Problems Summary

| Problem | Impact | Solution |
|---------|--------|----------|
| 900-line monolith | Hard to navigate, modify | Split into ~40 focused files |
| Types inline | No reuse, no separate testing | Extract to `lib/types/` |
| State scattered | Hard to track data flow | Centralize in stores |
| API logic mixed with UI | Untestable | Extract to services |
| `sendChat()` 200 lines | Complex, error-prone | Break into handler classes |
| Global CSS | Style conflicts, no scoping | Component-scoped + CSS vars |
| No tests | Regressions undetected | Add unit + E2E tests |

---

## 2. Target Architecture

### 2.1 Directory Structure

```
src/
├── lib/
│   ├── types/
│   │   ├── index.ts              # Re-exports all types
│   │   ├── chat.ts               # ChatMessage, PlannerEventPayload
│   │   ├── trajectory.ts         # TimelineStep, TrajectoryStep
│   │   ├── spec.ts               # SpecError, ValidationResult
│   │   └── meta.ts               # AgentMeta, ServiceInfo, ToolInfo, ConfigItem
│   │
│   ├── stores/
│   │   ├── index.ts              # Re-exports all stores
│   │   ├── session.svelte.ts     # sessionId, activeTraceId, isSending
│   │   ├── chat.svelte.ts        # messages, input
│   │   ├── events.svelte.ts      # plannerEvents, eventFilter, pauseEvents
│   │   ├── timeline.svelte.ts    # timeline, artifactStreams
│   │   ├── agent.svelte.ts       # agentMeta, plannerConfig, services, catalog
│   │   ├── spec.svelte.ts        # specContent, specValid, specErrors
│   │   └── setup.svelte.ts       # tenantId, userId, contexts, error
│   │
│   ├── services/
│   │   ├── index.ts              # Re-exports
│   │   ├── api.ts                # REST API calls
│   │   ├── chat-stream.ts        # Chat EventSource manager
│   │   ├── event-stream.ts       # Follow EventSource manager
│   │   ├── stream-handlers.ts    # Event handler logic (extracted from sendChat)
│   │   └── markdown.ts           # Marked configuration
│   │
│   ├── utils/
│   │   ├── index.ts              # Re-exports
│   │   ├── format.ts             # formatTime, randomId
│   │   ├── json.ts               # safeParse, parseJsonObject
│   │   └── constants.ts          # ANSWER_GATE_SENTINEL, etc.
│   │
│   └── components/
│       ├── layout/
│       │   ├── Page.svelte
│       │   ├── Card.svelte
│       │   └── Column.svelte
│       │
│       ├── sidebar-left/
│       │   ├── LeftSidebar.svelte
│       │   ├── ProjectCard.svelte
│       │   ├── SpecCard.svelte
│       │   ├── GeneratorCard.svelte
│       │   └── GeneratorStepper.svelte
│       │
│       ├── center/
│       │   ├── CenterColumn.svelte
│       │   │
│       │   ├── chat/
│       │   │   ├── ChatCard.svelte
│       │   │   ├── ChatHeader.svelte
│       │   │   ├── ChatBody.svelte
│       │   │   ├── ChatInput.svelte
│       │   │   ├── Message.svelte
│       │   │   ├── ThinkingPanel.svelte
│       │   │   ├── PauseCard.svelte
│       │   │   └── TypingIndicator.svelte
│       │   │
│       │   ├── setup/
│       │   │   ├── SetupTab.svelte
│       │   │   └── SetupField.svelte
│       │   │
│       │   └── trajectory/
│       │       ├── TrajectoryCard.svelte
│       │       ├── Timeline.svelte
│       │       ├── TimelineItem.svelte
│       │       └── StepDetails.svelte
│       │
│       ├── sidebar-right/
│       │   ├── RightSidebar.svelte
│       │   │
│       │   ├── events/
│       │   │   ├── EventsCard.svelte
│       │   │   ├── EventsHeader.svelte
│       │   │   ├── EventsBody.svelte
│       │   │   └── EventRow.svelte
│       │   │
│       │   ├── config/
│       │   │   ├── ConfigCard.svelte
│       │   │   ├── PlannerConfigSection.svelte
│       │   │   ├── ServicesSection.svelte
│       │   │   ├── ToolCatalogSection.svelte
│       │   │   └── ServiceRow.svelte
│       │   │
│       │   └── artifacts/
│       │       └── ArtifactStreams.svelte
│       │
│       └── ui/
│           ├── Pill.svelte
│           ├── Tabs.svelte
│           ├── Tab.svelte
│           ├── Empty.svelte
│           ├── ErrorList.svelte
│           ├── CodeBlock.svelte
│           ├── StatusDot.svelte
│           └── IconButton.svelte
│
├── App.svelte                    # Thin orchestrator (~50 lines)
├── app.css                       # CSS variables + reset only
└── main.js                       # Entry point (unchanged)

tests/
├── unit/
│   ├── utils/
│   │   ├── format.test.ts
│   │   └── json.test.ts
│   ├── stores/
│   │   ├── session.test.ts
│   │   ├── chat.test.ts
│   │   └── ...
│   ├── services/
│   │   ├── api.test.ts
│   │   └── stream-handlers.test.ts
│   └── components/
│       ├── ui/
│       │   ├── Pill.test.ts
│       │   └── ...
│       └── ...
│
└── e2e/
    ├── chat-flow.spec.ts
    ├── setup-config.spec.ts
    ├── spec-validation.spec.ts
    └── events-display.spec.ts
```

### 2.2 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                           App.svelte                             │
│  - Imports stores, calls services on mount                       │
│  - Renders layout components                                     │
└─────────────────────────────────────────────────────────────────┘
                                │
           ┌────────────────────┼────────────────────┐
           ▼                    ▼                    ▼
    ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
    │LeftSidebar  │      │CenterColumn │      │RightSidebar │
    └─────────────┘      └─────────────┘      └─────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
    ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
    │  Stores     │◄────►│  Stores     │◄────►│  Stores     │
    │ (agent,spec)│      │(chat,setup) │      │(events,cfg) │
    └─────────────┘      └─────────────┘      └─────────────┘
           │                    │                    │
           └────────────────────┼────────────────────┘
                                ▼
                       ┌─────────────────┐
                       │    Services     │
                       │ (api, streams)  │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │   Backend API   │
                       │  /ui/*, /chat/* │
                       └─────────────────┘
```

---

## 3. Phase 1: Extract Types & Utilities

**Goal**: Move all TypeScript types and pure utility functions out of App.svelte.
**Risk**: Low - no behavioral changes, just file reorganization.
**Estimated Files**: 8 new files

### 3.1 Types to Extract

#### `lib/types/chat.ts`
```typescript
export type ChatMessage = {
  id: string;
  role: 'user' | 'agent';
  text: string;
  observations?: string;
  showObservations?: boolean;
  isStreaming?: boolean;
  isThinking?: boolean;
  answerStreamDone?: boolean;
  revisionStreamActive?: boolean;
  answerActionSeq?: number | null;
  ts: number;
  traceId?: string;
  latencyMs?: number;
  pause?: PauseInfo;
};

export type PauseInfo = {
  reason?: string;
  payload?: Record<string, unknown>;
  resume_token?: string;
};

export type PlannerEventPayload = {
  id: string;
  event: string;
  trace_id?: string;
  session_id?: string;
  node?: string;
  latency_ms?: number;
  thought?: string;
  stream_id?: string;
  seq?: number;
  text?: string;
  done?: boolean;
  ts?: number;
  chunk?: unknown;
  artifact_type?: string;
  meta?: Record<string, unknown>;
};
```

#### `lib/types/trajectory.ts`
```typescript
export type TimelineStep = {
  id: string;
  name: string;
  thought?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  latencyMs?: number;
  reflectionScore?: number;
  status?: 'ok' | 'error';
  isParallel?: boolean;
};

export interface TrajectoryStep {
  action?: {
    next_node?: string;
    plan?: { node: string }[];
    thought?: string;
    args?: Record<string, unknown>;
  };
  observation?: Record<string, unknown>;
  latency_ms?: number;
  metadata?: {
    reflection?: { score?: number };
  };
  error?: boolean;
}

export interface TrajectoryPayload {
  steps?: TrajectoryStep[];
}
```

#### `lib/types/spec.ts`
```typescript
export type ValidationStatus = 'pending' | 'valid' | 'error';

export type SpecError = {
  id: string;
  message: string;
  line?: number | null;
};

export interface SpecData {
  content: string;
  valid: boolean;
  errors?: Array<{ message: string; line?: number | null }>;
}

export interface ValidationResult {
  valid: boolean;
  errors?: Array<{ message: string; line?: number | null }>;
}
```

#### `lib/types/meta.ts`
```typescript
export type AgentMeta = {
  name: string;
  description: string;
  template: string;
  version: string;
  flags: string[];
  tools: number;
  flows: number;
};

export type ServiceInfo = {
  name: string;
  status: string;
  url: string | null;
};

export type ToolInfo = {
  name: string;
  desc: string;
  tags: string[];
};

export type ConfigItem = {
  label: string;
  value: string | number | boolean | null;
};

export interface MetaResponse {
  agent?: {
    name?: string;
    description?: string;
    template?: string;
    version?: string;
    flags?: string[];
  };
  planner?: Record<string, string | number | boolean | null>;
  services?: Array<{ name: string; enabled: boolean; url?: string }>;
  tools?: Array<{ name: string; description: string; tags?: string[] }>;
  flows?: unknown[];
}
```

#### `lib/types/index.ts`
```typescript
export * from './chat';
export * from './trajectory';
export * from './spec';
export * from './meta';
```

### 3.2 Utilities to Extract

#### `lib/utils/format.ts`
```typescript
/**
 * Format timestamp to HH:MM display
 */
export const formatTime = (ts: number): string =>
  new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

/**
 * Generate a random UUID
 */
export const randomId = (): string => crypto.randomUUID();
```

#### `lib/utils/json.ts`
```typescript
/**
 * Safely parse JSON, returning null on failure
 */
export const safeParse = (raw: string): Record<string, unknown> | null => {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
};

/**
 * Parse JSON and validate it's an object (not array/null)
 * @throws Error if invalid
 */
export const parseJsonObject = (
  raw: string,
  options: { label: string }
): Record<string, unknown> => {
  const { label } = options;
  const trimmed = raw.trim();
  if (!trimmed) return {};

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }

  if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} must be a JSON object.`);
  }

  return parsed as Record<string, unknown>;
};
```

#### `lib/utils/constants.ts`
```typescript
/**
 * Sentinel value to gate answer rendering until planner sets action sequence
 */
export const ANSWER_GATE_SENTINEL = -1;

/**
 * Maximum number of events to keep in memory
 */
export const MAX_EVENTS = 200;

/**
 * Maximum events from chat stream
 */
export const MAX_CHAT_EVENTS = 120;
```

#### `lib/utils/index.ts`
```typescript
export * from './format';
export * from './json';
export * from './constants';
```

### 3.3 Phase 1 Checklist

- [ ] Create `lib/types/chat.ts`
- [ ] Create `lib/types/trajectory.ts`
- [ ] Create `lib/types/spec.ts`
- [ ] Create `lib/types/meta.ts`
- [ ] Create `lib/types/index.ts`
- [ ] Create `lib/utils/format.ts`
- [ ] Create `lib/utils/json.ts`
- [ ] Create `lib/utils/constants.ts`
- [ ] Create `lib/utils/index.ts`
- [ ] Update App.svelte imports to use new modules
- [ ] Remove inline type definitions from App.svelte
- [ ] Remove utility functions from App.svelte
- [ ] Verify app still works (manual test)
- [ ] Delete `lib/Counter.svelte` (unused template file)

---

## 4. Phase 2: Create Stores

**Goal**: Centralize all reactive state into Svelte 5 rune-based stores.
**Risk**: Medium - changes how state is accessed throughout components.
**Estimated Files**: 8 new files

### 4.1 Store Pattern

Using Svelte 5's module-level `$state` runes with getter/setter pattern:

```typescript
// Pattern for all stores
export const createStore = () => {
  // Private reactive state
  let value = $state<Type>(initialValue);

  return {
    // Getters expose reactive reads
    get value() { return value; },

    // Setters for external updates
    set value(v: Type) { value = v; },

    // Derived values
    get computed() { return someComputation(value); },

    // Actions
    action() { /* modify value */ },
    reset() { value = initialValue; }
  };
};

export const store = createStore();
```

### 4.2 Store Definitions

#### `lib/stores/session.svelte.ts`
```typescript
import { randomId } from '$lib/utils';

function createSessionStore() {
  let sessionId = $state(randomId());
  let activeTraceId = $state<string | null>(null);
  let isSending = $state(false);

  return {
    get sessionId() { return sessionId; },
    set sessionId(v: string) { sessionId = v; },

    get activeTraceId() { return activeTraceId; },
    set activeTraceId(v: string | null) { activeTraceId = v; },

    get isSending() { return isSending; },
    set isSending(v: boolean) { isSending = v; },

    reset() {
      sessionId = randomId();
      activeTraceId = null;
      isSending = false;
    },

    newSession() {
      sessionId = randomId();
      activeTraceId = null;
    }
  };
}

export const sessionStore = createSessionStore();
```

#### `lib/stores/chat.svelte.ts`
```typescript
import type { ChatMessage } from '$lib/types';
import { randomId } from '$lib/utils';
import { ANSWER_GATE_SENTINEL } from '$lib/utils/constants';

function createChatStore() {
  let messages = $state<ChatMessage[]>([]);
  let input = $state('');

  return {
    get messages() { return messages; },
    get input() { return input; },
    set input(v: string) { input = v; },

    get isEmpty() { return messages.length === 0; },

    addUserMessage(text: string): ChatMessage {
      const msg: ChatMessage = {
        id: randomId(),
        role: 'user',
        text,
        ts: Date.now()
      };
      messages.push(msg);
      return msg;
    },

    addAgentMessage(): ChatMessage {
      const msg: ChatMessage = {
        id: randomId(),
        role: 'agent',
        text: '',
        observations: '',
        showObservations: false,
        isStreaming: true,
        isThinking: false,
        answerStreamDone: false,
        revisionStreamActive: false,
        answerActionSeq: ANSWER_GATE_SENTINEL,
        ts: Date.now()
      };
      messages.push(msg);
      return msg;
    },

    findMessage(id: string): ChatMessage | undefined {
      return messages.find(m => m.id === id);
    },

    updateMessage(id: string, updates: Partial<ChatMessage>) {
      const msg = messages.find(m => m.id === id);
      if (msg) {
        Object.assign(msg, updates);
      }
    },

    clearInput() {
      input = '';
    },

    clear() {
      messages = [];
      input = '';
    }
  };
}

export const chatStore = createChatStore();
```

#### `lib/stores/events.svelte.ts`
```typescript
import type { PlannerEventPayload } from '$lib/types';
import { randomId } from '$lib/utils';
import { MAX_EVENTS } from '$lib/utils/constants';

function createEventsStore() {
  let events = $state<PlannerEventPayload[]>([]);
  let filter = $state<Set<string>>(new Set());
  let paused = $state(false);

  return {
    get events() { return events; },
    get filter() { return filter; },
    get paused() { return paused; },
    set paused(v: boolean) { paused = v; },

    get isEmpty() { return events.length === 0; },

    setFilter(eventType: string | null) {
      if (!eventType) {
        filter = new Set();
      } else {
        filter = new Set([eventType]);
      }
    },

    addEvent(data: Record<string, unknown>, eventType: string) {
      // Check for duplicates
      const eventKey = `${data.event ?? ''}|${data.step ?? ''}|${data.ts ?? ''}`;
      const isDuplicate = events.some(
        e => `${e.event ?? ''}|${e.step ?? ''}|${e.ts ?? ''}` === eventKey
      );

      if (!isDuplicate) {
        events.unshift({ id: randomId(), ...data, event: eventType } as PlannerEventPayload);
        if (events.length > MAX_EVENTS) {
          events.length = MAX_EVENTS;
        }
      }
    },

    shouldProcess(eventType: string): boolean {
      if (paused) return false;
      if (filter.size === 0) return true;
      return filter.has(eventType);
    },

    clear() {
      events = [];
    }
  };
}

export const eventsStore = createEventsStore();
```

#### `lib/stores/timeline.svelte.ts`
```typescript
import type { TimelineStep, TrajectoryPayload } from '$lib/types';

function createTimelineStore() {
  let steps = $state<TimelineStep[]>([]);
  let artifactStreams = $state<Record<string, unknown[]>>({});

  return {
    get steps() { return steps; },
    get artifactStreams() { return artifactStreams; },

    get isEmpty() { return steps.length === 0; },
    get hasArtifacts() { return Object.keys(artifactStreams).length > 0; },

    setFromPayload(payload: TrajectoryPayload) {
      const rawSteps = payload?.steps ?? [];
      steps = rawSteps.map((step, idx) => {
        const action = step.action ?? {};
        return {
          id: `step-${idx}`,
          name: action.next_node ?? action.plan?.[0]?.node ?? 'step',
          thought: action.thought,
          args: action.args,
          result: step.observation,
          latencyMs: step.latency_ms ?? undefined,
          reflectionScore: step.metadata?.reflection?.score ?? undefined,
          status: step.error ? 'error' : 'ok'
        } as TimelineStep;
      });
    },

    addArtifactChunk(streamId: string, chunk: unknown) {
      const existing = artifactStreams[streamId] ?? [];
      artifactStreams[streamId] = [...existing, chunk];
    },

    clearArtifacts() {
      artifactStreams = {};
    },

    clear() {
      steps = [];
      artifactStreams = {};
    }
  };
}

export const timelineStore = createTimelineStore();
```

#### `lib/stores/agent.svelte.ts`
```typescript
import type { AgentMeta, ConfigItem, ServiceInfo, ToolInfo, MetaResponse } from '$lib/types';

const DEFAULT_META: AgentMeta = {
  name: 'loading_agent',
  description: '',
  template: '',
  version: '',
  flags: [],
  tools: 0,
  flows: 0
};

function createAgentStore() {
  let meta = $state<AgentMeta>({ ...DEFAULT_META });
  let plannerConfig = $state<ConfigItem[]>([]);
  let services = $state<ServiceInfo[]>([]);
  let catalog = $state<ToolInfo[]>([]);

  return {
    get meta() { return meta; },
    get plannerConfig() { return plannerConfig; },
    get services() { return services; },
    get catalog() { return catalog; },

    setFromResponse(data: MetaResponse) {
      const agent = data.agent ?? {};
      meta = {
        name: agent.name ?? 'agent',
        description: agent.description ?? '',
        template: agent.template ?? '',
        version: agent.version ?? '',
        flags: agent.flags ?? [],
        tools: (data.tools ?? []).length,
        flows: (data.flows ?? []).length
      };

      plannerConfig = data.planner && Object.keys(data.planner).length
        ? Object.entries(data.planner).map(([label, value]) => ({
            label,
            value: value as string | number | boolean | null
          }))
        : [];

      services = data.services?.map(svc => ({
        name: svc.name,
        status: svc.enabled ? 'enabled' : 'disabled',
        url: svc.url ?? null
      })) ?? [];

      catalog = data.tools?.map(tool => ({
        name: tool.name,
        desc: tool.description,
        tags: tool.tags ?? []
      })) ?? [];
    },

    reset() {
      meta = { ...DEFAULT_META };
      plannerConfig = [];
      services = [];
      catalog = [];
    }
  };
}

export const agentStore = createAgentStore();
```

#### `lib/stores/spec.svelte.ts`
```typescript
import type { SpecError, ValidationStatus, SpecData, ValidationResult } from '$lib/types';

function createSpecStore() {
  let content = $state('');
  let status = $state<ValidationStatus>('pending');
  let errors = $state<SpecError[]>([]);

  return {
    get content() { return content; },
    set content(v: string) { content = v; },

    get status() { return status; },
    get errors() { return errors; },

    get isValid() { return status === 'valid'; },
    get hasErrors() { return errors.length > 0; },

    setFromSpecData(data: SpecData) {
      content = data.content;
      status = data.valid ? 'valid' : 'error';
      errors = (data.errors ?? []).map((err, idx) => ({
        id: `err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    setValidationResult(result: ValidationResult) {
      status = result.valid ? 'valid' : 'error';
      errors = (result.errors ?? []).map((err, idx) => ({
        id: `val-err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    setGenerationErrors(errs: Array<{ message: string; line?: number | null }>) {
      status = 'error';
      errors = errs.map((err, idx) => ({
        id: `gen-err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    markValid() {
      status = 'valid';
      errors = [];
    },

    reset() {
      content = '';
      status = 'pending';
      errors = [];
    }
  };
}

export const specStore = createSpecStore();
```

#### `lib/stores/setup.svelte.ts`
```typescript
import { parseJsonObject } from '$lib/utils';

export interface SetupContext {
  toolContext: Record<string, unknown>;
  llmContext: Record<string, unknown>;
}

function createSetupStore() {
  let tenantId = $state('playground-tenant');
  let userId = $state('playground-user');
  let toolContextRaw = $state('{}');
  let llmContextRaw = $state('{}');
  let error = $state<string | null>(null);

  return {
    get tenantId() { return tenantId; },
    set tenantId(v: string) { tenantId = v; },

    get userId() { return userId; },
    set userId(v: string) { userId = v; },

    get toolContextRaw() { return toolContextRaw; },
    set toolContextRaw(v: string) { toolContextRaw = v; },

    get llmContextRaw() { return llmContextRaw; },
    set llmContextRaw(v: string) { llmContextRaw = v; },

    get error() { return error; },
    set error(v: string | null) { error = v; },

    /**
     * Parse and validate contexts
     * @returns Parsed contexts or null if error (error is set internally)
     */
    parseContexts(): SetupContext | null {
      try {
        const extraTool = parseJsonObject(toolContextRaw, { label: 'Tool context' });
        const toolContext = {
          tenant_id: tenantId,
          user_id: userId,
          ...extraTool
        };
        const llmContext = parseJsonObject(llmContextRaw, { label: 'LLM context' });
        error = null;
        return { toolContext, llmContext };
      } catch (e) {
        error = e instanceof Error ? e.message : 'Invalid setup configuration.';
        return null;
      }
    },

    clearError() {
      error = null;
    },

    reset() {
      tenantId = 'playground-tenant';
      userId = 'playground-user';
      toolContextRaw = '{}';
      llmContextRaw = '{}';
      error = null;
    }
  };
}

export const setupStore = createSetupStore();
```

#### `lib/stores/index.ts`
```typescript
export { sessionStore } from './session.svelte';
export { chatStore } from './chat.svelte';
export { eventsStore } from './events.svelte';
export { timelineStore } from './timeline.svelte';
export { agentStore } from './agent.svelte';
export { specStore } from './spec.svelte';
export { setupStore } from './setup.svelte';
```

### 4.3 Phase 2 Checklist

- [ ] Create `lib/stores/session.svelte.ts`
- [ ] Create `lib/stores/chat.svelte.ts`
- [ ] Create `lib/stores/events.svelte.ts`
- [ ] Create `lib/stores/timeline.svelte.ts`
- [ ] Create `lib/stores/agent.svelte.ts`
- [ ] Create `lib/stores/spec.svelte.ts`
- [ ] Create `lib/stores/setup.svelte.ts`
- [ ] Create `lib/stores/index.ts`
- [ ] Update App.svelte to import and use stores
- [ ] Remove local $state declarations from App.svelte
- [ ] Update all state references to use store getters/setters
- [ ] Verify reactive updates work correctly
- [ ] Test all functionality manually

---

## 5. Phase 3: Extract Services

**Goal**: Move all API calls and EventSource management to dedicated service modules.
**Risk**: Medium - refactors the most complex logic (sendChat ~200 lines).
**Estimated Files**: 5 new files

### 5.1 API Service

#### `lib/services/api.ts`
```typescript
import type { MetaResponse, SpecData, ValidationResult, TrajectoryPayload } from '$lib/types';

const BASE_URL = '';  // Same origin

/**
 * Load agent metadata, config, services, and tool catalog
 */
export async function loadMeta(): Promise<MetaResponse | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/meta`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('meta load failed', err);
    return null;
  }
}

/**
 * Load spec content and validation status
 */
export async function loadSpec(): Promise<SpecData | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/spec`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('spec load failed', err);
    return null;
  }
}

/**
 * Validate spec content
 */
export async function validateSpec(specText: string): Promise<ValidationResult | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_text: specText })
    });
    return await resp.json();
  } catch (err) {
    console.error('validate failed', err);
    return null;
  }
}

/**
 * Generate project from spec
 * @returns true on success, array of errors on failure, null on exception
 */
export async function generateProject(
  specText: string
): Promise<true | Array<{ message: string; line?: number | null }> | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_text: specText })
    });
    if (!resp.ok) {
      return await resp.json();
    }
    return true;
  } catch (err) {
    console.error('generate failed', err);
    return null;
  }
}

/**
 * Fetch execution trajectory for a trace
 */
export async function fetchTrajectory(
  traceId: string,
  sessionId: string
): Promise<TrajectoryPayload | null> {
  try {
    const resp = await fetch(
      `${BASE_URL}/trajectory/${traceId}?session_id=${encodeURIComponent(sessionId)}`
    );
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('trajectory fetch failed', err);
    return null;
  }
}
```

### 5.2 Chat Stream Service

#### `lib/services/chat-stream.ts`
```typescript
import type { ChatMessage } from '$lib/types';
import { safeParse } from '$lib/utils';
import { ANSWER_GATE_SENTINEL } from '$lib/utils/constants';
import { chatStore, eventsStore, timelineStore, sessionStore } from '$lib/stores';

export interface ChatStreamCallbacks {
  onDone: (traceId: string | null) => void;
  onError: (error: string) => void;
}

/**
 * Manages the chat EventSource connection
 */
class ChatStreamManager {
  private eventSource: EventSource | null = null;
  private agentMsgId: string | null = null;

  /**
   * Start a new chat stream
   */
  start(
    query: string,
    sessionId: string,
    toolContext: Record<string, unknown>,
    llmContext: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    // Close any existing connection
    this.close();

    // Create agent message placeholder
    const agentMsg = chatStore.addAgentMessage();
    this.agentMsgId = agentMsg.id;

    // Build URL
    const url = new URL('/chat/stream', window.location.origin);
    url.searchParams.set('query', query);
    url.searchParams.set('session_id', sessionId);
    if (Object.keys(toolContext).length) {
      url.searchParams.set('tool_context', JSON.stringify(toolContext));
    }
    if (Object.keys(llmContext).length) {
      url.searchParams.set('llm_context', JSON.stringify(llmContext));
    }

    this.eventSource = new EventSource(url.toString());

    // Register event handlers
    const events = ['chunk', 'artifact_chunk', 'llm_stream_chunk', 'step', 'event', 'done', 'error'];
    events.forEach(eventName => {
      this.eventSource!.addEventListener(eventName, (evt: MessageEvent) => {
        this.handleEvent(eventName, evt, callbacks);
      });
    });

    this.eventSource.onerror = () => {
      const msg = this.findAgentMsg();
      if (msg) {
        chatStore.updateMessage(msg.id, { isStreaming: false });
      }
      callbacks.onError('Connection lost');
      this.close();
    };
  }

  /**
   * Close the EventSource connection
   */
  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.agentMsgId = null;
  }

  private findAgentMsg(): ChatMessage | undefined {
    return this.agentMsgId ? chatStore.findMessage(this.agentMsgId) : undefined;
  }

  private handleEvent(
    eventName: string,
    evt: MessageEvent,
    callbacks: ChatStreamCallbacks
  ): void {
    const data = safeParse(evt.data);
    if (!data) return;

    const msg = this.findAgentMsg();
    if (!msg) return;

    switch (eventName) {
      case 'chunk':
      case 'llm_stream_chunk':
        this.handleChunk(msg, data, eventName);
        break;

      case 'artifact_chunk':
        this.handleArtifactChunk(data);
        break;

      case 'step':
      case 'event':
        this.handleStepEvent(msg, data, eventName);
        break;

      case 'done':
        this.handleDone(msg, data, callbacks);
        break;

      case 'error':
        this.handleError(msg, data, callbacks);
        break;
    }
  }

  private handleChunk(msg: ChatMessage, data: Record<string, unknown>, eventName: string): void {
    const channel = (data.channel as string) ?? 'thinking';
    const phase = (data.phase as string) ?? (eventName === 'chunk' ? 'observation' : undefined);
    const text = (data.text as string) ?? '';
    const done = Boolean(data.done);

    if (channel === 'thinking' && phase === 'action') {
      chatStore.updateMessage(msg.id, { isThinking: !done });
      return;
    }

    if (channel === 'thinking') {
      if (text) {
        chatStore.updateMessage(msg.id, {
          observations: `${msg.observations ?? ''}${text}`,
          showObservations: true,
          isThinking: false
        });
      } else {
        chatStore.updateMessage(msg.id, { isThinking: false });
      }
      return;
    }

    if (channel === 'revision') {
      const updates: Partial<ChatMessage> = {
        isThinking: false,
        isStreaming: true
      };
      if (!msg.revisionStreamActive) {
        updates.revisionStreamActive = true;
        updates.text = '';
      }
      if (text) {
        updates.text = `${msg.revisionStreamActive ? msg.text : ''}${text}`;
      }
      chatStore.updateMessage(msg.id, updates);
      return;
    }

    if (channel === 'answer') {
      const gate = (msg.answerActionSeq ?? ANSWER_GATE_SENTINEL) as number;
      const seq = data.action_seq as number | undefined;

      if (gate === ANSWER_GATE_SENTINEL) {
        chatStore.updateMessage(msg.id, {
          answerStreamDone: done,
          isStreaming: !done,
          isThinking: false
        });
        return;
      }

      if (seq !== undefined && seq !== gate) {
        chatStore.updateMessage(msg.id, { isThinking: false });
        return;
      }

      const updates: Partial<ChatMessage> = {
        isThinking: false,
        isStreaming: !done
      };
      if (text) {
        updates.text = `${msg.text}${text}`;
      }
      if (done) {
        updates.answerStreamDone = true;
      }
      chatStore.updateMessage(msg.id, updates);
      return;
    }

    // Default: append to observations
    if (text) {
      chatStore.updateMessage(msg.id, {
        observations: `${msg.observations ?? ''}${text}`,
        showObservations: true,
        isThinking: false
      });
    } else {
      chatStore.updateMessage(msg.id, { isThinking: false });
    }
  }

  private handleArtifactChunk(data: Record<string, unknown>): void {
    const streamId = (data.stream_id as string) ?? 'artifact';
    timelineStore.addArtifactChunk(streamId, data.chunk);
    eventsStore.addEvent(data, 'artifact_chunk');
  }

  private handleStepEvent(
    msg: ChatMessage,
    data: Record<string, unknown>,
    eventName: string
  ): void {
    const eventType = data.event as string;

    if (eventType === 'step_start') {
      const seq = data.action_seq as number | undefined;
      chatStore.updateMessage(msg.id, {
        answerActionSeq: typeof seq === 'number' ? seq : ANSWER_GATE_SENTINEL
      });
    }

    eventsStore.addEvent(data, eventName);
  }

  private handleDone(
    msg: ChatMessage,
    data: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    const pause = data.pause as Record<string, unknown> | undefined;
    const traceId = (data.trace_id as string) ?? null;

    if (pause) {
      this.handlePause(msg, data, pause, traceId, callbacks);
      return;
    }

    // Handle final answer
    const doneActionSeq = data.answer_action_seq as number | undefined;
    const gate = (msg.answerActionSeq ?? ANSWER_GATE_SENTINEL) as number;
    const gateReady = gate !== ANSWER_GATE_SENTINEL;

    if (gateReady && (doneActionSeq === undefined || doneActionSeq === gate)) {
      if (data.answer && typeof data.answer === 'string') {
        chatStore.updateMessage(msg.id, { text: data.answer });
      }
    }

    chatStore.updateMessage(msg.id, {
      isStreaming: false,
      isThinking: false
    });

    callbacks.onDone(traceId);
    this.close();
  }

  private handlePause(
    msg: ChatMessage,
    data: Record<string, unknown>,
    pause: Record<string, unknown>,
    traceId: string | null,
    callbacks: ChatStreamCallbacks
  ): void {
    const payload = (pause.payload as Record<string, unknown>) ?? {};
    const authUrl = (payload.auth_url as string) || (payload.url as string) || '';
    const provider = (payload.provider as string) || '';
    const reason = (pause.reason as string) || 'pause';

    let body = `Planner paused (${reason})`;
    if (provider) body += ` for ${provider}`;
    if (authUrl) body += `\n[Open auth link](${authUrl})`;
    if (pause.resume_token) body += `\nResume token: \`${pause.resume_token}\``;

    chatStore.updateMessage(msg.id, {
      pause: pause as ChatMessage['pause'],
      traceId: traceId ?? undefined,
      text: body,
      isStreaming: false,
      isThinking: false
    });

    callbacks.onDone(traceId);
    this.close();
  }

  private handleError(
    msg: ChatMessage,
    data: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    const error = (data.error as string) ?? 'Unexpected error';
    chatStore.updateMessage(msg.id, {
      text: error,
      isStreaming: false,
      isThinking: false
    });
    callbacks.onError(error);
    this.close();
  }
}

export const chatStreamManager = new ChatStreamManager();
```

### 5.3 Event Follow Service

#### `lib/services/event-stream.ts`
```typescript
import { safeParse } from '$lib/utils';
import { eventsStore, timelineStore } from '$lib/stores';

/**
 * Manages the follow EventSource for live event updates
 */
class EventStreamManager {
  private eventSource: EventSource | null = null;

  /**
   * Start following events for a trace
   */
  start(traceId: string, sessionId: string): void {
    this.close();

    const url = new URL('/events', window.location.origin);
    url.searchParams.set('trace_id', traceId);
    url.searchParams.set('session_id', sessionId);
    url.searchParams.set('follow', 'true');

    this.eventSource = new EventSource(url.toString());

    const listener = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;

      const incomingEvent = (evt.type as string) || (data.event as string) || '';

      // Check if we should process this event
      if (!eventsStore.shouldProcess(incomingEvent)) return;

      // Handle artifact chunks
      if (incomingEvent === 'artifact_chunk') {
        const streamId = (data.stream_id as string) ?? 'artifact';
        timelineStore.addArtifactChunk(streamId, data.chunk);
      }

      // Skip llm_stream_chunk to avoid flooding
      if (incomingEvent === 'llm_stream_chunk') return;

      // Add to events
      eventsStore.addEvent(data, incomingEvent || 'event');
    };

    // Register for multiple event types
    ['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk'].forEach(type => {
      this.eventSource!.addEventListener(type, listener);
    });

    this.eventSource.onmessage = listener;
    this.eventSource.onerror = () => this.close();
  }

  /**
   * Close the EventSource connection
   */
  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}

export const eventStreamManager = new EventStreamManager();
```

### 5.4 Markdown Service

#### `lib/services/markdown.ts`
```typescript
import { marked } from 'marked';

// Configure marked options
marked.setOptions({
  breaks: true,
  gfm: true
});

/**
 * Render markdown to HTML (synchronous for streaming compatibility)
 */
export function renderMarkdown(text: string): string {
  if (!text) return '';
  try {
    return marked.parse(text, { async: false }) as string;
  } catch {
    return text;
  }
}
```

### 5.5 Service Index

#### `lib/services/index.ts`
```typescript
export * from './api';
export { chatStreamManager } from './chat-stream';
export { eventStreamManager } from './event-stream';
export { renderMarkdown } from './markdown';
```

### 5.6 Phase 3 Checklist

- [ ] Create `lib/services/api.ts`
- [ ] Create `lib/services/chat-stream.ts`
- [ ] Create `lib/services/event-stream.ts`
- [ ] Create `lib/services/markdown.ts`
- [ ] Create `lib/services/index.ts`
- [ ] Update App.svelte to use service modules
- [ ] Remove API functions from App.svelte
- [ ] Remove EventSource management from App.svelte
- [ ] Wire services to stores
- [ ] Verify streaming works correctly
- [ ] Test chat flow end-to-end
- [ ] Test event following
- [ ] Test spec validation/generation

---

## 6. Phase 4: Component Extraction

**Goal**: Break down the monolithic UI into focused, reusable components.
**Risk**: Medium-High - most invasive change, affects all UI rendering.
**Estimated Files**: ~35 new component files

### 6.1 Extraction Strategy

Extract in this order (leaf components first):

1. **UI Primitives** - Reusable, stateless
2. **Layout Components** - Structural containers
3. **Leaf Components** - Simple, focused (ProjectCard, ServiceRow, etc.)
4. **Composite Components** - Combine primitives (ChatCard, EventsCard)
5. **Section Components** - Sidebars and columns
6. **App.svelte** - Thin orchestrator

### 6.2 UI Primitives (`lib/components/ui/`)

#### `Pill.svelte`
```svelte
<script lang="ts">
  type Variant = 'default' | 'subtle' | 'ghost';
  type Size = 'default' | 'small';

  interface Props {
    variant?: Variant;
    size?: Size;
    class?: string;
  }

  let { variant = 'default', size = 'default', class: className = '' }: Props = $props();
</script>

<span class="pill {variant} {size} {className}">
  <slot />
</span>

<style>
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 11px;
  }

  .subtle {
    background: var(--color-pill-subtle-bg, #eef5f3);
    color: var(--color-pill-subtle-text, #1f6c68);
  }

  .ghost {
    background: var(--color-pill-ghost-bg, #f4f0ea);
    color: var(--color-pill-ghost-text, #514c45);
    border: 1px solid var(--color-pill-ghost-border, #ebe5dd);
  }

  .small {
    padding: 3px 7px;
    font-size: 10px;
  }
</style>
```

#### `Tabs.svelte`
```svelte
<script lang="ts">
  interface Tab {
    id: string;
    label: string;
  }

  interface Props {
    tabs: Tab[];
    active: string;
    onchange?: (id: string) => void;
  }

  let { tabs, active, onchange }: Props = $props();
</script>

<div class="tabs">
  {#each tabs as tab (tab.id)}
    <button
      type="button"
      class="tab"
      class:active={active === tab.id}
      onclick={() => onchange?.(tab.id)}
    >
      {tab.label}
    </button>
  {/each}
  <slot name="right" />
</div>

<style>
  .tabs {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 10px;
  }

  .tab {
    padding: 6px 12px;
    border-radius: 10px;
    background: var(--color-tab-bg, #f2eee8);
    font-weight: 600;
    font-size: 12px;
    color: var(--color-tab-text, #5a534a);
    cursor: pointer;
    border: none;
  }

  .tab.active {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }
</style>
```

#### `Empty.svelte`
```svelte
<script lang="ts">
  interface Props {
    icon?: string;
    title: string;
    subtitle?: string;
    inline?: boolean;
  }

  let { icon, title, subtitle, inline = false }: Props = $props();
</script>

<div class="empty" class:inline>
  {#if icon}
    <div class="icon">{icon}</div>
  {/if}
  <div class="title">{title}</div>
  {#if subtitle}
    <div class="subtitle">{subtitle}</div>
  {/if}
</div>

<style>
  .empty {
    text-align: center;
    color: var(--color-muted, #7a756d);
    padding: 30px 10px;
  }

  .empty.inline {
    padding: 12px;
  }

  .icon {
    font-size: 24px;
    margin-bottom: 6px;
    opacity: 0.6;
  }

  .title {
    font-weight: 600;
    font-size: 13px;
    margin-bottom: 4px;
  }

  .subtitle {
    font-size: 11px;
    color: var(--color-muted-light, #8a847c);
  }
</style>
```

#### `ErrorList.svelte`
```svelte
<script lang="ts">
  import type { SpecError } from '$lib/types';

  interface Props {
    errors: SpecError[];
  }

  let { errors }: Props = $props();
</script>

{#if errors.length > 0}
  <div class="errors">
    {#each errors as err (err.id)}
      <div class="error-row">⚠️ {err.message}</div>
    {/each}
  </div>
{/if}

<style>
  .errors {
    margin-top: 6px;
    padding: 8px;
    border-radius: 10px;
    background: var(--color-error-bg, #fdf3f3);
    color: var(--color-error-text, #9b2d2d);
    font-size: 11px;
    border: 1px solid var(--color-error-border, #f5dddd);
  }

  .error-row {
    margin-bottom: 3px;
  }

  .error-row:last-child {
    margin-bottom: 0;
  }
</style>
```

#### `CodeBlock.svelte`
```svelte
<script lang="ts">
  interface Props {
    label?: string;
    content: unknown;
    maxHeight?: string;
  }

  let { label, content, maxHeight = '100px' }: Props = $props();

  const formatted = $derived(
    typeof content === 'string' ? content : JSON.stringify(content, null, 2)
  );
</script>

<div class="code-block">
  {#if label}
    <div class="label">{label}</div>
  {/if}
  <pre style="max-height: {maxHeight}">{formatted}</pre>
</div>

<style>
  .code-block {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-code-border, #eee5d9);
    border-radius: 8px;
    padding: 6px;
    margin-top: 4px;
  }

  .label {
    text-transform: uppercase;
    letter-spacing: 0.4px;
    font-size: 9px;
    color: var(--color-muted, #8b857c);
    margin-bottom: 3px;
  }

  pre {
    margin: 0;
    font-size: 10px;
    overflow: auto;
    font-family: var(--font-mono);
  }
</style>
```

#### `StatusDot.svelte`
```svelte
<script lang="ts">
  type Status = 'pending' | 'valid' | 'error' | 'active' | 'inactive';

  interface Props {
    status: Status;
    label?: string;
  }

  let { status, label }: Props = $props();
</script>

<span class="status-dot {status}">
  {#if label}{label}{/if}
</span>

<style>
  .status-dot {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-primary, #106c67);
  }

  .status-dot.error {
    color: var(--color-error-text, #b24c4c);
  }

  .status-dot.pending {
    color: var(--color-muted, #8b857c);
  }
</style>
```

#### `IconButton.svelte`
```svelte
<script lang="ts">
  interface Props {
    onclick?: () => void;
    title?: string;
  }

  let { onclick, title }: Props = $props();
</script>

<button class="icon-btn" {onclick} {title}>
  <slot />
</button>

<style>
  .icon-btn {
    background: var(--color-btn-ghost-bg, #f2eee8);
    padding: 6px;
    border-radius: 8px;
    font-size: 11px;
    border: 1px solid var(--color-border, #e8e1d7);
    cursor: pointer;
  }

  .icon-btn:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
  }
</style>
```

### 6.3 Layout Components (`lib/components/layout/`)

#### `Card.svelte`
```svelte
<script lang="ts">
  interface Props {
    class?: string;
  }

  let { class: className = '' }: Props = $props();
</script>

<div class="card {className}">
  <slot />
</div>

<style>
  .card {
    background: var(--color-card-bg, #fcfaf7);
    border-radius: var(--radius-lg, 18px);
    box-shadow: 0 12px 32px rgba(17, 17, 17, 0.06);
    border: 1px solid var(--color-border, #f0ebe4);
    padding: 14px;
  }
</style>
```

#### `Column.svelte`
```svelte
<script lang="ts">
  type Position = 'left' | 'center' | 'right';

  interface Props {
    position?: Position;
    class?: string;
  }

  let { position = 'center', class: className = '' }: Props = $props();
</script>

<div class="column {position} {className}">
  <slot />
</div>

<style>
  .column {
    display: flex;
    flex-direction: column;
    gap: 12px;
    height: calc(100vh - 32px);
    overflow: hidden;
  }
</style>
```

#### `Page.svelte`
```svelte
<div class="page">
  <slot />
</div>

<style>
  .page {
    display: grid;
    grid-template-columns: 300px 1fr 320px;
    gap: 16px;
    padding: 16px;
    height: 100vh;
    overflow: hidden;
  }

  @media (max-width: 1200px) {
    .page {
      grid-template-columns: 1fr;
      height: auto;
      overflow: auto;
    }
  }
</style>
```

### 6.4 Component File List

Due to space, here's the complete component list with responsibilities:

**Left Sidebar (`lib/components/sidebar-left/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `LeftSidebar.svelte` | ~15 | - | Container, renders children |
| `ProjectCard.svelte` | ~60 | - | Agent name, desc, badges, stats (reads agentStore) |
| `SpecCard.svelte` | ~50 | - | YAML viewer, status dot (reads specStore) |
| `GeneratorCard.svelte` | ~40 | - | Validate/Generate buttons |
| `GeneratorStepper.svelte` | ~40 | status | 7-step progress stepper |

**Center Column (`lib/components/center/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `CenterColumn.svelte` | ~20 | - | Container for chat + trajectory |

**Chat (`lib/components/center/chat/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `ChatCard.svelte` | ~50 | - | Tab container, renders Chat or Setup |
| `ChatHeader.svelte` | ~20 | - | Agent status pill |
| `ChatBody.svelte` | ~40 | - | Message list with auto-scroll |
| `ChatInput.svelte` | ~50 | - | Textarea + send button |
| `Message.svelte` | ~80 | message | Single message bubble |
| `ThinkingPanel.svelte` | ~40 | observations, open | Collapsible observations |
| `PauseCard.svelte` | ~50 | pause | HITL pause display |
| `TypingIndicator.svelte` | ~20 | - | Animated dots |

**Setup (`lib/components/center/setup/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `SetupTab.svelte` | ~100 | - | Form fields for session config |
| `SetupField.svelte` | ~30 | label, hint | Labeled input wrapper |

**Trajectory (`lib/components/center/trajectory/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `TrajectoryCard.svelte` | ~40 | - | Timeline container |
| `Timeline.svelte` | ~30 | steps | Renders timeline items |
| `TimelineItem.svelte` | ~60 | step | Single step with details |
| `StepDetails.svelte` | ~30 | args, result | Expandable args/result |

**Right Sidebar (`lib/components/sidebar-right/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `RightSidebar.svelte` | ~15 | - | Container |

**Events (`lib/components/sidebar-right/events/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `EventsCard.svelte` | ~50 | - | Events container with filter |
| `EventsHeader.svelte` | ~30 | - | Title + filter controls |
| `EventsBody.svelte` | ~30 | - | Event list |
| `EventRow.svelte` | ~30 | event, alt | Single event display |

**Config (`lib/components/sidebar-right/config/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `ConfigCard.svelte` | ~30 | - | Config container |
| `PlannerConfigSection.svelte` | ~30 | - | Tile grid |
| `ServicesSection.svelte` | ~30 | - | Service list |
| `ToolCatalogSection.svelte` | ~40 | - | Tool list |
| `ServiceRow.svelte` | ~25 | service | Single service |
| `ToolRow.svelte` | ~30 | tool | Single tool |

**Artifacts (`lib/components/sidebar-right/artifacts/`)**
| File | Lines | Props | Description |
|------|-------|-------|-------------|
| `ArtifactStreams.svelte` | ~40 | - | Artifact display |

### 6.5 Phase 4 Checklist

**UI Primitives**
- [ ] Create `lib/components/ui/Pill.svelte`
- [ ] Create `lib/components/ui/Tabs.svelte`
- [ ] Create `lib/components/ui/Empty.svelte`
- [ ] Create `lib/components/ui/ErrorList.svelte`
- [ ] Create `lib/components/ui/CodeBlock.svelte`
- [ ] Create `lib/components/ui/StatusDot.svelte`
- [ ] Create `lib/components/ui/IconButton.svelte`

**Layout**
- [ ] Create `lib/components/layout/Card.svelte`
- [ ] Create `lib/components/layout/Column.svelte`
- [ ] Create `lib/components/layout/Page.svelte`

**Left Sidebar**
- [ ] Create `lib/components/sidebar-left/LeftSidebar.svelte`
- [ ] Create `lib/components/sidebar-left/ProjectCard.svelte`
- [ ] Create `lib/components/sidebar-left/SpecCard.svelte`
- [ ] Create `lib/components/sidebar-left/GeneratorCard.svelte`
- [ ] Create `lib/components/sidebar-left/GeneratorStepper.svelte`

**Center Column**
- [ ] Create `lib/components/center/CenterColumn.svelte`
- [ ] Create `lib/components/center/chat/ChatCard.svelte`
- [ ] Create `lib/components/center/chat/ChatHeader.svelte`
- [ ] Create `lib/components/center/chat/ChatBody.svelte`
- [ ] Create `lib/components/center/chat/ChatInput.svelte`
- [ ] Create `lib/components/center/chat/Message.svelte`
- [ ] Create `lib/components/center/chat/ThinkingPanel.svelte`
- [ ] Create `lib/components/center/chat/PauseCard.svelte`
- [ ] Create `lib/components/center/chat/TypingIndicator.svelte`
- [ ] Create `lib/components/center/setup/SetupTab.svelte`
- [ ] Create `lib/components/center/setup/SetupField.svelte`
- [ ] Create `lib/components/center/trajectory/TrajectoryCard.svelte`
- [ ] Create `lib/components/center/trajectory/Timeline.svelte`
- [ ] Create `lib/components/center/trajectory/TimelineItem.svelte`
- [ ] Create `lib/components/center/trajectory/StepDetails.svelte`

**Right Sidebar**
- [ ] Create `lib/components/sidebar-right/RightSidebar.svelte`
- [ ] Create `lib/components/sidebar-right/events/EventsCard.svelte`
- [ ] Create `lib/components/sidebar-right/events/EventsHeader.svelte`
- [ ] Create `lib/components/sidebar-right/events/EventsBody.svelte`
- [ ] Create `lib/components/sidebar-right/events/EventRow.svelte`
- [ ] Create `lib/components/sidebar-right/config/ConfigCard.svelte`
- [ ] Create `lib/components/sidebar-right/config/PlannerConfigSection.svelte`
- [ ] Create `lib/components/sidebar-right/config/ServicesSection.svelte`
- [ ] Create `lib/components/sidebar-right/config/ToolCatalogSection.svelte`
- [ ] Create `lib/components/sidebar-right/config/ServiceRow.svelte`
- [ ] Create `lib/components/sidebar-right/config/ToolRow.svelte`
- [ ] Create `lib/components/sidebar-right/artifacts/ArtifactStreams.svelte`

**Final**
- [ ] Refactor `App.svelte` to use new components
- [ ] Remove all UI markup from App.svelte
- [ ] Verify all functionality works
- [ ] Test responsive layout

---

## 7. Phase 5: CSS Migration

**Goal**: Extract CSS variables, migrate component styles to scoped styles.
**Risk**: Low - primarily moving styles, not changing behavior.
**Estimated Files**: 1 modified (app.css), ~35 components with scoped styles

### 7.1 New `app.css` Structure

The global CSS will be reduced to only:
- CSS Custom Properties (design tokens)
- CSS Reset
- Font imports
- Global utility classes (if any)

#### `app.css` (Target ~150 lines)
```css
/* ========== FONT IMPORTS ========== */
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");

/* ========== CSS CUSTOM PROPERTIES ========== */
:root {
  /* Typography */
  --font-sans: "Inter", system-ui, -apple-system, sans-serif;
  --font-mono: "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;

  /* Colors - Background */
  --color-bg: #f5f1eb;
  --color-bg-gradient: radial-gradient(circle at 20% 20%, #f8f4ee, #f4efe7 45%, #f5f1eb 100%);
  --color-card-bg: #fcfaf7;
  --color-code-bg: #fbf8f3;

  /* Colors - Border */
  --color-border: #f0ebe4;
  --color-border-light: #f1ece4;
  --color-code-border: #eee5d9;

  /* Colors - Text */
  --color-text: #1f1f1f;
  --color-text-secondary: #3c3a36;
  --color-muted: #6b665f;
  --color-muted-light: #8a847c;

  /* Colors - Primary (Teal) */
  --color-primary: #31a6a0;
  --color-primary-dark: #1a7c75;
  --color-primary-text: #1f6c68;
  --color-primary-light: #106c67;

  /* Colors - Error */
  --color-error-bg: #fdf3f3;
  --color-error-text: #9b2d2d;
  --color-error-border: #f5dddd;
  --color-error-accent: #b24c4c;

  /* Colors - Success */
  --color-success: #2ac19d;

  /* Colors - Pills */
  --color-pill-subtle-bg: #eef5f3;
  --color-pill-subtle-text: #1f6c68;
  --color-pill-ghost-bg: #f4f0ea;
  --color-pill-ghost-text: #514c45;
  --color-pill-ghost-border: #ebe5dd;

  /* Colors - Tabs */
  --color-tab-bg: #f2eee8;
  --color-tab-text: #5a534a;
  --color-tab-active-bg: #e8f6f2;
  --color-tab-active-text: #106c67;

  /* Colors - Buttons */
  --color-btn-ghost-bg: #f2eee8;
  --color-btn-ghost-hover: #e8e4de;
  --color-btn-primary-gradient: linear-gradient(135deg, #31a6a0, #1a7c75);
  --color-btn-primary-shadow: rgba(49, 166, 160, 0.3);

  /* Spacing */
  --space-xs: 4px;
  --space-sm: 6px;
  --space-md: 8px;
  --space-lg: 12px;
  --space-xl: 16px;

  /* Border Radius */
  --radius-sm: 8px;
  --radius-md: 10px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-2xl: 18px;
  --radius-full: 999px;

  /* Shadows */
  --shadow-card: 0 12px 32px rgba(17, 17, 17, 0.06);
  --shadow-btn: 0 6px 16px rgba(49, 166, 160, 0.3);
  --shadow-subtle: 0 4px 12px rgba(0, 0, 0, 0.04);

  /* Layout */
  --sidebar-left-width: 300px;
  --sidebar-right-width: 320px;
  --page-gap: 16px;
  --page-padding: 16px;
}

/* ========== CSS RESET ========== */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html,
body {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: var(--font-sans);
  color: var(--color-text);
  background: var(--color-bg-gradient);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

#app {
  height: 100vh;
  overflow: hidden;
}

button {
  font-family: inherit;
  cursor: pointer;
}

input,
textarea,
select {
  font-family: inherit;
}

a {
  color: var(--color-primary-text);
  text-decoration: underline;
}

a:hover {
  color: var(--color-primary-light);
}

/* ========== MARKDOWN CONTENT (Global) ========== */
.markdown-content {
  word-break: break-word;
}

.markdown-content p {
  margin: 0 0 0.5em 0;
}

.markdown-content p:last-child {
  margin-bottom: 0;
}

.markdown-content h1,
.markdown-content h2,
.markdown-content h3,
.markdown-content h4 {
  font-weight: 700;
  margin: 0.75em 0 0.25em 0;
}

.markdown-content h1:first-child,
.markdown-content h2:first-child,
.markdown-content h3:first-child,
.markdown-content h4:first-child {
  margin-top: 0;
}

.markdown-content h1 { font-size: 1.25em; }
.markdown-content h2 { font-size: 1.15em; }
.markdown-content h3 { font-size: 1.05em; }
.markdown-content h4 { font-size: 1em; }

.markdown-content ul,
.markdown-content ol {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.markdown-content li {
  margin: 0.25em 0;
}

.markdown-content code {
  background: var(--color-pill-ghost-bg);
  padding: 0.15em 0.4em;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.9em;
}

.markdown-content pre {
  background: var(--color-code-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.75em;
  overflow-x: auto;
  margin: 0.5em 0;
}

.markdown-content pre code {
  background: none;
  padding: 0;
  font-size: 0.85em;
}

.markdown-content blockquote {
  border-left: 3px solid var(--color-primary);
  margin: 0.5em 0;
  padding-left: 0.75em;
  color: var(--color-muted);
  font-style: italic;
}

.markdown-content strong {
  font-weight: 700;
}

.markdown-content em {
  font-style: italic;
}

.markdown-content hr {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 0.75em 0;
}

.markdown-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5em 0;
  font-size: 0.9em;
}

.markdown-content th,
.markdown-content td {
  border: 1px solid var(--color-border);
  padding: 0.4em 0.6em;
  text-align: left;
}

.markdown-content th {
  background: var(--color-pill-ghost-bg);
  font-weight: 600;
}

.markdown-content img {
  max-width: 100%;
  border-radius: var(--radius-sm);
}

/* ========== RESPONSIVE ========== */
@media (max-width: 1200px) {
  :root {
    --sidebar-left-width: 100%;
    --sidebar-right-width: 100%;
  }
}
```

### 7.2 Phase 5 Checklist

- [ ] Create backup of current `app.css`
- [ ] Replace `app.css` with CSS variables version
- [ ] Update each component to use CSS variables
- [ ] Move component-specific styles to `<style>` blocks
- [ ] Remove orphaned global styles
- [ ] Verify all components render correctly
- [ ] Test responsive breakpoints
- [ ] Verify dark mode compatibility (CSS variables ready)

---

## 8. Phase 6: Testing Infrastructure

**Goal**: Add comprehensive unit and E2E tests.
**Risk**: Low - adds tests, doesn't change behavior.
**Estimated Files**: ~20 test files

### 8.1 Testing Stack

| Tool | Purpose |
|------|---------|
| **Vitest** | Unit test runner (fast, Vite-native) |
| **@testing-library/svelte** | Component testing |
| **Playwright** | E2E browser testing |
| **MSW** | API mocking (Mock Service Worker) |

### 8.2 Setup Files

#### `vitest.config.ts`
```typescript
import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte({ hot: !process.env.VITEST })],
  test: {
    include: ['tests/unit/**/*.test.ts'],
    globals: true,
    environment: 'jsdom',
    setupFiles: ['tests/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte'],
      exclude: ['src/lib/types/**']
    }
  },
  resolve: {
    alias: {
      $lib: '/src/lib'
    }
  }
});
```

#### `playwright.config.ts`
```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
  },
});
```

#### `tests/setup.ts`
```typescript
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock crypto.randomUUID
vi.stubGlobal('crypto', {
  randomUUID: () => 'test-uuid-' + Math.random().toString(36).substr(2, 9)
});

// Mock EventSource
class MockEventSource {
  url: string;
  onmessage: ((evt: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Map<string, ((evt: MessageEvent) => void)[]> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (evt: MessageEvent) => void) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, []);
    }
    this.listeners.get(type)!.push(listener);
  }

  close() {}

  // Test helper to simulate events
  simulateEvent(type: string, data: unknown) {
    const evt = new MessageEvent(type, { data: JSON.stringify(data) });
    this.listeners.get(type)?.forEach(l => l(evt));
  }
}

vi.stubGlobal('EventSource', MockEventSource);
```

### 8.3 Unit Test Examples

#### `tests/unit/utils/format.test.ts`
```typescript
import { describe, it, expect } from 'vitest';
import { formatTime, randomId } from '$lib/utils/format';

describe('formatTime', () => {
  it('formats timestamp to HH:MM', () => {
    const ts = new Date('2024-01-15T14:30:00').getTime();
    const result = formatTime(ts);
    expect(result).toMatch(/^\d{2}:\d{2}$/);
  });
});

describe('randomId', () => {
  it('returns a UUID string', () => {
    const id = randomId();
    expect(id).toMatch(/^test-uuid-/);
  });

  it('returns unique values', () => {
    const id1 = randomId();
    const id2 = randomId();
    expect(id1).not.toBe(id2);
  });
});
```

#### `tests/unit/utils/json.test.ts`
```typescript
import { describe, it, expect } from 'vitest';
import { safeParse, parseJsonObject } from '$lib/utils/json';

describe('safeParse', () => {
  it('parses valid JSON', () => {
    expect(safeParse('{"a": 1}')).toEqual({ a: 1 });
  });

  it('returns null for invalid JSON', () => {
    expect(safeParse('not json')).toBeNull();
  });
});

describe('parseJsonObject', () => {
  it('parses valid object JSON', () => {
    expect(parseJsonObject('{"key": "value"}', { label: 'Test' }))
      .toEqual({ key: 'value' });
  });

  it('returns empty object for empty string', () => {
    expect(parseJsonObject('', { label: 'Test' })).toEqual({});
  });

  it('throws for array JSON', () => {
    expect(() => parseJsonObject('[1,2]', { label: 'Test' }))
      .toThrow('Test must be a JSON object');
  });

  it('throws for invalid JSON', () => {
    expect(() => parseJsonObject('not json', { label: 'Test' }))
      .toThrow('Test must be valid JSON');
  });
});
```

#### `tests/unit/stores/chat.test.ts`
```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { chatStore } from '$lib/stores/chat.svelte';

describe('chatStore', () => {
  beforeEach(() => {
    chatStore.clear();
  });

  it('starts empty', () => {
    expect(chatStore.isEmpty).toBe(true);
    expect(chatStore.messages).toEqual([]);
  });

  it('adds user message', () => {
    const msg = chatStore.addUserMessage('Hello');
    expect(msg.role).toBe('user');
    expect(msg.text).toBe('Hello');
    expect(chatStore.isEmpty).toBe(false);
  });

  it('adds agent message with defaults', () => {
    const msg = chatStore.addAgentMessage();
    expect(msg.role).toBe('agent');
    expect(msg.text).toBe('');
    expect(msg.isStreaming).toBe(true);
    expect(msg.isThinking).toBe(false);
  });

  it('updates message by id', () => {
    const msg = chatStore.addAgentMessage();
    chatStore.updateMessage(msg.id, { text: 'Updated' });
    expect(chatStore.findMessage(msg.id)?.text).toBe('Updated');
  });

  it('manages input state', () => {
    chatStore.input = 'test';
    expect(chatStore.input).toBe('test');
    chatStore.clearInput();
    expect(chatStore.input).toBe('');
  });
});
```

#### `tests/unit/services/api.test.ts`
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { loadMeta, loadSpec, validateSpec } from '$lib/services/api';

describe('API Service', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  describe('loadMeta', () => {
    it('returns parsed response on success', async () => {
      const mockData = { agent: { name: 'TestAgent' } };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadMeta();
      expect(result).toEqual(mockData);
    });

    it('returns null on error', async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false });
      const result = await loadMeta();
      expect(result).toBeNull();
    });
  });

  describe('validateSpec', () => {
    it('posts spec and returns result', async () => {
      const mockResult = { valid: true, errors: [] };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResult)
      });

      const result = await validateSpec('test spec');
      expect(global.fetch).toHaveBeenCalledWith(
        '/ui/validate',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ spec_text: 'test spec' })
        })
      );
      expect(result).toEqual(mockResult);
    });
  });
});
```

#### `tests/unit/components/Pill.test.ts`
```typescript
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Pill from '$lib/components/ui/Pill.svelte';

describe('Pill', () => {
  it('renders children', () => {
    render(Pill, { props: {}, slots: { default: 'Test Label' } });
    expect(screen.getByText('Test Label')).toBeInTheDocument();
  });

  it('applies variant class', () => {
    const { container } = render(Pill, { props: { variant: 'subtle' } });
    expect(container.querySelector('.pill')).toHaveClass('subtle');
  });

  it('applies size class', () => {
    const { container } = render(Pill, { props: { size: 'small' } });
    expect(container.querySelector('.pill')).toHaveClass('small');
  });
});
```

### 8.4 E2E Test Examples

#### `tests/e2e/chat-flow.spec.ts`
```typescript
import { test, expect } from '@playwright/test';

test.describe('Chat Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('displays empty state initially', async ({ page }) => {
    await expect(page.getByText('Ready to test agent behavior')).toBeVisible();
  });

  test('can switch to setup tab', async ({ page }) => {
    await page.getByRole('button', { name: 'Setup' }).click();
    await expect(page.getByText('Session ID')).toBeVisible();
  });

  test('sends message and shows agent response', async ({ page }) => {
    // Type message
    await page.getByPlaceholder('Ask your agent something...').fill('Hello agent');

    // Send
    await page.getByRole('button', { name: '➤' }).click();

    // User message appears
    await expect(page.getByText('Hello agent')).toBeVisible();

    // Agent message placeholder appears (streaming)
    await expect(page.locator('.bubble.agent')).toBeVisible();
  });

  test('shows trajectory after completion', async ({ page }) => {
    // Send a message and wait for completion
    await page.getByPlaceholder('Ask your agent something...').fill('Test query');
    await page.getByRole('button', { name: '➤' }).click();

    // Wait for trajectory section to have content
    await expect(page.getByText('Execution Trajectory')).toBeVisible();
  });
});
```

#### `tests/e2e/setup-config.spec.ts`
```typescript
import { test, expect } from '@playwright/test';

test.describe('Setup Configuration', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Setup' }).click();
  });

  test('can update session ID', async ({ page }) => {
    const input = page.locator('.setup-input').first();
    await input.clear();
    await input.fill('custom-session-123');
    await expect(input).toHaveValue('custom-session-123');
  });

  test('can generate new session', async ({ page }) => {
    const input = page.locator('.setup-input').first();
    const originalValue = await input.inputValue();

    await page.getByRole('button', { name: 'New' }).click();

    const newValue = await input.inputValue();
    expect(newValue).not.toBe(originalValue);
  });

  test('shows error for invalid JSON context', async ({ page }) => {
    await page.locator('textarea').first().fill('not valid json');
    await page.getByRole('button', { name: 'Chat' }).click();
    await page.getByPlaceholder('Ask your agent something...').fill('test');
    await page.getByRole('button', { name: '➤' }).click();

    // Should show error and switch to setup tab
    await expect(page.getByText('must be valid JSON')).toBeVisible();
  });
});
```

#### `tests/e2e/spec-validation.spec.ts`
```typescript
import { test, expect } from '@playwright/test';

test.describe('Spec Validation', () => {
  test('displays spec content', async ({ page }) => {
    await page.goto('/');

    // Spec card should be visible
    await expect(page.locator('.spec-card')).toBeVisible();
  });

  test('validate button triggers validation', async ({ page }) => {
    await page.goto('/');

    await page.getByRole('button', { name: 'Validate' }).click();

    // Status should update (either Valid or Errors)
    await expect(page.locator('.status-dot')).toContainText(/Valid|Errors/);
  });
});
```

### 8.5 Package.json Updates

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest",
    "test:ui": "vitest --ui",
    "test:coverage": "vitest run --coverage",
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  },
  "devDependencies": {
    "@playwright/test": "^1.40.0",
    "@sveltejs/vite-plugin-svelte": "^6.2.1",
    "@testing-library/jest-dom": "^6.1.0",
    "@testing-library/svelte": "^4.0.0",
    "@vitest/coverage-v8": "^1.0.0",
    "@vitest/ui": "^1.0.0",
    "jsdom": "^23.0.0",
    "svelte": "^5.43.8",
    "vite": "^7.2.4",
    "vitest": "^1.0.0"
  }
}
```

### 8.6 Phase 6 Checklist

**Setup**
- [ ] Install test dependencies (vitest, playwright, testing-library)
- [ ] Create `vitest.config.ts`
- [ ] Create `playwright.config.ts`
- [ ] Create `tests/setup.ts`
- [ ] Update `package.json` scripts

**Unit Tests - Utils**
- [ ] Create `tests/unit/utils/format.test.ts`
- [ ] Create `tests/unit/utils/json.test.ts`

**Unit Tests - Stores**
- [ ] Create `tests/unit/stores/session.test.ts`
- [ ] Create `tests/unit/stores/chat.test.ts`
- [ ] Create `tests/unit/stores/events.test.ts`
- [ ] Create `tests/unit/stores/timeline.test.ts`
- [ ] Create `tests/unit/stores/agent.test.ts`
- [ ] Create `tests/unit/stores/spec.test.ts`
- [ ] Create `tests/unit/stores/setup.test.ts`

**Unit Tests - Services**
- [ ] Create `tests/unit/services/api.test.ts`
- [ ] Create `tests/unit/services/markdown.test.ts`

**Unit Tests - Components**
- [ ] Create `tests/unit/components/ui/Pill.test.ts`
- [ ] Create `tests/unit/components/ui/Tabs.test.ts`
- [ ] Create `tests/unit/components/ui/Empty.test.ts`

**E2E Tests**
- [ ] Create `tests/e2e/chat-flow.spec.ts`
- [ ] Create `tests/e2e/setup-config.spec.ts`
- [ ] Create `tests/e2e/spec-validation.spec.ts`
- [ ] Create `tests/e2e/events-display.spec.ts`

**CI Integration**
- [ ] Add test commands to CI workflow
- [ ] Configure coverage reporting
- [ ] Add Playwright browser installation to CI

---

## 9. Phase 7: Svelte 5 Slot Migration

### 9.1 Overview

Migrate all components from deprecated `<slot>` syntax to Svelte 5's `{@render}` pattern with Snippets. This ensures the codebase uses modern Svelte 5 idioms and eliminates deprecation warnings.

### 9.2 Migration Pattern

**Before (deprecated):**
```svelte
<script lang="ts">
  interface Props {
    class?: string;
  }
  let { class: className = '' }: Props = $props();
</script>

<div class={className}>
  <slot />
</div>
```

**After (Svelte 5):**
```svelte
<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    class?: string;
    children?: Snippet;
  }
  let { class: className = '', children }: Props = $props();
</script>

<div class={className}>
  {@render children?.()}
</div>
```

**Named slots migration:**
```svelte
// Before: <slot name="right" />
// After:  {@render right?.()}
// Prop:   right?: Snippet;
```

### 9.3 Components to Migrate

| Component | Slot Type | Location |
|-----------|-----------|----------|
| `Page.svelte` | default | `layout/` |
| `Column.svelte` | default | `layout/` |
| `Card.svelte` | default | `layout/` |
| `Pill.svelte` | default | `ui/` |
| `IconButton.svelte` | default | `ui/` |
| `Tabs.svelte` | named (`right`) | `ui/` |
| `LeftSidebar.svelte` | default | `sidebar-left/` |
| `SetupField.svelte` | default | `center/setup/` |

### 9.4 Phase 7 Checklist

**Layout Components**
- [ ] Migrate `Page.svelte`
- [ ] Migrate `Column.svelte`
- [ ] Migrate `Card.svelte`

**UI Components**
- [ ] Migrate `Pill.svelte`
- [ ] Migrate `IconButton.svelte`
- [ ] Migrate `Tabs.svelte` (named slot)

**Feature Components**
- [ ] Migrate `LeftSidebar.svelte`
- [ ] Migrate `SetupField.svelte`

**Verification**
- [ ] Run unit tests: `npm run test`
- [ ] Run E2E tests: `npm run test:e2e`
- [ ] Verify no `<slot>` deprecation warnings in build output
- [ ] Visual inspection of all migrated components

---

## 10. Implementation Checklists

### 10.1 Master Phase Checklist

| Phase | Description | Files | Risk | Dependencies |
|-------|-------------|-------|------|--------------|
| 1 | Extract Types & Utilities | 9 | Low | None |
| 2 | Create Stores | 8 | Medium | Phase 1 |
| 3 | Extract Services | 5 | Medium | Phases 1, 2 |
| 4 | Component Extraction | ~35 | High | Phases 1, 2, 3 |
| 5 | CSS Migration | 1 + components | Low | Phase 4 |
| 6 | Testing Infrastructure | ~20 | Low | All phases |
| 7 | Svelte 5 Slot Migration | 8 | Low | Phase 6 |

### 10.2 Pre-Flight Checklist (Before Starting)

- [ ] Create a new git branch: `git checkout -b refactor/playground-ui-v2`
- [ ] Ensure current app builds: `npm run build`
- [ ] Ensure current app runs: `npm run dev`
- [ ] Take screenshots of current UI for visual regression comparison
- [ ] Document any known bugs to avoid re-introducing them

### 9.3 Per-Phase Validation

After each phase, run these checks:

```bash
# Build check
npm run build

# Dev server check (manual)
npm run dev
# Then verify all features work in browser

# TypeScript check (add to package.json if needed)
npx tsc --noEmit

# After Phase 6: Run tests
npm run test
npm run test:e2e
```

### 9.4 Final App.svelte Target

After refactoring, `App.svelte` should be approximately:

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import Page from '$lib/components/layout/Page.svelte';
  import LeftSidebar from '$lib/components/sidebar-left/LeftSidebar.svelte';
  import CenterColumn from '$lib/components/center/CenterColumn.svelte';
  import RightSidebar from '$lib/components/sidebar-right/RightSidebar.svelte';
  import { loadMeta, loadSpec } from '$lib/services/api';
  import { agentStore, specStore } from '$lib/stores';
  import { chatStreamManager, eventStreamManager } from '$lib/services';

  onMount(() => {
    // Load initial data
    loadMeta().then(data => data && agentStore.setFromResponse(data));
    loadSpec().then(data => data && specStore.setFromSpecData(data));

    // Cleanup on unmount
    return () => {
      chatStreamManager.close();
      eventStreamManager.close();
    };
  });
</script>

<Page>
  <LeftSidebar />
  <CenterColumn />
  <RightSidebar />
</Page>
```

**Target: ~30 lines** (down from ~900)

---

## 10. Validation Criteria

### 10.1 Functional Requirements

All existing functionality must work after refactor:

| Feature | Test Method |
|---------|-------------|
| Chat send/receive | Manual + E2E |
| Streaming responses | Manual |
| Thinking/observations panel | Manual |
| Setup tab (session, contexts) | Manual + E2E |
| Spec YAML display | Manual |
| Spec validation | Manual + E2E |
| Project generation | Manual |
| Trajectory display | Manual |
| Events panel with filter | Manual |
| Config/Services/Tools display | Manual |
| Artifact streams | Manual |
| Pause/HITL display | Manual |
| Responsive layout | Manual |
| Auto-scroll on new messages | Manual |

### 10.2 Non-Functional Requirements

| Requirement | Criteria |
|-------------|----------|
| Build time | Should not increase >20% |
| Bundle size | Should not increase >10% |
| Initial load | Should not be noticeably slower |
| No regressions | All existing bugs fixed, no new ones |
| Code quality | Ruff/ESLint passes |
| Type safety | TypeScript strict mode passes |

### 10.3 Code Quality Metrics

| Metric | Before | Target After |
|--------|--------|--------------|
| Largest file | 900 lines | <100 lines |
| Total files | 2 | ~50 |
| Test coverage | 0% | >80% |
| Component depth | 1 | 3-4 max |
| Cyclomatic complexity | High | Medium-Low |

### 10.4 Acceptance Tests

Before merging, verify:

1. **Build passes**: `npm run build` succeeds
2. **Dev server works**: `npm run dev` starts without errors
3. **All manual tests pass**: Check each feature in browser
4. **Unit tests pass**: `npm run test` (after Phase 6)
5. **E2E tests pass**: `npm run test:e2e` (after Phase 6)
6. **No console errors**: Browser devtools clean
7. **Visual parity**: Screenshots match (allow for intentional improvements)

---

## 11. Notes & Recommendations

### 11.1 Playwright MCP

Yes, adding the Playwright MCP would be valuable for:
- Running E2E tests directly from Claude
- Debugging test failures interactively
- Visual regression testing

To add it:
```bash
# Install Playwright
npx playwright install

# The MCP server would need to be configured in your MCP settings
```

### 11.2 Suggested Order of Execution

For minimal risk and early validation:

1. **Phase 1** (Types/Utils) - Quick win, no behavior change
2. **Phase 6 partial** (Test setup only) - Get test infrastructure ready
3. **Phase 2** (Stores) - Core state management
4. **Phase 3** (Services) - Critical business logic
5. **Phase 4** (Components) - Most complex, do incrementally:
   - UI primitives first (can test immediately)
   - Layout components
   - Simple cards (left sidebar)
   - Right sidebar
   - Center column (most complex, do last)
6. **Phase 5** (CSS) - Polish after components work
7. **Phase 6** (Tests) - Complete test coverage

### 11.3 Rollback Strategy

If issues arise:
- Keep the old `App.svelte` as `App.svelte.backup`
- Each phase should be a separate commit
- If a phase breaks things, `git revert` to previous commit
- Test early and often

### 11.4 Future Enhancements Enabled

This refactor enables:
- **Theming**: CSS variables ready for dark mode
- **Component library**: UI primitives can be exported
- **Feature flags**: Stores can easily add feature toggles
- **Performance optimization**: Individual components can be lazy-loaded
- **A/B testing**: Swap components for experiments
- **Documentation**: Components are self-documenting with props interfaces

---

## Appendix A: File Count Summary

| Category | Files |
|----------|-------|
| Types | 5 |
| Utils | 4 |
| Stores | 8 |
| Services | 5 |
| UI Components | 7 |
| Layout Components | 3 |
| Left Sidebar | 5 |
| Center Column | 15 |
| Right Sidebar | 12 |
| Test Setup | 3 |
| Unit Tests | ~15 |
| E2E Tests | 4 |
| **Total** | **~81 files** |

---

## Appendix B: Import Alias Configuration

Update `vite.config.js` to support `$lib` alias:

```javascript
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'path';

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: path.resolve('./src/lib')
    }
  }
});
```

And add to `tsconfig.json`:

```json
{
  "compilerOptions": {
    "paths": {
      "$lib/*": ["./src/lib/*"]
    }
  }
}
```

---

*Document Version: 1.0*
*Created: December 2024*
*Target Completion: TBD based on phase execution*

