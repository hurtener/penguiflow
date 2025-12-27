<script lang="ts">
  import { AGUIProvider } from '$lib/agui/components';
  import MessageList from '$lib/agui/components/MessageList.svelte';

  export let url = 'http://test';
</script>

<AGUIProvider {url}>
  <MessageList />
</AGUIProvider>
