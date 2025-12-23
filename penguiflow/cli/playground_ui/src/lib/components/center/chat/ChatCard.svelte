<script lang="ts">
  import { Card } from '$lib/components/layout';
  import { Tabs } from '$lib/components/ui';
  import ChatHeader from './ChatHeader.svelte';
  import ChatBody from './ChatBody.svelte';
  import ChatInput from './ChatInput.svelte';
  import SetupTab from '../setup/SetupTab.svelte';

  interface Props {
    onSendChat: () => void;
    chatBodyEl?: HTMLDivElement | null;
  }

  let { onSendChat, chatBodyEl = $bindable(null) }: Props = $props();

  type CenterTab = 'chat' | 'setup';
  let activeTab = $state<CenterTab>('chat');

  const tabs = [
    { id: 'chat', label: 'Chat' },
    { id: 'setup', label: 'Setup' }
  ];

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
  {:else}
    <ChatBody bind:chatBodyEl />
    <ChatInput onsubmit={onSendChat} />
  {/if}
</Card>

<style>
  :global(.chat-card) {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
  }
</style>
