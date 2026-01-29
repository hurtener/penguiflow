<script lang="ts">
  import MobileHeader from '$lib/components/features/mobile/MobileHeader.svelte';
  import { createAgentStore, setAgentStore } from '$lib/stores';

  export let agentName = 'Test Agent';

  const agentStore = createAgentStore();
  agentStore.setFromResponse({ agent: { name: agentName } });
  setAgentStore(agentStore);
</script>

{#snippet infoContent()}
  <div>Info</div>
{/snippet}

{#snippet specContent()}
  <div>Spec</div>
{/snippet}

{#snippet configContent()}
  <div>Config</div>
{/snippet}

<MobileHeader {infoContent} {specContent} {configContent} />
