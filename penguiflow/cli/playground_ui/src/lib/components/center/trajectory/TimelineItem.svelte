<script lang="ts">
  import type { TimelineStep } from '$lib/types';
  import { Pill } from '$lib/components/ui';
  import StepDetails from './StepDetails.svelte';

  interface Props {
    step: TimelineStep;
  }

  let { step }: Props = $props();
</script>

<div class="timeline-item">
  <div class="line"></div>
  <div class="dot {step.status}"></div>
  <div class="timeline-body">
    <div class="row space-between align-center">
      <div class="step-name">{step.name}</div>
      {#if step.latencyMs}
        <Pill variant="subtle" size="small">{step.latencyMs} ms</Pill>
      {/if}
    </div>
    {#if step.thought}
      <div class="thought">"{step.thought}"</div>
    {/if}
    <StepDetails args={step.args} result={step.result} />
  </div>
</div>

<style>
  .timeline-item {
    position: relative;
    padding-left: 24px;
    padding-bottom: 12px;
  }

  .line {
    position: absolute;
    left: 6px;
    top: 12px;
    width: 2px;
    height: calc(100% - 4px);
    background: var(--color-border, #e8e1d7);
  }

  .timeline-item:last-child .line {
    display: none;
  }

  .dot {
    position: absolute;
    left: 2px;
    top: 4px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--color-primary, #31a6a0);
    border: 2px solid var(--color-card-bg, #fcfaf7);
  }

  .dot.error {
    background: var(--color-error-accent, #b24c4c);
  }

  .timeline-body {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    padding: 10px;
  }

  .row {
    display: flex;
  }

  .space-between {
    justify-content: space-between;
  }

  .align-center {
    align-items: center;
  }

  .step-name {
    font-weight: 600;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
  }

  .thought {
    font-size: 11px;
    font-style: italic;
    color: var(--color-muted, #6b665f);
    margin-top: 4px;
  }
</style>
