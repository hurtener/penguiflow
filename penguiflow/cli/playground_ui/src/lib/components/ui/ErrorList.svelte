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
      <div class="error-row">Warning {err.message}</div>
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
