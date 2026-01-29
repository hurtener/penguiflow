<script lang="ts">
  import { Card } from '$lib/components/composites';
  import PlannerConfigSection from './PlannerConfigSection.svelte';
  import ServicesSection from './ServicesSection.svelte';
  import ToolCatalogSection from './ToolCatalogSection.svelte';
</script>

<Card class="config-card">
  <div class="title-small">Config & Catalog</div>
  <PlannerConfigSection />
  <ServicesSection />
  <ToolCatalogSection />
</Card>

<style>
  .title-small {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    margin-bottom: 12px;
  }

  :global(.config-card) {
    flex: 0 0 auto;
    max-height: 50%;
    overflow-y: auto;
  }
</style>
