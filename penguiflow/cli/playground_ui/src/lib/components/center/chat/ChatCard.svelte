<script lang="ts">
  import { Card } from '$lib/components/layout';
  import { Tabs } from '$lib/components/ui';
  import ChatHeader from './ChatHeader.svelte';
  import ChatBody from './ChatBody.svelte';
  import ChatInput from './ChatInput.svelte';
  import SetupTab from '../setup/SetupTab.svelte';
  import { ComponentLab } from '$lib/component_artifacts';
  import { componentRegistryStore } from '$lib/stores';
  import type { PendingInteraction } from '$lib/stores/component_artifacts.svelte';

  interface Props {
    onSendChat: () => void;
    chatBodyEl?: HTMLDivElement | null;
    onInteractionResult?: (interaction: PendingInteraction, result: unknown) => void;
  }

  let { onSendChat, chatBodyEl = $bindable(null), onInteractionResult }: Props = $props();

  type CenterTab = 'chat' | 'setup' | 'components';
  let activeTab = $state<CenterTab>('chat');

  const tabs = $derived.by(() => {
    const base: Array<{ id: string; label: string }> = [
      { id: 'chat', label: 'Chat' },
      { id: 'setup', label: 'Setup' }
    ];
    if (componentRegistryStore.enabled) {
      base.push({ id: 'components', label: 'Components' });
    }
    return base;
  });

  export const switchToSetup = () => {
    activeTab = 'setup';
  };
</script>

<Card class="chat-card">
  <ChatHeader />
  <Tabs
    {tabs}
    active={activeTab}
    onchange={(id) => { activeTab = id as CenterTab; }}
  />

  {#if activeTab === 'setup'}
    <SetupTab />
  {:else if activeTab === 'components'}
    <ComponentLab />
  {:else}
    <ChatBody bind:chatBodyEl {onInteractionResult} />
    <ChatInput onsubmit={onSendChat} />
  {/if}
</Card>

<style>
  :global(.chat-card) {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 300px;
    overflow: hidden;
  }
</style>
