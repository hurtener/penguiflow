<script lang="ts">
  import { setAGUIContext } from '$lib/agui/stores';
  import MessageList from '$lib/agui/components/MessageList.svelte';
  import Message from '$lib/agui/components/Message.svelte';
  import ToolCall from '$lib/agui/components/ToolCall.svelte';
  import StateDebugger from '$lib/agui/components/StateDebugger.svelte';
  import type { StreamingMessage, StreamingToolCall } from '$lib/agui/stores';

  export let store: any;
  export let message: StreamingMessage;
  export let toolCall: StreamingToolCall;

  setAGUIContext(store);
</script>

<div>
  <MessageList />
  <StateDebugger />
  <Message {message} />
  <ToolCall {toolCall} />
</div>
