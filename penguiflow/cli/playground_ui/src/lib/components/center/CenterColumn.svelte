<script lang="ts">
  import { Column } from '$lib/components/layout';
  import { ChatCard } from './chat';
  import { TrajectoryCard } from './trajectory';
  import type { PendingInteraction } from '$lib/stores/component_artifacts.svelte';

  interface Props {
    onSendChat: () => void;
    chatBodyEl?: HTMLDivElement | null;
    onInteractionResult?: (interaction: PendingInteraction, result: unknown) => void;
  }

  let { onSendChat, chatBodyEl = $bindable(null), onInteractionResult }: Props = $props();

  let chatCardRef: ChatCard;

  export const switchToSetup = () => {
    chatCardRef?.switchToSetup();
  };
</script>

<Column position="center">
  <ChatCard bind:this={chatCardRef} {onSendChat} {onInteractionResult} bind:chatBodyEl />
  <TrajectoryCard />
</Column>
