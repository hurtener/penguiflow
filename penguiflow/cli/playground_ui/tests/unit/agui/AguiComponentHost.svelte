<script lang="ts">
  import { Message, MessageList, StateDebugger, ToolCall, setAGUIContext, type AGUIStore, type StreamingMessage, type StreamingToolCall } from '$lib/agui';

  export let store: AGUIStore;
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
