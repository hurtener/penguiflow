<script lang="ts">
  import { getAGUIContext } from '../stores';
  import Message from './Message.svelte';

  const { messages } = getAGUIContext();

  let container: HTMLElement;

  $: if ($messages.length && container) {
    requestAnimationFrame(() => {
      // Re-check container inside callback to avoid null reference errors
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    });
  }
</script>

<div class="agui-message-list" bind:this={container}>
  {#each $messages as message (message.id)}
    <Message {message} />
  {/each}

  {#if $messages.length === 0}
    <slot name="empty">
      <p class="agui-empty">No messages yet</p>
    </slot>
  {/if}
</div>

<style>
  .agui-message-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    padding: 12px;
  }

  .agui-empty {
    text-align: center;
    color: var(--color-muted, #7a756d);
  }
</style>
