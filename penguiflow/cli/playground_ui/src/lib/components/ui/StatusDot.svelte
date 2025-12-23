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
