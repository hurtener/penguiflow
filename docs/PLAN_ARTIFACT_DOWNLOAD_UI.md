# Implementation Plan: Artifact Download UI + UI Restructuring

## Executive Summary

Add download functionality for binary artifacts (PDFs, images, Tableau exports) to the Playground UI, **combined with a significant UI restructuring** to improve semantic organization. The backend infrastructure is **already complete**—this plan focuses on frontend changes.

---

## UI Restructuring Overview

### Motivation

The current layout mixes static reference data with dynamic execution data. This restructuring:
1. Groups **static agent reference** info in the left sidebar
2. Groups **dynamic execution outputs** in the right sidebar
3. Removes unused Validate/Generate functionality (kept but disabled)
4. Adds artifact download capability

### Current Layout (Before)

**Desktop:**
```
Left Sidebar              Center              Right Sidebar
├── ProjectCard           ├── ChatCard        ├── EventsCard
├── SpecCard              └── TrajectoryCard  └── ConfigCard
└── GeneratorCard (Validate/Generate + stepper)
```

**Mobile:**
```
Header (drawer): Info | Spec | Actions (GeneratorCard)
Bottom (panel):  Steps | Events | Config
```

### Target Layout (After)

**Desktop:**
```
Left Sidebar              Center              Right Sidebar
├── ProjectCard           ├── ChatCard        ├── EventsCard
├── SpecCard              └── TrajectoryCard  └── ArtifactsCard (NEW)
└── ConfigCard (moved)
```

**Mobile:**
```
Header (drawer): Info | Spec | Config
Bottom (panel):  Steps | Events | Artifacts
```

### Semantic Organization

| Left Sidebar (Static Reference) | Right Sidebar (Dynamic Execution) |
|--------------------------------|-----------------------------------|
| ProjectCard - Agent identity | EventsCard - Real-time planner events |
| SpecCard - Agent definition (YAML) | ArtifactsCard - Downloadable outputs |
| ConfigCard - Agent configuration | |

---

## Implementation Phases

### Phase 0: UI Restructuring (NEW)

#### 0.1 Remove GeneratorCard from UI (Keep Files)

**Files to keep (do NOT delete):**
- `src/lib/components/sidebar-left/GeneratorCard.svelte`
- `src/lib/components/sidebar-left/GeneratorStepper.svelte`

**Files to modify:**

**`src/lib/components/sidebar-left/index.ts`** - Remove GeneratorCard export:
```typescript
// BEFORE
export { default as GeneratorCard } from './GeneratorCard.svelte';
// AFTER - Comment out or remove this line
// export { default as GeneratorCard } from './GeneratorCard.svelte'; // DISABLED - not currently used
```

**`src/App.svelte`** - Remove GeneratorCard from desktop and mobile:
```svelte
// Desktop: Remove from LeftSidebar
<LeftSidebar>
  <ProjectCard />
  <SpecCard />
  <!-- GeneratorCard removed - not currently used -->
  <ConfigCard />  <!-- MOVED from RightSidebar -->
</LeftSidebar>

// Mobile: Update MobileHeader tabs
<MobileHeader>
  {#snippet infoContent()}
    <ProjectCard />
  {/snippet}
  {#snippet specContent()}
    <SpecCard />
  {/snippet}
  {#snippet configContent()}  <!-- RENAMED from actionsContent -->
    <ConfigCard />  <!-- CHANGED from GeneratorCard -->
  {/snippet}
</MobileHeader>
```

#### 0.2 Move ConfigCard to Left Sidebar

**`src/lib/components/sidebar-right/RightSidebar.svelte`** - Remove ConfigCard:
```svelte
<script lang="ts">
  import { Column } from '$lib/components/layout';
  import { EventsCard } from './events';
  // ConfigCard import removed - moved to left sidebar
  import { ArtifactsCard } from './artifacts';  // NEW
</script>

<Column position="right">
  <EventsCard />
  <ArtifactsCard />  <!-- NEW - replaces ConfigCard -->
</Column>
```

**`src/App.svelte`** - Add ConfigCard import and use in LeftSidebar:
```svelte
import { ConfigCard } from '$lib/components/sidebar-right/config';
// ... in LeftSidebar:
<LeftSidebar>
  <ProjectCard />
  <SpecCard />
  <ConfigCard />
</LeftSidebar>
```

#### 0.3 Update MobileHeader Tabs

**`src/lib/components/mobile/MobileHeader.svelte`** - Update tab labels:
```typescript
const tabs = [
  { id: 'info', label: 'Info' },
  { id: 'spec', label: 'Spec' },
  { id: 'config', label: 'Config' }  // CHANGED from 'Actions'
] as const;
```

Update Props interface:
```typescript
interface Props {
  infoContent?: Snippet;
  specContent?: Snippet;
  configContent?: Snippet;  // RENAMED from actionsContent
}
```

#### 0.4 Update MobileBottomPanel Tabs

**`src/lib/components/mobile/MobileBottomPanel.svelte`** - Update tabs:
```typescript
const tabs = [
  { id: 'trajectory', label: 'Steps' },
  { id: 'events', label: 'Events' },
  { id: 'artifacts', label: 'Artifacts' }  // CHANGED from 'Config'
] as const;
```

Update Props interface:
```typescript
interface Props {
  trajectoryContent?: Snippet;
  eventsContent?: Snippet;
  artifactsContent?: Snippet;  // RENAMED from configContent
}
```

#### 0.5 Update App.svelte Mobile Layout

```svelte
<MobileBottomPanel>
  {#snippet trajectoryContent()}
    <TrajectoryCard />
  {/snippet}
  {#snippet eventsContent()}
    <EventsCard />
  {/snippet}
  {#snippet artifactsContent()}  <!-- RENAMED from configContent -->
    <ArtifactsCard />  <!-- CHANGED from ConfigCard -->
  {/snippet}
</MobileBottomPanel>
```

---

## Current State Analysis

### Backend (Complete ✓)
| Component | Status | Location |
|-----------|--------|----------|
| `GET /artifacts/{id}` | ✓ Ready | `playground.py:842-897` |
| `GET /artifacts/{id}/meta` | ✓ Ready | `playground.py:899-936` |
| `artifact_stored` SSE event | ✓ Ready | `playground.py:267-279` |
| `PlaygroundArtifactStore` | ✓ Ready | `playground_state.py:57-213` |
| Session validation | ✓ Ready | `get_with_session_check()` |

### Frontend (Needs Work)
| Component | Status | Issue |
|-----------|--------|-------|
| `ArtifactStreams.svelte` | Partial | Shows raw JSON, no download |
| Artifact state tracking | Partial | Chunks tracked, not final refs |
| Download triggers | Missing | No buttons or links |
| Type definitions | Partial | Missing ArtifactRef interface |

---

## Architecture Decision

### Option A: Dedicated Artifacts Panel (Recommended)
Create a new `ArtifactsCard.svelte` in the right sidebar that:
- Displays all session artifacts as a list
- Shows filename, size, MIME type
- Has download button per artifact
- Supports multi-download (future: zip)

**Why:** Clean separation, doesn't clutter existing components, visible throughout session.

### Option B: Inline in Message/Observation
Embed download buttons directly in `Message.svelte` where artifacts appear.

**Why not:** Artifacts may appear in observations (thinking panel) which is collapsible; harder to find.

### Option C: Both (Phase 2)
Start with dedicated panel, then add inline hints that link to the panel.

---

## Implementation Plan

### Phase 1: Type Definitions & Store (Foundation)

#### 1.1 Add ArtifactRef Type
**File:** `src/lib/types/artifacts.ts` (NEW)

```typescript
export interface ArtifactRef {
  id: string;
  mime_type: string | null;
  size_bytes: number | null;
  filename: string | null;
  sha256: string | null;
  source: Record<string, unknown>;
}

export interface ArtifactStoredEvent {
  artifact_id: string;
  mime_type: string;
  size_bytes: number;
  filename: string;
  source: Record<string, unknown>;
  trace_id: string;
  session_id: string;
  ts: number;
}
```

**Why:** Type safety for artifact handling throughout the codebase.

#### 1.2 Create Artifacts Store
**File:** `src/lib/stores/artifacts.svelte.ts` (NEW)

```typescript
import type { ArtifactRef } from '$lib/types/artifacts';

function createArtifactsStore() {
  let artifacts = $state<Map<string, ArtifactRef>>(new Map());

  return {
    get artifacts() { return artifacts; },
    get list() { return Array.from(artifacts.values()); },
    get count() { return artifacts.size; },

    addArtifact(event: ArtifactStoredEvent): void {
      artifacts.set(event.artifact_id, {
        id: event.artifact_id,
        mime_type: event.mime_type,
        size_bytes: event.size_bytes,
        filename: event.filename,
        sha256: null,
        source: event.source
      });
    },

    clear(): void {
      artifacts = new Map();
    }
  };
}

export const artifactsStore = createArtifactsStore();
```

**Why:** Centralized artifact tracking separate from raw stream chunks.

#### 1.3 Export from stores/index.ts
```typescript
export { artifactsStore } from './artifacts.svelte';
```

---

### Phase 2: API Service Layer

#### 2.1 Add Download Functions to api.ts
**File:** `src/lib/services/api.ts` (MODIFY)

```typescript
/**
 * Download artifact binary as blob.
 * Triggers browser download with proper filename.
 */
export async function downloadArtifact(
  artifactId: string,
  sessionId: string,
  filename?: string
): Promise<void> {
  const response = await fetch(`${API_BASE}/artifacts/${artifactId}`, {
    headers: {
      'X-Session-ID': sessionId
    }
  });

  if (!response.ok) {
    throw new Error(`Download failed: ${response.status}`);
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get('Content-Disposition');
  const inferredFilename = filename
    || extractFilename(contentDisposition)
    || `artifact-${artifactId}`;

  // Trigger browser download
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = inferredFilename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Get artifact metadata without downloading content.
 */
export async function getArtifactMeta(
  artifactId: string,
  sessionId: string
): Promise<ArtifactRef> {
  const response = await fetch(`${API_BASE}/artifacts/${artifactId}/meta`, {
    headers: {
      'X-Session-ID': sessionId
    }
  });

  if (!response.ok) {
    throw new Error(`Metadata fetch failed: ${response.status}`);
  }

  return response.json();
}

function extractFilename(header: string | null): string | null {
  if (!header) return null;
  const match = header.match(/filename="(.+?)"/);
  return match ? match[1] : null;
}
```

**Why:** Encapsulates download logic with proper session header and filename handling.

---

### Phase 3: SSE Event Handling

#### 3.1 Wire artifact_stored Event
**File:** `src/lib/services/event-stream.ts` (MODIFY)

Add listener for `artifact_stored` event type:

```typescript
// In start() method, add to event types array:
['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk', 'artifact_stored'].forEach(type => {
  this.eventSource!.addEventListener(type, listener);
});

// In listener handler:
if (incomingEvent === 'artifact_stored') {
  artifactsStore.addArtifact({
    artifact_id: data.artifact_id as string,
    mime_type: data.mime_type as string,
    size_bytes: data.size_bytes as number,
    filename: data.filename as string,
    source: (data.source as Record<string, unknown>) || {},
    trace_id: data.trace_id as string,
    session_id: data.session_id as string,
    ts: data.ts as number
  });
}
```

#### 3.2 Wire in chat-stream.ts as well
**File:** `src/lib/services/chat-stream.ts` (MODIFY)

Same pattern—add `artifact_stored` to the event types handled during chat streaming.

**Why:** Artifacts may be stored during chat flow before `/events` stream starts.

---

### Phase 4: UI Components

#### 4.1 Create ArtifactItem Component
**File:** `src/lib/components/sidebar-right/artifacts/ArtifactItem.svelte` (NEW)

```svelte
<script lang="ts">
  import type { ArtifactRef } from '$lib/types/artifacts';
  import { downloadArtifact } from '$lib/services/api';
  import { sessionStore } from '$lib/stores';
  import IconButton from '$lib/components/ui/IconButton.svelte';
  import Pill from '$lib/components/ui/Pill.svelte';

  interface Props {
    artifact: ArtifactRef;
  }

  let { artifact }: Props = $props();
  let downloading = $state(false);
  let error = $state<string | null>(null);

  function formatSize(bytes: number | null): string {
    if (!bytes) return 'Unknown size';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function getMimeIcon(mime: string | null): string {
    if (!mime) return '📄';
    if (mime.startsWith('image/')) return '🖼️';
    if (mime === 'application/pdf') return '📕';
    if (mime.includes('spreadsheet') || mime.includes('excel')) return '📊';
    if (mime.includes('presentation') || mime.includes('powerpoint')) return '📽️';
    return '📄';
  }

  async function handleDownload() {
    downloading = true;
    error = null;
    try {
      await downloadArtifact(
        artifact.id,
        sessionStore.sessionId,
        artifact.filename ?? undefined
      );
    } catch (e) {
      error = e instanceof Error ? e.message : 'Download failed';
    } finally {
      downloading = false;
    }
  }
</script>

<div class="artifact-item">
  <div class="artifact-info">
    <span class="artifact-icon">{getMimeIcon(artifact.mime_type)}</span>
    <div class="artifact-details">
      <span class="artifact-name">{artifact.filename || artifact.id}</span>
      <span class="artifact-meta">
        {formatSize(artifact.size_bytes)}
        {#if artifact.mime_type}
          <Pill size="small" variant="subtle">{artifact.mime_type.split('/')[1]}</Pill>
        {/if}
      </span>
    </div>
  </div>

  <IconButton
    onclick={handleDownload}
    title={downloading ? 'Downloading...' : 'Download'}
    disabled={downloading}
  >
    {#if downloading}
      ⏳
    {:else}
      ⬇️
    {/if}
  </IconButton>

  {#if error}
    <span class="artifact-error">{error}</span>
  {/if}
</div>

<style>
  .artifact-item {
    display: flex;
    align-items: center;
    gap: var(--space-sm, 8px);
    padding: var(--space-sm, 8px);
    border-radius: var(--radius-md, 8px);
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
  }

  .artifact-info {
    display: flex;
    align-items: center;
    gap: var(--space-xs, 4px);
    flex: 1;
    min-width: 0;
  }

  .artifact-icon {
    font-size: 1.2em;
  }

  .artifact-details {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .artifact-name {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .artifact-meta {
    display: flex;
    align-items: center;
    gap: var(--space-xs, 4px);
    font-size: 0.85em;
    color: var(--color-muted, #7a756d);
  }

  .artifact-error {
    color: var(--color-error-accent, #b24c4c);
    font-size: 0.85em;
  }
</style>
```

**Why:** Reusable artifact display with download button, error handling, and loading state.

#### 4.2 Create ArtifactsCard Component
**File:** `src/lib/components/sidebar-right/artifacts/ArtifactsCard.svelte` (MODIFY or NEW)

```svelte
<script lang="ts">
  import { artifactsStore, sessionStore } from '$lib/stores';
  import { downloadArtifact } from '$lib/services/api';
  import Card from '$lib/components/layout/Card.svelte';
  import ArtifactItem from './ArtifactItem.svelte';

  let downloadingAll = $state(false);

  async function downloadAll() {
    downloadingAll = true;
    for (const artifact of artifactsStore.list) {
      try {
        await downloadArtifact(
          artifact.id,
          sessionStore.sessionId,
          artifact.filename ?? undefined
        );
        // Small delay between downloads to avoid browser blocking
        await new Promise(r => setTimeout(r, 300));
      } catch {
        // Continue with other downloads
      }
    }
    downloadingAll = false;
  }
</script>

<Card>
  <div class="artifacts-header">
    <h3>Artifacts ({artifactsStore.count})</h3>
    {#if artifactsStore.count > 1}
      <button
        class="download-all-btn"
        onclick={downloadAll}
        disabled={downloadingAll}
      >
        {downloadingAll ? 'Downloading...' : 'Download All'}
      </button>
    {/if}
  </div>

  {#if artifactsStore.count === 0}
    <p class="no-artifacts">No artifacts yet. Artifacts will appear here when tools generate downloadable files.</p>
  {:else}
    <div class="artifacts-list">
      {#each artifactsStore.list as artifact (artifact.id)}
        <ArtifactItem {artifact} />
      {/each}
    </div>
  {/if}
</Card>

<style>
  .artifacts-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-md, 12px);
  }

  .artifacts-header h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
  }

  .download-all-btn {
    padding: var(--space-xs, 4px) var(--space-sm, 8px);
    border-radius: var(--radius-md, 8px);
    background: var(--color-primary, #31a6a0);
    color: white;
    border: none;
    cursor: pointer;
    font-size: 0.85em;
  }

  .download-all-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .no-artifacts {
    color: var(--color-muted, #7a756d);
    font-style: italic;
    text-align: center;
    padding: var(--space-md, 12px);
  }

  .artifacts-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 8px);
  }
</style>
```

**Why:** Container card that shows all artifacts with batch download option.

#### 4.3 Integrate into RightSidebar
**File:** `src/lib/components/sidebar-right/RightSidebar.svelte` (MODIFY)

Add `ArtifactsCard` to the sidebar tabs or as a permanent section:

```svelte
<script lang="ts">
  import ArtifactsCard from './artifacts/ArtifactsCard.svelte';
  // ... other imports
</script>

<!-- Add after EventsCard or ConfigCard -->
<ArtifactsCard />
```

**Why:** Makes artifacts visible and accessible throughout the session.

---

### Phase 5: Clear on New Session

#### 5.1 Reset Artifacts on Session Change
**File:** `src/lib/stores/session.svelte.ts` (MODIFY)

```typescript
function resetSession() {
  sessionId = randomId();
  activeTraceId = null;
  isSending = false;

  // Clear related stores
  artifactsStore.clear();  // NEW
}
```

**Why:** Artifacts are session-scoped on backend; frontend must match.

---

## File Change Summary

| File | Action | Description |
|------|--------|-------------|
| `src/lib/types/artifacts.ts` | CREATE | ArtifactRef and event types |
| `src/lib/stores/artifacts.svelte.ts` | CREATE | Centralized artifact state |
| `src/lib/stores/index.ts` | MODIFY | Export artifactsStore |
| `src/lib/services/api.ts` | MODIFY | Add download/meta functions |
| `src/lib/services/event-stream.ts` | MODIFY | Handle artifact_stored event |
| `src/lib/services/chat-stream.ts` | MODIFY | Handle artifact_stored event |
| `src/lib/components/sidebar-right/artifacts/ArtifactItem.svelte` | CREATE | Single artifact display |
| `src/lib/components/sidebar-right/artifacts/ArtifactsCard.svelte` | CREATE/MODIFY | Artifacts list container |
| `src/lib/components/sidebar-right/RightSidebar.svelte` | MODIFY | Include ArtifactsCard |
| `src/lib/stores/session.svelte.ts` | MODIFY | Clear artifacts on reset |

---

## Testing Plan

1. **Unit Tests:**
   - `artifactsStore.addArtifact()` correctly stores refs
   - `downloadArtifact()` handles errors gracefully
   - Type exports compile correctly

2. **Integration Tests:**
   - SSE `artifact_stored` event populates store
   - Download button triggers fetch with correct session header
   - Session reset clears artifacts

3. **Manual E2E:**
   - Use reporting-agent with Tableau MCP
   - Call `get_view_as_pdf` tool
   - Verify artifact appears in sidebar
   - Click download, verify PDF opens
   - Reset session, verify artifacts cleared

---

## Future Enhancements (Out of Scope)

1. **Inline artifact hints** in Message.svelte observations
2. **Preview modal** for images/PDFs before download
3. **Zip download** for multiple artifacts
4. **Progress indicator** for large downloads
5. **Drag-and-drop** artifact upload (reverse flow)
6. **Artifact expiry warning** (TTL countdown)

---

## Implementation Order

```
1. Phase 1.1 → Types (5 min)
2. Phase 1.2 → Store (10 min)
3. Phase 2.1 → API functions (10 min)
4. Phase 3.1-3.2 → SSE wiring (10 min)
5. Phase 4.1 → ArtifactItem (15 min)
6. Phase 4.2 → ArtifactsCard (10 min)
7. Phase 4.3 → Sidebar integration (5 min)
8. Phase 5.1 → Session cleanup (5 min)
9. Testing → E2E validation (15 min)
```

**Total estimated effort:** ~1.5 hours

---

## Notes

- Backend is 100% ready—no backend changes needed
- SSE `artifact_stored` event is already emitted by planner
- Session header `X-Session-ID` is already supported by endpoints
- Existing Pill/IconButton components can be reused
- Consider adding Lucide icons package for better download icon

---

## Master Implementation Checklist

### Phase 0: UI Restructuring ✅ COMPLETE
- [x] Comment out GeneratorCard export in `sidebar-left/index.ts`
- [x] Update MobileHeader: rename `actionsContent` → `configContent`, update tab label
- [x] Update MobileBottomPanel: rename `configContent` → `artifactsContent`, update tab label
- [x] Update App.svelte desktop: remove GeneratorCard, add ConfigCard to LeftSidebar
- [x] Update App.svelte mobile: use ConfigCard in header, placeholder for ArtifactsCard in bottom
- [x] Update RightSidebar: remove ConfigCard import (placeholder for ArtifactsCard added)
- [x] Update unit tests (MobileHeader, MobileBottomPanel) for new tab names
- [x] Update E2E tests for new tab names
- [x] All 179 tests passing

### Phase 1: Type Definitions & Store ✅ COMPLETE
- [x] Create `src/lib/types/artifacts.ts` with ArtifactRef and ArtifactStoredEvent
- [x] Create `src/lib/stores/artifacts.svelte.ts` with artifactsStore
- [x] Export from `src/lib/stores/index.ts`
- [x] Export from `src/lib/types/index.ts`
- [x] Create unit tests (`tests/unit/stores/artifacts.test.ts` - 18 tests)
- [x] All 197 tests passing

### Phase 2: API Service Layer ✅ COMPLETE
- [x] Add `downloadArtifact()` function to `src/lib/services/api.ts`
- [x] Add `getArtifactMeta()` function to `src/lib/services/api.ts`
- [x] Add `extractFilename()` helper function
- [x] Add unit tests (18 new tests: 8 extractFilename, 7 downloadArtifact, 3 getArtifactMeta)
- [x] All 215 tests passing

### Phase 3: SSE Event Handling ✅ COMPLETE
- [x] Add `artifact_stored` event handling to `src/lib/services/event-stream.ts`
- [x] Add `artifact_stored` event handling to `src/lib/services/chat-stream.ts`
- [x] Import `artifactsStore` and `ArtifactStoredEvent` type in both files
- [x] Register `artifact_stored` event listener in event arrays
- [x] All 215 tests passing

### Phase 4: UI Components ✅ COMPLETE
- [x] Create `src/lib/components/sidebar-right/artifacts/ArtifactItem.svelte`
- [x] Create `src/lib/components/sidebar-right/artifacts/ArtifactsCard.svelte`
- [x] Update `src/lib/components/sidebar-right/artifacts/index.ts` exports
- [x] Update `src/lib/components/sidebar-right/RightSidebar.svelte` to include ArtifactsCard
- [x] Update App.svelte mobile to use ArtifactsCard in bottom panel
- [x] Extract helper functions to `src/lib/utils/artifact-helpers.ts`
- [x] Create unit tests for artifact helpers (15 tests)
- [x] All 230 tests passing

### Phase 5: Session Cleanup ✅ COMPLETE
- [x] Update `src/lib/stores/session.svelte.ts` to clear artifacts on reset
- [x] Update `src/lib/stores/session.svelte.ts` to clear artifacts on newSession
- [x] Add unit tests for artifact clearing in session.test.ts (2 tests)
- [x] All 266 tests passing

### Phase 6: Testing ✅ COMPLETE
- [x] Create unit tests for artifactsStore (Phase 1 - 18 tests)
- [x] Create unit tests for artifact helpers (Phase 4 - 15 tests)
- [x] Create unit tests for ArtifactItem component (17 tests)
- [x] Create unit tests for ArtifactsCard component (17 tests)
- [x] All 266 tests passing
- [ ] Manual E2E test with reporting-agent + Tableau MCP (optional)

### Phase 7: Documentation ✅ COMPLETE
- [x] Update PLAN_ARTIFACT_DOWNLOAD_UI.md with implementation status
- [x] Mark all checklist items complete

---

## File Change Summary (Complete)

| File | Action | Phase |
|------|--------|-------|
| `src/lib/components/sidebar-left/index.ts` | MODIFY | 0 |
| `src/lib/components/mobile/MobileHeader.svelte` | MODIFY | 0 |
| `src/lib/components/mobile/MobileBottomPanel.svelte` | MODIFY | 0 |
| `src/App.svelte` | MODIFY | 0, 4 |
| `src/lib/types/artifacts.ts` | CREATE | 1 |
| `src/lib/types/index.ts` | MODIFY | 1 |
| `src/lib/stores/artifacts.svelte.ts` | CREATE | 1 |
| `src/lib/stores/index.ts` | MODIFY | 1 |
| `src/lib/services/api.ts` | MODIFY | 2 |
| `src/lib/services/event-stream.ts` | MODIFY | 3 |
| `src/lib/services/chat-stream.ts` | MODIFY | 3 |
| `src/lib/components/sidebar-right/artifacts/ArtifactItem.svelte` | CREATE | 4 |
| `src/lib/components/sidebar-right/artifacts/ArtifactsCard.svelte` | CREATE | 4 |
| `src/lib/components/sidebar-right/artifacts/index.ts` | MODIFY | 4 |
| `src/lib/components/sidebar-right/RightSidebar.svelte` | MODIFY | 4 |
| `src/lib/utils/artifact-helpers.ts` | CREATE | 4 |
| `src/lib/utils/index.ts` | MODIFY | 4 |
| `tests/unit/utils/artifact-helpers.test.ts` | CREATE | 4 |
| `src/lib/stores/session.svelte.ts` | MODIFY | 5 |
| `tests/unit/stores/session.test.ts` | MODIFY | 5 |
| `tests/unit/stores/artifacts.test.ts` | CREATE | 1 |
| `tests/unit/components/sidebar-right/ArtifactItem.test.ts` | CREATE | 6 |
| `tests/unit/components/sidebar-right/ArtifactsCard.test.ts` | CREATE | 6 |

**Total: 24 files (11 create, 13 modify)**

---

## Estimated Effort

| Phase | Description | Time |
|-------|-------------|------|
| 0 | UI Restructuring | 20 min |
| 1 | Types & Store | 15 min |
| 2 | API Functions | 10 min |
| 3 | SSE Wiring | 10 min |
| 4 | UI Components | 25 min |
| 5 | Session Cleanup | 5 min |
| 6 | Testing | 30 min |
| 7 | Documentation | 10 min |
| **Total** | | **~2 hours**

---

## Implementation Complete

**All phases completed successfully!**

### Test Summary
- **266 tests passing**
- 18 tests for artifactsStore
- 15 tests for artifact helpers (formatSize, getMimeIcon, getMimeLabel)
- 17 tests for ArtifactItem component
- 17 tests for ArtifactsCard component
- 2 tests for session store artifact clearing
- All existing tests continue to pass

### Features Implemented
1. **Artifact Download UI** - ArtifactsCard in right sidebar showing downloadable files
2. **File Icons** - Visual icons based on MIME type (PDF, image, spreadsheet, presentation)
3. **Size Formatting** - Human-readable file sizes (B, KB, MB)
4. **Download Button** - Per-artifact download with loading state and error handling
5. **Download All** - Batch download for multiple artifacts
6. **Session Cleanup** - Artifacts clear when session resets or changes
7. **SSE Integration** - Real-time artifact notifications via artifact_stored events
8. **Mobile Support** - ArtifactsCard in mobile bottom panel

### Build Status
- Build successful (223 modules)
- No TypeScript errors
- All Svelte 5 patterns followed ($state, $props, $effect)
