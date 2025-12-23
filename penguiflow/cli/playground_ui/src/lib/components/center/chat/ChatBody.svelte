<script lang="ts">
  import { Empty } from '$lib/components/ui';
  import { chatStore } from '$lib/stores';
  import Message from './Message.svelte';

  interface Props {
    chatBodyEl?: HTMLDivElement | null;
  }

  let { chatBodyEl = $bindable(null) }: Props = $props();
</script>

<div class="chat-body" bind:this={chatBodyEl}>
  {#if chatStore.isEmpty}
    <Empty
      icon="*"
      title="Ready to test agent behavior."
      subtitle="Type a message below to start a run."
    />
  {:else}
    {#each chatStore.messages as msg (msg.id)}
      <Message message={msg} />
    {/each}
  {/if}
</div>

<style>
  .chat-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
  }
</style>
