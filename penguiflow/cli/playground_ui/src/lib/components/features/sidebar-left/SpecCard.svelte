<script lang="ts">
  import { Card, ErrorList } from '$lib/components/composites';
  import { StatusDot } from '$lib/components/primitives';
  import { getSpecStore } from '$lib/stores';

  const specStore = getSpecStore();

  const statusLabel = $derived(
    specStore.status === 'valid' ? 'Valid' :
    specStore.status === 'error' ? 'Errors' : 'Pending'
  );
</script>

<Card class="spec-card">
  <div class="tabs">
    <div class="tab active">Spec YAML</div>
    <div class="tab">Validation</div>
    <StatusDot status={specStore.status} label={statusLabel} />
  </div>
  <pre class="spec-view">{specStore.content}</pre>
  <ErrorList errors={specStore.errors} />
</Card>

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
  }

  .tab.active {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }

  .spec-view {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-code-border, #eee5d9);
    border-radius: 10px;
    padding: 10px;
    font-size: 10px;
    max-height: 180px;
    overflow: auto;
    font-family: var(--font-mono);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }
</style>
