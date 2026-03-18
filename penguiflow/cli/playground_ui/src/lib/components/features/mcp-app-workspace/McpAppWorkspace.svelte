<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { Component } from 'svelte';
  import type { ComponentArtifact, McpAppMessageRequest } from '$lib/types';

  interface Props {
    artifact: ComponentArtifact | null;
    widthPx: number;
    onClose: () => void;
    onWidthChange: (width: number) => void;
    onSendMessage?: (payload: McpAppMessageRequest) => Promise<void> | void;
  }

  let { artifact, widthPx, onClose, onWidthChange, onSendMessage = undefined }: Props = $props();

  let isResizing = $state(false);

  const AppRendererPromise = $derived.by(async () => {
    const module = await import('$lib/renderers/McpApp.svelte');
    return module.default as Component<Record<string, unknown>>;
  });

  function handleResizeStart(event: MouseEvent): void {
    event.preventDefault();
    isResizing = true;
    window.addEventListener('mousemove', handleResizeMove);
    window.addEventListener('mouseup', handleResizeEnd);
  }

  function handleResizeMove(event: MouseEvent): void {
    if (!isResizing) return;
    onWidthChange(Math.max(0, window.innerWidth - event.clientX));
  }

  function handleResizeEnd(): void {
    isResizing = false;
    window.removeEventListener('mousemove', handleResizeMove);
    window.removeEventListener('mouseup', handleResizeEnd);
  }

  onDestroy(() => {
    handleResizeEnd();
  });
</script>

<aside
  class="mcp-workspace"
  style={`width: ${widthPx}px;`}
  aria-label="MCP App Workspace"
  data-testid="mcp-app-workspace"
>
  <button
    type="button"
    class="resize-handle"
    class:active={isResizing}
    aria-label="Resize MCP app viewer"
    onmousedown={handleResizeStart}
  ></button>

  <div class="workspace-shell">
    <header class="workspace-header">
      <div class="eyebrow">Interactive App</div>

      <button type="button" class="close-button" onclick={onClose} aria-label="Close MCP app viewer">
        Close
      </button>
    </header>

    <div class="workspace-body">
      {#if artifact}
        {#await AppRendererPromise}
          <div class="workspace-loading">Loading viewer...</div>
        {:then AppRenderer}
          <AppRenderer {...artifact.props} height="100%" onSendMessage={onSendMessage} />
        {/await}
      {:else}
        <div class="workspace-empty">No MCP app selected.</div>
      {/if}
    </div>
  </div>
</aside>

<style>
  .mcp-workspace {
    position: relative;
    flex: 0 0 auto;
    min-width: var(--mcp-app-viewer-min-width, 420px);
    max-width: 70vw;
    align-self: stretch;
    height: calc(100vh - (var(--page-padding, 16px) * 2));
  }

  .workspace-shell {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: var(--space-lg);
    height: 100%;
    padding: 12px;
    margin-left: 6px;
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #f0ebe4);
    border-radius: var(--radius-lg, 12px);
    box-shadow: var(--shadow-card, 0 12px 32px rgba(17, 17, 17, 0.06));
    overflow: hidden;
  }

  .resize-handle {
    position: absolute;
    top: 16px;
    bottom: 16px;
    left: 0;
    width: 12px;
    border: none;
    background: transparent;
    cursor: col-resize;
    z-index: 2;
  }

  .resize-handle::before {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 5px;
    width: 2px;
    border-radius: var(--radius-full);
    background: var(--color-mcp-app-handle, rgba(49, 166, 160, 0.18));
    transition:
      background var(--motion-mcp-app, 180ms ease),
      transform var(--motion-mcp-app, 180ms ease);
  }

  .resize-handle:hover::before,
  .resize-handle.active::before {
    background: var(--color-mcp-app-handle-active, rgba(49, 166, 160, 0.6));
    transform: scaleX(1.5);
  }

  .workspace-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-lg);
    padding: 2px 2px 0;
  }

  .eyebrow {
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--color-muted);
    font-weight: 600;
  }

  .close-button {
    border: 1px solid var(--color-pill-ghost-border, #ebe5dd);
    background: var(--color-btn-ghost-bg, #f2eee8);
    color: var(--color-pill-ghost-text, #514c45);
    padding: 8px 14px;
    border-radius: var(--radius-full);
    font-size: 12px;
    font-weight: 600;
    transition:
      transform var(--motion-mcp-app, 180ms ease),
      background var(--motion-mcp-app, 180ms ease);
  }

  .close-button:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
    transform: translateY(-1px);
  }

  .workspace-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    border-radius: calc(var(--radius-lg, 12px) - 2px);
    background:
      linear-gradient(180deg, rgba(245, 241, 235, 0.68), rgba(252, 250, 247, 0.96)),
      var(--color-bg, #f5f1eb);
    border: 1px solid var(--color-border-light, #f1ece4);
    padding: 8px;
  }

  .workspace-body :global(.mcp-app-frame),
  .workspace-body :global(.mcp-app-loading),
  .workspace-body :global(.mcp-app-error) {
    height: 100%;
    min-height: 100%;
  }

  .workspace-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--color-muted);
    font-size: 0.95rem;
  }

  .workspace-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--color-muted);
    font-size: 0.95rem;
  }
</style>
