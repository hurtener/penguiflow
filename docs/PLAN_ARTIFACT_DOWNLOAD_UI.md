# Implementation Plan: Artifact Download UI for Playground

## Executive Summary

Add download functionality for binary artifacts (PDFs, images, Tableau exports) to the Playground UI. The backend infrastructure is **already complete**—this plan focuses on frontend changes to expose existing capabilities.

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
