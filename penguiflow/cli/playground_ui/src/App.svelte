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
  import { RightSidebar } from "$lib/components/sidebar-right";

  // Reference to chat body for auto-scrolling
  let chatBodyEl = $state<HTMLDivElement | null>(null);
  let centerColumnRef: CenterColumn;

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
    initializeApp();
    return () => {
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
      centerColumnRef?.switchToSetup();
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
      }
    );
  };
</script>

<Page>
  <LeftSidebar>
    <ProjectCard />
    <SpecCard />
    <GeneratorCard />
  </LeftSidebar>

  <CenterColumn
    bind:this={centerColumnRef}
    onSendChat={sendChat}
    bind:chatBodyEl
  />

  <RightSidebar />
</Page>
