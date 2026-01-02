<script lang="ts">
  import ComponentLab from '$lib/components/features/component-lab/ComponentLab.svelte';
  import { setComponentRegistryStore, setInteractionsStore, type ComponentRegistryStore, type InteractionsStore } from '$lib/stores';

  export let componentRegistryStore: ComponentRegistryStore;
  export let interactionsStore: InteractionsStore;

  setComponentRegistryStore(componentRegistryStore);
  setInteractionsStore(interactionsStore);
</script>

<ComponentLab />
