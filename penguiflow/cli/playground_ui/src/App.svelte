<script lang="ts">
  import { onMount } from "svelte";
  import {
    sessionStore,
    chatStore,
    timelineStore,
    agentStore,
    specStore,
    setupStore,
  } from "$lib/stores";
  import {
    loadMeta,
    loadSpec,
    fetchTrajectory,
    chatStreamManager,
    eventStreamManager,
  } from "$lib/services";
  import { Page } from "$lib/components/layout";
  import { LeftSidebar, ProjectCard, SpecCard, GeneratorCard } from "$lib/components/sidebar-left";
  import { CenterColumn } from "$lib/components/center";
  import { TrajectoryCard } from "$lib/components/center/trajectory";
  import { RightSidebar } from "$lib/components/sidebar-right";
  import { EventsCard } from "$lib/components/sidebar-right/events";
  import { ConfigCard } from "$lib/components/sidebar-right/config";
  import { ArtifactsCard } from "$lib/components/sidebar-right/artifacts";
  import { MobileHeader, MobileBottomPanel } from "$lib/components/mobile";
  import { ChatCard } from "$lib/components/center/chat";

  // Reference to chat body for auto-scrolling
  let chatBodyEl = $state<HTMLDivElement | null>(null);
  let centerColumnRef = $state<CenterColumn | undefined>(undefined);
  let chatCardRef = $state<ChatCard | undefined>(undefined);

  // Responsive breakpoint detection
  let isMobile = $state(false);

  const checkMobile = () => {
    isMobile = window.innerWidth <= 1200;
  };

  // Auto-scroll to bottom when new messages arrive
  $effect(() => {
    const _msgCount = chatStore.messages.length;
    const streamingMsg = chatStore.messages.find((m) => m.isStreaming);
    const _streamingText = streamingMsg ? `${streamingMsg.text}${streamingMsg.observations ?? ""}` : "";

    if (chatBodyEl) {
      requestAnimationFrame(() => {
        if (chatBodyEl) {
          chatBodyEl.scrollTop = chatBodyEl.scrollHeight;
        }
      });
    }
  });

  onMount(() => {
    checkMobile();
    window.addEventListener('resize', checkMobile);
    initializeApp();
    return () => {
      window.removeEventListener('resize', checkMobile);
      chatStreamManager.close();
      eventStreamManager.close();
    };
  });

  const initializeApp = async () => {
    const [metaData, specData] = await Promise.all([loadMeta(), loadSpec()]);
    if (metaData) {
      agentStore.setFromResponse(metaData);
    }
    if (specData) {
      specStore.setFromSpecData(specData);
    }
  };

  const sendChat = () => {
    const query = chatStore.input.trim();
    if (!query || sessionStore.isSending) {
      return;
    }
    setupStore.clearError();
    const contexts = setupStore.parseContexts();
    if (!contexts) {
      if (isMobile) {
        chatCardRef?.switchToSetup();
      } else {
        centerColumnRef?.switchToSetup();
      }
      return;
    }
    const { toolContext, llmContext } = contexts;

    sessionStore.isSending = true;
    timelineStore.clearArtifacts();
    chatStore.addUserMessage(query);
    chatStore.clearInput();

    chatStreamManager.start(
      query,
      sessionStore.sessionId,
      toolContext,
      llmContext,
      {
        onDone: async (traceId) => {
          sessionStore.activeTraceId = traceId;
          if (traceId) {
            const payload = await fetchTrajectory(traceId, sessionStore.sessionId);
            if (payload && sessionStore.activeTraceId === traceId) {
              timelineStore.setFromPayload(payload);
            }
            eventStreamManager.start(traceId, sessionStore.sessionId);
          }
          sessionStore.isSending = false;
        },
        onError: () => {
          sessionStore.isSending = false;
        }
      },
      setupStore.useAgui ? 'agui' : 'sse'
    );
  };
</script>

{#if isMobile}
  <!-- Mobile Layout -->
  <div class="mobile-layout">
    <MobileHeader>
      {#snippet infoContent()}
        <ProjectCard />
      {/snippet}
      {#snippet specContent()}
        <SpecCard />
      {/snippet}
      {#snippet configContent()}
        <ConfigCard />
      {/snippet}
    </MobileHeader>

    <main class="mobile-main">
      <ChatCard bind:this={chatCardRef} onSendChat={sendChat} bind:chatBodyEl />
    </main>

    <MobileBottomPanel>
      {#snippet trajectoryContent()}
        <TrajectoryCard />
      {/snippet}
      {#snippet eventsContent()}
        <EventsCard />
      {/snippet}
      {#snippet artifactsContent()}
        <ArtifactsCard />
      {/snippet}
    </MobileBottomPanel>
  </div>
{:else}
  <!-- Desktop Layout -->
  <Page>
    <LeftSidebar>
      <ProjectCard />
      <SpecCard />
      <GeneratorCard />
      <ConfigCard />
    </LeftSidebar>

    <CenterColumn
      bind:this={centerColumnRef}
      onSendChat={sendChat}
      bind:chatBodyEl
    />

    <RightSidebar />
  </Page>
{/if}

<style>
  .mobile-layout {
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: 100dvh;
    overflow: hidden;
  }

  .mobile-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding-top: var(--mobile-header-height);
    overflow: hidden;
    min-height: 0; /* Allow flex shrinking */
  }

  .mobile-main :global(.chat-card) {
    flex: 1;
    margin: 0;
    border-radius: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 200px; /* Ensure minimum usable space */
  }

</style>
