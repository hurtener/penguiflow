<script lang="ts">
  import { Card } from '$lib/components/layout';
  import GeneratorStepper from './GeneratorStepper.svelte';
  import { specStore } from '$lib/stores';
  import {
    validateSpec as apiValidateSpec,
    generateProject as apiGenerateProject,
  } from '$lib/services';

  const validateSpec = async () => {
    const result = await apiValidateSpec(specStore.content);
    if (result) {
      specStore.setValidationResult(result);
    }
  };

  const generateProject = async () => {
    const result = await apiGenerateProject(specStore.content);
    if (result === true) {
      specStore.markValid();
    } else if (result) {
      specStore.setGenerationErrors(result);
    }
  };
</script>

<Card class="generator-card">
  <div class="button-row">
    <button class="ghost-btn" onclick={validateSpec}>Validate</button>
    <button class="primary-btn" onclick={generateProject}>Generate</button>
  </div>
  <GeneratorStepper status={specStore.status} />
</Card>

<style>
  .button-row {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
  }

  .ghost-btn {
    flex: 1;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--color-btn-ghost-bg, #f2eee8);
    font-weight: 600;
    font-size: 13px;
    border: 1px solid var(--color-border, #e8e1d7);
    cursor: pointer;
  }

  .ghost-btn:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
  }

  .primary-btn {
    flex: 1;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--color-btn-primary-gradient, linear-gradient(135deg, #31a6a0, #1a7c75));
    color: white;
    font-weight: 600;
    font-size: 13px;
    border: none;
    cursor: pointer;
    box-shadow: 0 6px 16px rgba(49, 166, 160, 0.3);
  }

  .primary-btn:hover {
    filter: brightness(1.05);
  }
</style>
