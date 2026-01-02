<script lang="ts">
  import { Column } from '$lib/components/containers';
  import { ChatCard } from '$lib/components/features/chat';
  import { TrajectoryCard } from '$lib/components/features/trajectory';
  import type { PendingInteraction } from '$lib/types';

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
