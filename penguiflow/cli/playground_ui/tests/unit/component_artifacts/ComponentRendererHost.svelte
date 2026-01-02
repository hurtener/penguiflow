<script lang="ts">
  import ComponentRenderer from '$lib/renderers/ComponentRenderer.svelte';
  import { setComponentRegistryStore, type ComponentRegistryStore } from '$lib/stores';

  export let componentRegistryStore: ComponentRegistryStore;
  export let component: string;
  export let props: Record<string, unknown> = {};
  export let onResult: ((result: unknown) => void) | undefined = undefined;

  setComponentRegistryStore(componentRegistryStore);
</script>

<ComponentRenderer {component} {props} {onResult} />
