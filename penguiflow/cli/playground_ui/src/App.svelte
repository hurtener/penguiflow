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
  import { McpAppWorkspace } from "$lib/components/features/mcp-app-workspace";
  import { TrajectoryCard } from "$lib/components/features/trajectory";
  import { RightSidebar } from "$lib/components/features/sidebar-right";
  import { EventsCard } from "$lib/components/features/sidebar-right/events";
  import { ConfigCard } from "$lib/components/features/sidebar-right/config";
  import { ArtifactsCard } from "$lib/components/features/sidebar-right/artifacts";
  import { TasksCard } from "$lib/components/features/sidebar-right/tasks";
  import { NotificationsCard } from "$lib/components/features/sidebar-right/notifications";
  import { MobileHeader, MobileBottomPanel } from "$lib/components/features/mobile";
  import { ChatCard } from "$lib/components/features/chat";
  import {
    isMcpAppArtifact,
    mergeMcpAppLlmContext,
    type ChatMessage,
    type McpAppMessageRequest,
    type PendingInteraction
  } from '$lib/types';

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
    layoutStore,
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
    layoutStore.setMobile(isMobile);
  };

  const showDesktopMcpWorkspace = $derived(
    !isMobile && layoutStore.isMcpAppOpen && !!layoutStore.activeMcpApp
  );

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

  $effect(() => {
    const latestMcpAppArtifact = interactionsStore.latestMcpAppArtifact;
    if (isMobile || !latestMcpAppArtifact || !isMcpAppArtifact(latestMcpAppArtifact)) {
      return;
    }
    if (!layoutStore.canAutoOpenMcpApp(latestMcpAppArtifact)) {
      return;
    }
    layoutStore.openMcpApp(latestMcpAppArtifact);
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

  const runChat = (
    query: string,
    options: {
      clearInput?: boolean;
      llmContextOverride?: Record<string, unknown>;
      toolContextOverride?: Record<string, unknown>;
    } = {}
  ) => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      return;
    }
    if (sessionStore.isSending) {
      throw new Error('A planner run is already in progress.');
    }

    const { clearInput = false, llmContextOverride, toolContextOverride } = options;
    setupStore.clearError();
    const contexts = setupStore.parseContexts();
    if (!contexts) {
      if (isMobile) {
        chatCardRef?.switchToSetup();
      } else {
        centerColumnRef?.switchToSetup();
      }
      throw new Error(setupStore.error ?? 'Invalid setup configuration.');
    }

    const toolContext = toolContextOverride ?? contexts.toolContext;
    const llmContext = llmContextOverride ?? contexts.llmContext;

    sessionStore.isSending = true;
    trajectoryStore.clearArtifacts();
    interactionsStore.clearPendingInteraction();
    chatStore.addUserMessage(normalizedQuery);
    if (clearInput) {
      chatStore.clearInput();
    }

    chatStreamManager.start(
      normalizedQuery,
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

  const sendChat = () => {
    const query = chatStore.input.trim();
    if (!query || sessionStore.isSending) {
      return;
    }
    try {
      runChat(query, { clearInput: true });
    } catch {
      return;
    }
  };

  const sendMcpAppMessage = async (request: McpAppMessageRequest) => {
    const query = request.text.trim();
    if (!query) {
      throw new Error('App message is empty.');
    }

    const contexts = setupStore.parseContexts();
    if (!contexts) {
      if (isMobile) {
        chatCardRef?.switchToSetup();
      } else {
        centerColumnRef?.switchToSetup();
      }
      throw new Error(setupStore.error ?? 'Invalid setup configuration.');
    }

    const llmContext = mergeMcpAppLlmContext(contexts.llmContext, request);
    runChat(query, {
      llmContextOverride: llmContext,
      toolContextOverride: contexts.toolContext,
    });
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
      {#snippet tasksContent()}
        <TasksCard />
      {/snippet}
      {#snippet notificationsContent()}
        <NotificationsCard />
      {/snippet}
    </MobileBottomPanel>
  </div>
{:else}
  <!-- Desktop Layout -->
  {#if showDesktopMcpWorkspace}
    <div class="desktop-workspace-shell">
      <div class="desktop-chat-pane">
        <CenterColumn
          bind:this={centerColumnRef}
          onSendChat={sendChat}
          onInteractionResult={resumeInteraction}
          bind:chatBodyEl
        />
      </div>

      <McpAppWorkspace
        artifact={layoutStore.activeMcpApp}
        widthPx={layoutStore.mcpAppWidthPx}
        onClose={() => layoutStore.closeMcpApp()}
        onWidthChange={(width) => layoutStore.setMcpAppWidth(width)}
        onSendMessage={sendMcpAppMessage}
      />
    </div>
  {:else}
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

  .desktop-workspace-shell {
    display: flex;
    gap: var(--page-gap, 16px);
    padding: var(--page-padding, 16px);
    height: 100vh;
    overflow: hidden;
  }

  .desktop-chat-pane {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    animation: workspace-enter var(--motion-mcp-app, 180ms ease);
  }

  @keyframes workspace-enter {
    from {
      opacity: 0;
      transform: translateY(6px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

</style>
