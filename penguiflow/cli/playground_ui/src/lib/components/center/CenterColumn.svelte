<script lang="ts">
  import { Column } from '$lib/components/layout';
  import { ChatCard } from './chat';
  import { TrajectoryCard } from './trajectory';

  interface Props {
    onSendChat: () => void;
    chatBodyEl?: HTMLDivElement | null;
  }

  let { onSendChat, chatBodyEl = $bindable(null) }: Props = $props();

  let chatCardRef: ChatCard;

  export const switchToSetup = () => {
    chatCardRef?.switchToSetup();
  };
</script>

<Column position="center">
  <ChatCard bind:this={chatCardRef} {onSendChat} bind:chatBodyEl />
  <TrajectoryCard />
</Column>
