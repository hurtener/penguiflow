# RFC: Frontend Gold Standard Refactoring Plan

> **Status**: Draft
> **Target**: `penguiflow/cli/playground_ui`
> **Scope**: Architecture, patterns, performance, testability, stability

---

## Executive Summary

This document outlines a comprehensive refactoring plan to elevate the playground UI frontend to gold-standard quality. The goals are:

- **Testability**: Every component testable in isolation
- **Stability**: Graceful error handling, no cascade failures
- **Extensibility**: Easy to add new renderers, features, integrations
- **Consistency**: One canonical way to solve each problem
- **Performance**: Optimized bundle size and runtime efficiency

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Phase 1: Type Safety & Strict Mode](#2-phase-1-type-safety--strict-mode)
3. [Phase 2: Component Architecture Standardization](#3-phase-2-component-architecture-standardization)
4. [Phase 3: State Management Consolidation](#4-phase-3-state-management-consolidation)
5. [Phase 4: Error Boundaries & Resilience](#5-phase-4-error-boundaries--resilience)
6. [Phase 5: Code Splitting & Performance](#6-phase-5-code-splitting--performance)
7. [Phase 6: Testing Strategy](#7-phase-6-testing-strategy)
8. [Phase 7: Developer Experience](#8-phase-7-developer-experience)
9. [Migration Strategy](#9-migration-strategy)
10. [Acceptance Criteria](#10-acceptance-criteria)

---

## 1. Current State Assessment

### Strengths
- Clear folder structure with domain separation
- Svelte 5 runes adoption (`$state`, `$derived`, `$props`)
- TypeScript throughout
- Good test coverage (279 unit + 53 e2e)

### Issues to Address

| Category | Issue | Impact |
|----------|-------|--------|
| Types | `any` types in renderer registry | Type safety holes |
| Bundle | 6.8MB main bundle, no code splitting | Slow initial load |
| Patterns | Mixed event handling (dispatch, callbacks, stores) | Cognitive overhead |
| Errors | No error boundaries | Cascade failures |
| State | Overlapping store responsibilities | Confusion, bugs |
| Components | Some components have multiple responsibilities | Hard to test |

---

## 2. Phase 1: Type Safety & Strict Mode

### 2.1 Enable Strict TypeScript

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noUncheckedIndexedAccess": true
  }
}
```

### 2.2 Eliminate All `any` Types

**Current (Bad)**
```typescript
// ComponentRenderer.svelte
const renderers: Record<string, any> = { ... };
```

**Target (Good)**
```typescript
// types/renderer.ts
import type { Component } from 'svelte';

export interface RendererProps {
  onResult?: (result: unknown) => void;
  [key: string]: unknown;
}

export type RendererComponent = Component<RendererProps>;

// ComponentRenderer.svelte
const renderers: Record<string, RendererComponent> = { ... };
```

### 2.3 Define Strict Component Prop Interfaces

Every component must have an explicit `Props` interface:

```typescript
// Pattern for all components
interface Props {
  // Required props first
  data: DataType;

  // Optional props with defaults
  variant?: 'primary' | 'secondary';
  disabled?: boolean;

  // Event callbacks (consistent naming)
  onchange?: (value: string) => void;
  onsubmit?: (data: FormData) => void;
}

let {
  data,
  variant = 'primary',
  disabled = false,
  onchange,
  onsubmit
}: Props = $props();
```

### 2.4 Create Shared Type Definitions

```
src/lib/types/
  index.ts              # Public exports
  components.ts         # Component prop patterns
  renderers.ts          # Renderer system types
  events.ts             # Event payload types
  stores.ts             # Store state types
  api.ts                # API response types
```

### Deliverables
- [ ] `tsconfig.json` with strict mode enabled
- [ ] Zero `any` types in codebase
- [ ] Shared types module with full coverage
- [ ] All component Props interfaces documented

---

## 3. Phase 2: Component Architecture Standardization

### 3.1 Component Categories

Define clear categories with distinct responsibilities:

```
src/lib/components/
  primitives/           # Atomic UI elements (Button, Input, Badge)
  composites/           # Composed from primitives (FormField, Card)
  containers/           # Layout and state management (Column, ErrorBoundary)
  features/             # Domain-specific (ChatBody, SetupTab)
  renderers/            # Dynamic content renderers (Markdown, ECharts)
```

### 3.2 Single Responsibility Principle

**Current (Mixed Concerns)**
```svelte
<!-- ChatCard.svelte: handles tabs, state, routing, rendering -->
<script>
  let activeTab = $state('chat');
  const tabs = $derived.by(() => { ... });
  // ... 50+ lines
</script>
```

**Target (Separated)**
```svelte
<!-- ChatCard.svelte: orchestration only -->
<script>
  import { TabContainer } from '$lib/components/composites';
  import ChatTab from './ChatTab.svelte';
  import SetupTab from './SetupTab.svelte';
  import ComponentsTab from './ComponentsTab.svelte';

  const tabs = [
    { id: 'chat', label: 'Chat', component: ChatTab },
    { id: 'setup', label: 'Setup', component: SetupTab },
    { id: 'components', label: 'Components', component: ComponentsTab, condition: () => registryEnabled }
  ];
</script>

<Card>
  <ChatHeader />
  <TabContainer {tabs} />
</Card>
```

### 3.3 Composition Over Configuration

**Pattern: Compound Components**
```svelte
<!-- Usage -->
<DataGrid {data}>
  <DataGrid.Toolbar>
    <DataGrid.Search />
    <DataGrid.Export format="csv" />
  </DataGrid.Toolbar>
  <DataGrid.Table sortable selectable />
  <DataGrid.Pagination pageSize={10} />
</DataGrid>
```

### 3.4 Standardized Component Template

```svelte
<!--
  @component ComponentName
  @description Brief description of purpose
  @example
  <ComponentName data={myData} onchange={handleChange} />
-->
<script lang="ts">
  import type { Snippet } from 'svelte';

  // Types
  interface Props {
    // ... typed props
  }

  // Props destructuring with defaults
  let { ... }: Props = $props();

  // Derived state
  const computed = $derived(...);

  // Local state
  let localState = $state(...);

  // Effects (side effects only)
  $effect(() => { ... });

  // Event handlers (named functions, not inline)
  function handleClick() { ... }
  function handleSubmit() { ... }
</script>

<!-- Template: minimal logic, declarative -->
<div class="component-name">
  ...
</div>

<style>
  /* Scoped styles only */
</style>
```

### Deliverables
- [ ] Component category folders restructured
- [ ] ChatCard split into focused sub-components
- [ ] Component template documented in CONTRIBUTING.md
- [ ] All components follow single responsibility

---

## 4. Phase 3: State Management Consolidation

### 4.1 Store Architecture

Define clear boundaries for each store:

```
src/lib/stores/
  index.ts                    # Public API exports only

  # Domain stores (business logic)
  chat.svelte.ts              # Chat messages, streaming state
  session.svelte.ts           # Session ID, tenant, user
  artifacts.svelte.ts         # Downloaded artifacts

  # UI stores (presentation state)
  ui/
    layout.svelte.ts          # Sidebar state, mobile drawer
    notifications.svelte.ts   # Toast messages, alerts

  # Feature stores (isolated features)
  features/
    component-registry.svelte.ts   # Renderer registry
    component-artifacts.svelte.ts  # Pending interactions
```

### 4.2 Store Pattern: Single Factory Function

**Canonical Pattern**
```typescript
// stores/chat.svelte.ts
import { getContext, setContext } from 'svelte';

const CHAT_KEY = Symbol('chat');

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
}

export interface ChatStore {
  // Readonly state
  readonly messages: ChatMessage[];
  readonly isStreaming: boolean;
  readonly error: string | null;

  // Actions
  addUserMessage(text: string): void;
  addAgentMessage(id: string, text: string): void;
  appendToMessage(id: string, delta: string): void;
  setStreaming(value: boolean): void;
  setError(error: string | null): void;
  clear(): void;
}

function createChatStore(): ChatStore {
  let messages = $state<ChatMessage[]>([]);
  let isStreaming = $state(false);
  let error = $state<string | null>(null);

  return {
    get messages() { return messages; },
    get isStreaming() { return isStreaming; },
    get error() { return error; },

    addUserMessage(text: string) {
      messages = [...messages, {
        id: crypto.randomUUID(),
        role: 'user',
        text,
        timestamp: Date.now()
      }];
    },

    addAgentMessage(id: string, text: string) {
      messages = [...messages, { id, role: 'agent', text, timestamp: Date.now() }];
    },

    appendToMessage(id: string, delta: string) {
      messages = messages.map(m =>
        m.id === id ? { ...m, text: m.text + delta } : m
      );
    },

    setStreaming(value: boolean) {
      isStreaming = value;
    },

    setError(err: string | null) {
      error = err;
    },

    clear() {
      messages = [];
      error = null;
    }
  };
}

// Context-based access (for nested components)
export function setChatStore() {
  return setContext(CHAT_KEY, createChatStore());
}

export function getChatStore(): ChatStore {
  return getContext(CHAT_KEY);
}

// Module-level singleton (for services, top-level)
export const chatStore = createChatStore();
```

### 4.3 Communication Patterns

**Rule: One Pattern Per Use Case**

| Use Case | Pattern | Example |
|----------|---------|---------|
| Parent → Child | Props | `<Child {data} />` |
| Child → Parent | Callback props | `<Child onchange={handler} />` |
| Siblings | Shared store | `chatStore.addMessage()` |
| Deep nesting | Context | `getChatStore()` |
| Global events | Custom events | `dispatchEvent(new CustomEvent(...))` |

**Banned Patterns**
- ❌ `createEventDispatcher` (Svelte 4 legacy)
- ❌ Direct store mutations from components
- ❌ Prop drilling beyond 2 levels

### 4.4 Remove Store Overlap

**Current Overlap**
```
artifacts.svelte.ts           # Stores artifact metadata
component_artifacts.svelte.ts # Also stores artifacts + interactions
```

**Target: Clear Boundaries**
```
artifacts.svelte.ts           # File artifacts (downloads, previews)
interactions.svelte.ts        # Pending user interactions (forms, confirms)
```

### Deliverables
- [ ] Store architecture diagram
- [ ] All stores follow factory pattern
- [ ] Communication pattern guide in CONTRIBUTING.md
- [ ] Zero store overlap
- [ ] Remove all `createEventDispatcher` usage

---

## 5. Phase 4: Error Boundaries & Resilience

### 5.1 Error Boundary Component

```svelte
<!-- src/lib/components/containers/ErrorBoundary.svelte -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';

  interface Props {
    fallback?: Snippet<[Error]>;
    onError?: (error: Error, componentStack: string) => void;
    children: Snippet;
  }

  let { fallback, onError, children }: Props = $props();

  let error = $state<Error | null>(null);
  let componentStack = $state<string>('');

  onMount(() => {
    const handler = (event: ErrorEvent) => {
      error = event.error;
      componentStack = event.error?.stack || '';
      onError?.(event.error, componentStack);
      event.preventDefault();
    };

    window.addEventListener('error', handler);
    return () => window.removeEventListener('error', handler);
  });

  function reset() {
    error = null;
  }
</script>

{#if error}
  {#if fallback}
    {@render fallback(error)}
  {:else}
    <div class="error-boundary">
      <h3>Something went wrong</h3>
      <p>{error.message}</p>
      <button onclick={reset}>Try again</button>
    </div>
  {/if}
{:else}
  {@render children()}
{/if}
```

### 5.2 Strategic Boundary Placement

```svelte
<!-- App.svelte -->
<ErrorBoundary onError={logToService}>
  <Layout>
    <!-- Each major section has its own boundary -->
    <ErrorBoundary fallback={sidebarFallback}>
      <LeftSidebar />
    </ErrorBoundary>

    <ErrorBoundary fallback={chatFallback}>
      <CenterColumn />
    </ErrorBoundary>

    <ErrorBoundary fallback={configFallback}>
      <RightSidebar />
    </ErrorBoundary>
  </Layout>
</ErrorBoundary>
```

### 5.3 Renderer-Level Boundaries

Each dynamic renderer must be wrapped:

```svelte
<!-- ComponentRenderer.svelte -->
<ErrorBoundary fallback={rendererErrorFallback}>
  <Renderer {...props} {onResult} />
</ErrorBoundary>

{#snippet rendererErrorFallback(error)}
  <div class="renderer-error">
    <span class="icon">⚠️</span>
    <span>Failed to render {component}: {error.message}</span>
  </div>
{/snippet}
```

### 5.4 Async Error Handling

```typescript
// services/api.ts
export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public details?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function fetchWithErrorHandling<T>(
  url: string,
  options?: RequestInit
): Promise<Result<T, ApiError>> {
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      return { ok: false, error: new ApiError(response.statusText, response.status) };
    }
    return { ok: true, data: await response.json() };
  } catch (err) {
    return { ok: false, error: new ApiError('Network error', 0, err) };
  }
}
```

### 5.5 Result Type Pattern

```typescript
// types/result.ts
export type Result<T, E = Error> =
  | { ok: true; data: T }
  | { ok: false; error: E };

// Usage
const result = await fetchWithErrorHandling<Meta>('/api/meta');
if (result.ok) {
  console.log(result.data);
} else {
  console.error(result.error.message);
}
```

### Deliverables
- [ ] `ErrorBoundary` component implemented
- [ ] All major sections wrapped
- [ ] All renderers wrapped
- [ ] `Result<T, E>` pattern for async operations
- [ ] Error logging service integration

---

## 6. Phase 5: Code Splitting & Performance

### 6.1 Dynamic Renderer Imports

**Current (All Loaded Upfront)**
```typescript
import ECharts from './renderers/ECharts.svelte';
import Mermaid from './renderers/Mermaid.svelte';
import Plotly from './renderers/Plotly.svelte';
// ... 20 more
```

**Target (Lazy Loaded)**
```typescript
// renderers/registry.ts
export const rendererRegistry = {
  echarts: () => import('./ECharts.svelte'),
  mermaid: () => import('./Mermaid.svelte'),
  plotly: () => import('./Plotly.svelte'),
  // Lightweight renderers can be eager
  markdown: () => Promise.resolve(Markdown),
  json: () => Promise.resolve(Json),
} as const;

export type RendererName = keyof typeof rendererRegistry;
```

```svelte
<!-- ComponentRenderer.svelte -->
<script lang="ts">
  import { rendererRegistry, type RendererName } from './registry';

  interface Props {
    component: RendererName;
    props?: Record<string, unknown>;
  }

  let { component, props = {} }: Props = $props();

  const RendererPromise = $derived(rendererRegistry[component]?.());
</script>

{#await RendererPromise}
  <div class="renderer-loading">
    <Spinner size="sm" />
  </div>
{:then module}
  {@const Renderer = module.default}
  <ErrorBoundary>
    <Renderer {...props} />
  </ErrorBoundary>
{:catch error}
  <div class="renderer-error">
    Failed to load {component}: {error.message}
  </div>
{/await}
```

### 6.2 Route-Based Code Splitting

If the playground grows to have routes:

```typescript
// routes.ts
export const routes = {
  '/': () => import('./pages/Playground.svelte'),
  '/components': () => import('./pages/ComponentLab.svelte'),
  '/docs': () => import('./pages/Documentation.svelte'),
};
```

### 6.3 Bundle Analysis

Add bundle analysis tooling:

```json
// package.json
{
  "scripts": {
    "build:analyze": "vite build --mode analyze",
    "bundle:report": "npx vite-bundle-visualizer"
  }
}
```

### 6.4 Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| Main bundle | 6.8 MB | < 500 KB |
| Initial load (3G) | ~8s | < 2s |
| Time to Interactive | ~5s | < 1.5s |
| Lighthouse Score | ~60 | > 90 |

### 6.5 Optimization Checklist

- [ ] Heavy libs lazy loaded (Mermaid, Plotly, Cytoscape, KaTeX)
- [ ] Image optimization (if any)
- [ ] CSS purging enabled
- [ ] Preload critical chunks
- [ ] Service worker for caching (optional)

### Deliverables
- [ ] Renderer lazy loading implemented
- [ ] Bundle size < 500KB initial
- [ ] Bundle analyzer in CI
- [ ] Performance budget enforced

---

## 7. Phase 6: Testing Strategy

### 7.1 Testing Pyramid

```
         /\
        /  \     E2E Tests (53 → 80)
       /----\    Critical user journeys
      /      \
     /--------\  Integration Tests (new)
    /          \ Component interactions, store logic
   /------------\
  /              \ Unit Tests (279 → 400+)
 /                \ Pure functions, isolated components
/------------------\
```

### 7.2 Component Testing Pattern

```typescript
// ComponentName.test.ts
import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ComponentName from './ComponentName.svelte';

describe('ComponentName', () => {
  // Rendering
  describe('rendering', () => {
    it('renders with required props', () => {
      render(ComponentName, { props: { data: mockData } });
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('renders empty state when no data', () => {
      render(ComponentName, { props: { data: [] } });
      expect(screen.getByText('No items')).toBeInTheDocument();
    });
  });

  // Interactions
  describe('interactions', () => {
    it('calls onchange when clicked', async () => {
      const onchange = vi.fn();
      render(ComponentName, { props: { data: mockData, onchange } });

      await fireEvent.click(screen.getByRole('button'));

      expect(onchange).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }));
    });
  });

  // Edge cases
  describe('edge cases', () => {
    it('handles undefined optional props', () => {
      expect(() => render(ComponentName, { props: { data: mockData } })).not.toThrow();
    });
  });
});
```

### 7.3 Store Testing Pattern

```typescript
// stores/chat.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { chatStore } from './chat.svelte';

describe('chatStore', () => {
  beforeEach(() => {
    chatStore.clear();
  });

  it('adds user message', () => {
    chatStore.addUserMessage('Hello');

    expect(chatStore.messages).toHaveLength(1);
    expect(chatStore.messages[0].role).toBe('user');
    expect(chatStore.messages[0].text).toBe('Hello');
  });

  it('appends to existing message', () => {
    chatStore.addAgentMessage('msg-1', 'Hello');
    chatStore.appendToMessage('msg-1', ' World');

    expect(chatStore.messages[0].text).toBe('Hello World');
  });
});
```

### 7.4 Integration Testing

```typescript
// tests/integration/chat-flow.test.ts
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import App from '$lib/App.svelte';
import { chatStore } from '$lib/stores';
import { mockAgentResponse } from '../mocks/agent';

describe('Chat Flow Integration', () => {
  it('sends message and displays response', async () => {
    mockAgentResponse('Hello! How can I help?');

    render(App);

    const input = screen.getByPlaceholderText('Type your message');
    await fireEvent.input(input, { target: { value: 'Hi' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument();
    });
  });
});
```

### 7.5 Visual Regression Testing (Optional)

```typescript
// tests/visual/components.test.ts
import { test, expect } from '@playwright/test';

test('DataGrid visual regression', async ({ page }) => {
  await page.goto('/component-lab?component=datagrid');
  await expect(page.locator('.datagrid')).toHaveScreenshot('datagrid-default.png');
});
```

### 7.6 Coverage Requirements

| Category | Current | Target |
|----------|---------|--------|
| Statements | 84% | > 90% |
| Branches | ~75% | > 85% |
| Functions | ~80% | > 90% |
| Lines | 84% | > 90% |

### Deliverables
- [ ] Component test template documented
- [ ] All components have unit tests
- [ ] Integration test suite for critical flows
- [ ] Coverage thresholds enforced in CI
- [ ] Visual regression tests for renderers (optional)

---

## 8. Phase 7: Developer Experience

### 8.1 Component Documentation (Storybook Alternative)

Since we have `ComponentLab`, enhance it to serve as living documentation:

```svelte
<!-- ComponentLab.svelte - Enhanced -->
<script>
  // Add markdown documentation support
  const componentDocs = {
    datagrid: {
      description: 'Renders tabular data with sorting, filtering, and pagination.',
      props: [
        { name: 'columns', type: 'Column[]', required: true, description: '...' },
        { name: 'rows', type: 'Row[]', required: true, description: '...' },
      ],
      examples: [
        { name: 'Basic', props: { ... } },
        { name: 'With Pagination', props: { ... } },
      ]
    }
  };
</script>
```

### 8.2 CONTRIBUTING.md

```markdown
# Contributing to Playground UI

## Component Guidelines

### Creating a New Component

1. Create file in appropriate category folder
2. Use the component template (see templates/Component.svelte)
3. Add Props interface with JSDoc comments
4. Write unit tests before implementing
5. Add to ComponentLab if it's a renderer

### Code Style

- Props: Required first, optional with defaults
- Events: `on{eventname}` callback props, no dispatchers
- State: Prefer `$derived` over `$effect` where possible
- Naming: PascalCase components, camelCase functions

### Testing

```bash
npm run test          # Unit tests
npm run test:e2e      # E2E tests
npm run test:coverage # With coverage report
```

### Performance

- Lazy load heavy dependencies
- Use `$derived` for computed values
- Avoid unnecessary `$effect` calls
```

### 8.3 Code Generation Scripts

```bash
# scripts/new-component.sh
#!/bin/bash
NAME=$1
CATEGORY=$2

mkdir -p "src/lib/components/$CATEGORY"
cat > "src/lib/components/$CATEGORY/$NAME.svelte" << EOF
<script lang="ts">
  interface Props {
    // Add props here
  }

  let { }: Props = \$props();
</script>

<div class="${NAME,,}">
  <!-- Component content -->
</div>

<style>
  .${NAME,,} {
    /* Styles */
  }
</style>
EOF

cat > "src/lib/components/$CATEGORY/$NAME.test.ts" << EOF
import { render } from '@testing-library/svelte';
import { describe, it, expect } from 'vitest';
import $NAME from './$NAME.svelte';

describe('$NAME', () => {
  it('renders', () => {
    const { container } = render($NAME);
    expect(container).toBeTruthy();
  });
});
EOF

echo "Created $NAME component in $CATEGORY"
```

### 8.4 Pre-commit Hooks

```json
// package.json
{
  "scripts": {
    "lint": "eslint . && svelte-check",
    "format": "prettier --write .",
    "precommit": "npm run lint && npm run test"
  }
}
```

### 8.5 VS Code Workspace Settings

```json
// .vscode/settings.json
{
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "svelte.enable-ts-plugin": true,
  "typescript.preferences.importModuleSpecifier": "relative"
}
```

### Deliverables
- [ ] CONTRIBUTING.md with all patterns
- [ ] Component generator script
- [ ] Pre-commit hooks configured
- [ ] VS Code settings for team consistency
- [ ] Enhanced ComponentLab as documentation

---

## 9. Migration Strategy

### Phase Execution Order

```
Phase 1: Type Safety (Week 1-2)
    ↓ Foundation for all other work
Phase 2: Component Architecture (Week 2-4)
    ↓ Restructure without breaking
Phase 3: State Management (Week 3-5)
    ↓ Can overlap with Phase 2
Phase 4: Error Boundaries (Week 4-5)
    ↓ Quick win, high impact
Phase 5: Code Splitting (Week 5-6)
    ↓ Performance gains
Phase 6: Testing (Ongoing)
    ↓ Add tests as you refactor
Phase 7: Developer Experience (Week 6-7)
    ↓ Polish and documentation
```

### Incremental Migration Rules

1. **No Big Bang**: Refactor one component/store at a time
2. **Tests First**: Write tests for existing behavior before changing
3. **Feature Freeze**: No new features during core refactoring
4. **Backwards Compat**: Old patterns work until fully migrated
5. **Review Gates**: Each phase requires PR review before next

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing functionality | Comprehensive test suite first |
| Performance regression | Bundle size checks in CI |
| Team confusion during migration | Clear documentation, pair programming |
| Scope creep | Strict phase boundaries |

---

## 10. Acceptance Criteria

### Phase 1: Type Safety
- [ ] `strict: true` in tsconfig with zero errors
- [ ] No `any` types in codebase (enforced by ESLint)
- [ ] All component props have interfaces
- [ ] Shared types module complete

### Phase 2: Component Architecture
- [ ] All components follow single responsibility
- [ ] Component categories documented and enforced
- [ ] No component > 200 lines
- [ ] Compound component pattern for complex UI

### Phase 3: State Management
- [ ] All stores follow factory pattern
- [ ] No `createEventDispatcher` usage
- [ ] Communication patterns documented
- [ ] Zero store responsibility overlap

### Phase 4: Error Boundaries
- [ ] ErrorBoundary component implemented
- [ ] All major sections wrapped
- [ ] All renderers wrapped
- [ ] Result type pattern for async

### Phase 5: Performance
- [ ] Initial bundle < 500KB
- [ ] Lighthouse score > 90
- [ ] All heavy libs lazy loaded
- [ ] Bundle analysis in CI

### Phase 6: Testing
- [ ] 90%+ code coverage
- [ ] All components have unit tests
- [ ] Integration tests for critical paths
- [ ] E2E tests for user journeys

### Phase 7: DX
- [ ] CONTRIBUTING.md complete
- [ ] Component generator working
- [ ] Pre-commit hooks active
- [ ] ComponentLab as documentation

---

## Appendix A: File Structure (Target)

```
src/lib/
├── components/
│   ├── primitives/
│   │   ├── Button.svelte
│   │   ├── Input.svelte
│   │   ├── Badge.svelte
│   │   └── Spinner.svelte
│   ├── composites/
│   │   ├── Card.svelte
│   │   ├── FormField.svelte
│   │   ├── TabContainer.svelte
│   │   └── Modal.svelte
│   ├── containers/
│   │   ├── ErrorBoundary.svelte
│   │   ├── Column.svelte
│   │   └── Layout.svelte
│   └── features/
│       ├── chat/
│       │   ├── ChatCard.svelte
│       │   ├── ChatBody.svelte
│       │   ├── ChatInput.svelte
│       │   └── Message.svelte
│       └── setup/
│           └── SetupTab.svelte
├── renderers/
│   ├── registry.ts           # Lazy loader registry
│   ├── ComponentRenderer.svelte
│   ├── Markdown.svelte
│   ├── Json.svelte
│   └── ... (other renderers)
├── stores/
│   ├── index.ts
│   ├── chat.svelte.ts
│   ├── session.svelte.ts
│   ├── artifacts.svelte.ts
│   └── ui/
│       └── layout.svelte.ts
├── services/
│   ├── api.ts
│   ├── chat-stream.ts
│   └── error-logger.ts
├── types/
│   ├── index.ts
│   ├── components.ts
│   ├── renderers.ts
│   └── api.ts
└── utils/
    ├── format.ts
    └── result.ts
```

---

## Appendix B: ESLint Rules to Enforce

```javascript
// eslint.config.js
export default [
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/explicit-function-return-type': 'warn',
      'max-lines-per-function': ['warn', 50],
      'max-depth': ['error', 3],
      'complexity': ['warn', 10],
    }
  }
];
```

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2024-12-28 | Claude | Initial draft |

