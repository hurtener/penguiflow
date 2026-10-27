<script lang="ts">
  import ChatCard from '$lib/components/features/chat/ChatCard.svelte';
  import { initStores } from '$lib/stores';

  initStores();

  const onSendChat = () => {};
</script>

<ChatCard {onSendChat} />
