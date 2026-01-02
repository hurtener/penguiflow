<script lang="ts">
  import { AGUIProvider, MessageList } from '$lib/agui';

  export let url = 'http://test';
</script>

<AGUIProvider {url}>
  <MessageList />
</AGUIProvider>
