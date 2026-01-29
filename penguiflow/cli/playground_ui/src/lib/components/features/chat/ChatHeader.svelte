<script lang="ts">
  import { Pill } from '$lib/components/primitives';
  import { getAgentStore } from '$lib/stores';

  const agentStore = getAgentStore();
</script>

<div class="chat-header">
  <Pill class="header-pill">
    <span class="dot active"></span>
    {agentStore.meta.name}
  </Pill>
  <Pill variant="ghost">DEV PLAYGROUND</Pill>
</div>

<style>
  .chat-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-success, #2ac19d);
  }

  :global(.header-pill) {
    background: var(--color-pill-subtle-bg, #eef5f3);
    color: var(--color-pill-subtle-text, #1f6c68);
  }
</style>
