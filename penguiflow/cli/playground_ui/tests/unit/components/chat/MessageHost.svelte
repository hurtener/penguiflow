<script lang="ts">
  import Message from '$lib/components/features/chat/Message.svelte';
  import type { ChatMessage } from '$lib/types';
  import {
    setComponentRegistryStore,
    setInteractionsStore,
    setLayoutStore,
    setSessionStore,
    type ComponentRegistryStore,
    type InteractionsStore,
    type LayoutStore,
    type SessionStore,
  } from '$lib/stores';

  export let message: ChatMessage;
  export let componentRegistryStore: ComponentRegistryStore;
  export let interactionsStore: InteractionsStore;
  export let layoutStore: LayoutStore;
  export let sessionStore: SessionStore;

  setComponentRegistryStore(componentRegistryStore);
  setInteractionsStore(interactionsStore);
  setLayoutStore(layoutStore);
  setSessionStore(sessionStore);
</script>

<Message {message} />
