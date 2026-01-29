<script lang="ts">
  import type { ValidationStatus } from '$lib/types';

  interface Props {
    status: ValidationStatus;
  }

  let { status }: Props = $props();

  const steps = ['Validation', 'Scaffold', 'Tools', 'Flows', 'Planner', 'Tests', 'Config'];
</script>

<div class="stepper">
  {#each steps as step, idx (step)}
    <div class="step">
      <div class="step-icon">{idx + 1}</div>
      <div class="step-label">{step}</div>
      <div class="step-status {status === 'valid' && idx === 0 ? 'done' : 'pending'}"></div>
    </div>
  {/each}
</div>

<style>
  .stepper {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .step {
    flex: 0 0 calc(33% - 6px);
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 4px;
    background: var(--color-code-bg, #fbf8f3);
    border-radius: 10px;
    border: 1px solid var(--color-border, #f0ebe4);
  }

  .step-icon {
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    background: var(--color-pill-ghost-bg, #f4f0ea);
    font-size: 11px;
    font-weight: 700;
    color: var(--color-muted, #6b665f);
    margin-bottom: 4px;
  }

  .step-label {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: var(--color-muted, #6b665f);
    margin-bottom: 4px;
  }

  .step-status {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-muted-light, #c4bfb6);
  }

  .step-status.done {
    background: var(--color-success, #2ac19d);
  }
</style>
