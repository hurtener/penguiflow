<script lang="ts">
  import { onMount } from "svelte";
  import {
    initStores,
  } from "$lib/stores";
  import {
    loadMeta,
    loadSpec,
    loadComponentRegistry,
    fetchTrajectory,
    createChatStreamManager,
    createEventStreamManager,
    createSessionStreamManager,
  } from "$lib/services";
  import { Page } from "$lib/components/containers";
  import { LeftSidebar, ProjectCard, SpecCard } from "$lib/components/features/sidebar-left";
  // GeneratorCard intentionally disabled - validate/generate not active
  import { CenterColumn } from "$lib/components/features/center";
  import { TrajectoryCard } from "$lib/components/features/trajectory";
  import { RightSidebar } from "$lib/components/features/sidebar-right";
  import { EventsCard } from "$lib/components/features/sidebar-right/events";
  import { ConfigCard } from "$lib/components/features/sidebar-right/config";
  import { ArtifactsCard } from "$lib/components/features/sidebar-right/artifacts";
  import { MobileHeader, MobileBottomPanel } from "$lib/components/features/mobile";
  import { ChatCard } from "$lib/components/features/chat";
  import type { ChatMessage, PendingInteraction } from '$lib/types';

  const stores = initStores();
  const {
    sessionStore,
    chatStore,
    trajectoryStore,
    agentStore,
    specStore,
    setupStore,
    componentRegistryStore,
    interactionsStore,
  } = stores;
  const chatStreamManager = createChatStreamManager(stores);
  const eventStreamManager = createEventStreamManager(stores);
  const sessionStreamManager = createSessionStreamManager(stores);

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
    const streamingMsg = chatStore.messages.find((m: ChatMessage) => m.isStreaming);
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
      sessionStreamManager.close();
    };
  });

  $effect(() => {
    const sessionId = sessionStore.sessionId;
    if (sessionId) {
      sessionStreamManager.start(sessionId);
    }
  });

  const initializeApp = async () => {
    const [metaData, specData, componentData] = await Promise.all([
      loadMeta(),
      loadSpec(),
      loadComponentRegistry()
    ]);
    if (metaData) {
      agentStore.setFromResponse(metaData);
    }
    if (specData) {
      specStore.setFromSpecData(specData);
    }
    if (componentData) {
      componentRegistryStore.setFromPayload(componentData);
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
    trajectoryStore.clearArtifacts();
    interactionsStore.clearPendingInteraction();
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
              trajectoryStore.setFromPayload(payload);
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

  const resumeInteraction = (interaction: PendingInteraction, result: unknown) => {
    if (!setupStore.useAgui) {
      setupStore.error = 'Interactive components require AG-UI streaming.';
      return;
    }

    // Check resume_token BEFORE clearing state
    if (!interaction.resume_token) {
      setupStore.error = 'Cannot submit: session expired or interrupted. Please resend your message.';
      interactionsStore.clearPendingInteraction();
      return;
    }

    const contexts = setupStore.parseContexts();
    if (!contexts) {
      if (isMobile) {
        chatCardRef?.switchToSetup();
      } else {
        centerColumnRef?.switchToSetup();
      }
      return;
    }
    const { toolContext } = contexts;

    sessionStore.isSending = true;
    trajectoryStore.clearArtifacts();
    interactionsStore.clearPendingInteraction();

    chatStreamManager.resumeAgui(
      interaction,
      result,
      sessionStore.sessionId,
      toolContext,
      {
        onDone: async (traceId) => {
          sessionStore.activeTraceId = traceId;
          if (traceId) {
            const payload = await fetchTrajectory(traceId, sessionStore.sessionId);
            if (payload && sessionStore.activeTraceId === traceId) {
              trajectoryStore.setFromPayload(payload);
            }
            eventStreamManager.start(traceId, sessionStore.sessionId);
          }
          sessionStore.isSending = false;
        },
        onError: (error) => {
          setupStore.error = error || 'Failed to submit response. Please try again.';
          sessionStore.isSending = false;
        }
      }
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
      <ChatCard
        bind:this={chatCardRef}
        onSendChat={sendChat}
        onInteractionResult={resumeInteraction}
        bind:chatBodyEl
      />
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
      <!-- GeneratorCard disabled -->
      <ConfigCard />
    </LeftSidebar>

    <CenterColumn
      bind:this={centerColumnRef}
      onSendChat={sendChat}
      onInteractionResult={resumeInteraction}
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
