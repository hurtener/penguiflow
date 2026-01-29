<script lang="ts">
  import { onDestroy } from 'svelte';
  import { createAGUIStore, setAGUIContext } from '$lib/agui';
  import type { Tool } from '@ag-ui/core';

  export let url: string;
  export let tools: Tool[] = [];
  export let initialState: Record<string, unknown> = {};
  export let getForwardedProps: (() => Record<string, unknown>) | undefined = undefined;
  export let onComplete: (() => void) | undefined = undefined;
  export let onError: ((e: { message: string; code?: string }) => void) | undefined = undefined;
  export let onCustomEvent: ((name: string, value: unknown) => void) | undefined = undefined;

  const store = createAGUIStore({
    url,
    tools,
    initialState,
    getForwardedProps,
    onComplete,
    onError,
    onCustomEvent
  });

  setAGUIContext(store);

  onDestroy(() => store.cancel());
</script>

<slot {store} />
