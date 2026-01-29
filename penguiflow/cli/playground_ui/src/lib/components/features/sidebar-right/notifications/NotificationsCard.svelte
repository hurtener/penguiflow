<script lang="ts">
  import { Card } from '$lib/components/composites';
  import { applyContextPatch } from '$lib/services/api';
  import { getNotificationsStore, getSessionStore } from '$lib/stores';

  const notificationsStore = getNotificationsStore();
  const sessionStore = getSessionStore();

  function levelClass(level: string): string {
    return `level-${level}`;
  }

  async function handleAction(
    action: { id: string; label: string; payload?: Record<string, unknown> }
  ) {
    if (action.id === 'apply_context_patch') {
      const patchId = action.payload?.patch_id as string | undefined;
      if (patchId) {
        await applyContextPatch(sessionStore.sessionId, patchId, 'apply');
      }
    }
  }
</script>

<Card class="notifications-card">
  <div class="notifications-header">
    <h3 class="notifications-title">Notifications</h3>
  </div>

  <div class="notifications-body">
    {#if notificationsStore.items.length === 0}
      <p class="no-notifications">No notifications yet.</p>
    {:else}
      <div class="notifications-list">
        {#each notificationsStore.items as note (note.id)}
          <div class="notification-row {levelClass(note.level)}">
            <div class="notification-message">{note.message}</div>
            <time class="notification-time" datetime={new Date(note.ts).toISOString()}>
              {new Date(note.ts).toLocaleTimeString()}
            </time>
            {#if note.actions && note.actions.length > 0}
              <div class="notification-actions">
                {#each note.actions as action}
                  <button type="button" class="notification-btn" onclick={() => handleAction(action)}>
                    {action.label}
                  </button>
                {/each}
              </div>
            {/if}
            <button
              type="button"
              class="notification-dismiss"
              aria-label="Dismiss notification"
              onclick={() => notificationsStore.remove(note.id)}
            >
              Dismiss
            </button>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</Card>

<style>
  :global(.notifications-card) {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    max-height: 220px;
  }

  .notifications-header {
    margin-bottom: var(--space-sm, 8px);
  }

  .notifications-title {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
  }

  .notifications-body {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .no-notifications {
    margin: 0;
    padding: var(--space-md, 12px);
    text-align: center;
    color: var(--color-muted, #7a756d);
  }

  .notifications-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .notification-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 8px 10px;
    border-radius: var(--radius-md, 8px);
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-card-bg, #fffdf9);
    font-size: 12px;
  }

  .notification-actions {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }

  .notification-btn,
  .notification-dismiss {
    border: none;
    background: var(--color-tab-bg, #f0ebe4);
    color: var(--color-text, #1f1f1f);
    padding: 4px 8px;
    border-radius: var(--radius-sm, 6px);
    font-size: 10px;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .notification-btn:hover:not(:disabled),
  .notification-dismiss:hover:not(:disabled) {
    background: var(--color-border, #e8e1d7);
  }

  .notification-btn:focus-visible,
  .notification-dismiss:focus-visible {
    outline: 2px solid var(--color-primary, #31a6a0);
    outline-offset: 2px;
  }

  .notification-btn:disabled,
  .notification-dismiss:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .notification-message {
    color: var(--color-text, #1f1f1f);
  }

  .notification-time {
    color: var(--color-muted, #7a756d);
    font-size: 10px;
  }

  .level-warning {
    border-color: #f5c542;
    background: #fff6d6;
  }

  .level-error {
    border-color: #e57373;
    background: #fff0f0;
  }

  .level-success {
    border-color: #81c784;
    background: #f2fbf3;
  }
</style>
