<script lang="ts">
  import { onMount } from "svelte";
  import { marked } from "marked";

  // Configure marked for safe, minimal markdown rendering
  marked.setOptions({
    breaks: true, // Convert \n to <br>
    gfm: true, // GitHub Flavored Markdown
  });

  type ChatMessage = {
    id: string;
    role: "user" | "agent";
    text: string;
    isStreaming?: boolean;
    isThinking?: boolean;
    ts: number;
    traceId?: string;
    latencyMs?: number;
  };

  type TimelineStep = {
    id: string;
    name: string;
    thought?: string;
    args?: Record<string, unknown>;
    result?: Record<string, unknown>;
    latencyMs?: number;
    reflectionScore?: number;
    status?: "ok" | "error";
    isParallel?: boolean;
  };

  type PlannerEventPayload = {
    id: string;
    event: string;
    trace_id?: string;
    session_id?: string;
    node?: string;
    latency_ms?: number;
    thought?: string;
    stream_id?: string;
    seq?: number;
    text?: string;
    done?: boolean;
    ts?: number;
    chunk?: unknown;
    artifact_type?: string;
    meta?: Record<string, unknown>;
  };

  type SpecError = {
    id: string;
    message: string;
    line?: number | null;
  };

  type ServiceInfo = {
    name: string;
    status: string;
    url: string | null;
  };

  type ToolInfo = {
    name: string;
    desc: string;
    tags: string[];
  };

  type ConfigItem = {
    label: string;
    value: string | number | boolean | null;
  };

  let agentMeta = $state({
    name: "loading_agent",
    description: "",
    template: "",
    version: "",
    flags: [] as string[],
    tools: 0,
    flows: 0,
  });

  let plannerConfig = $state<ConfigItem[]>([]);
  let services = $state<ServiceInfo[]>([]);
  let catalog = $state<ToolInfo[]>([]);
  let specContent = $state("");
  let specValid = $state<"pending" | "valid" | "error">("pending");
  let specErrors = $state<SpecError[]>([]);

  const formatTime = (ts: number) =>
    new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const randomId = () => crypto.randomUUID();

  let sessionId = $state(randomId());
  let chatInput = $state("");
  let chatMessages = $state<ChatMessage[]>([]);
  let timeline = $state<TimelineStep[]>([]);
  let plannerEvents = $state<PlannerEventPayload[]>([]);
  let artifactStreams = $state<Record<string, unknown[]>>({});
  let activeTraceId = $state<string | null>(null);
  let isSending = $state(false);
  let validationStatus = $state<"pending" | "valid" | "error">("pending");
  let eventFilter = $state<Set<string>>(new Set());
  let pauseEvents = $state(false);

  // Derived state for computed checks
  let hasNoMessages = $derived(chatMessages.length === 0);
  let hasNoTimeline = $derived(timeline.length === 0);
  let hasNoEvents = $derived(plannerEvents.length === 0);
  let hasArtifacts = $derived(Object.keys(artifactStreams).length > 0);

  let chatEventSource: EventSource | null = null;
  let followEventSource: EventSource | null = null;

  // Reference to chat body for auto-scrolling
  let chatBodyEl: HTMLDivElement | null = null;

  // Auto-scroll to bottom when new messages arrive
  $effect(() => {
    // Track chatMessages length to trigger on new messages
    const _msgCount = chatMessages.length;
    // Also track if any message is streaming (text updates)
    const _streamingText = chatMessages.find((m) => m.isStreaming)?.text;

    if (chatBodyEl) {
      // Use requestAnimationFrame to ensure DOM has updated
      requestAnimationFrame(() => {
        if (chatBodyEl) {
          chatBodyEl.scrollTop = chatBodyEl.scrollHeight;
        }
      });
    }
  });

  onMount(() => {
    loadMeta();
    loadSpec();

    // Cleanup EventSources on unmount
    return () => {
      if (chatEventSource) {
        chatEventSource.close();
        chatEventSource = null;
      }
      if (followEventSource) {
        followEventSource.close();
        followEventSource = null;
      }
    };
  });

  const loadSpec = async () => {
    try {
      const resp = await fetch("/ui/spec");
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data) return;
      specContent = data.content;
      specValid = data.valid ? "valid" : "error";
      validationStatus = specValid;
      specErrors = (data.errors || []).map((err: { message: string; line?: number | null }, idx: number) => ({
        id: `err-${idx}`,
        ...err,
      }));
    } catch (err) {
      console.error("spec load failed", err);
    }
  };

  const loadMeta = async () => {
    try {
      const resp = await fetch("/ui/meta");
      if (!resp.ok) return;
      const data = await resp.json();
      const agent = data.agent || {};
      agentMeta = {
        name: agent.name ?? "agent",
        description: agent.description ?? "",
        template: agent.template ?? "",
        version: agent.version ?? "",
        flags: agent.flags ?? [],
        tools: (data.tools || []).length,
        flows: (data.flows || []).length,
      };
      plannerConfig =
        data.planner && Object.keys(data.planner).length
          ? Object.entries(data.planner).map(([label, value]) => ({
              label,
              value: value as string | number | boolean | null,
            }))
          : [];
      services =
        data.services?.map((svc: { name: string; enabled: boolean; url?: string }) => ({
          name: svc.name,
          status: svc.enabled ? "enabled" : "disabled",
          url: svc.url || null,
        })) ?? [];
      catalog =
        data.tools?.map((tool: { name: string; description: string; tags?: string[] }) => ({
          name: tool.name,
          desc: tool.description,
          tags: tool.tags ?? [],
        })) ?? [];
    } catch (err) {
      console.error("meta load failed", err);
    }
  };

  const resetChatStream = () => {
    if (chatEventSource) {
      chatEventSource.close();
      chatEventSource = null;
    }
  };

  const resetFollowStream = () => {
    if (followEventSource) {
      followEventSource.close();
      followEventSource = null;
    }
  };

  const sendChat = () => {
    const query = chatInput.trim();
    if (!query || isSending) {
      return;
    }
    isSending = true;
    artifactStreams = {};
    const userMessage: ChatMessage = {
      id: randomId(),
      role: "user",
      text: query,
      ts: Date.now(),
    };
    chatMessages.push(userMessage);

    const agentMsgId = randomId();
    const agentMsg: ChatMessage = {
      id: agentMsgId,
      role: "agent",
      text: "",
      isStreaming: true,
      ts: Date.now(),
    };
    chatMessages.push(agentMsg);

    const url = new URL("/chat/stream", window.location.origin);
    url.searchParams.set("query", query);
    url.searchParams.set("session_id", sessionId);

    resetChatStream();
    chatEventSource = new EventSource(url.toString());

    // Find the agent message by id for updates (handles reactivity correctly)
    const findAgentMsg = () => chatMessages.find((m) => m.id === agentMsgId);

    const handler = (eventName: string) => (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;
      const msg = findAgentMsg();
      if (!msg) return;

      if (eventName === "chunk" || eventName === "llm_stream_chunk") {
        const phase = data.phase as string | undefined;
        if (phase === "action") {
          msg.isThinking = !data.done;
        } else {
          msg.text = `${msg.text}${(data.text as string) ?? ""}`;
          if (data.done) {
            msg.isStreaming = false;
          }
        }
      } else if (eventName === "artifact_chunk") {
        const streamId = (data.stream_id as string) ?? "artifact";
        const existing = artifactStreams[streamId] ?? [];
        artifactStreams[streamId] = [...existing, data.chunk];
        plannerEvents.unshift({ id: randomId(), ...data, event: "artifact_chunk" });
        if (plannerEvents.length > 120) plannerEvents.length = 120;
      } else if (eventName === "step" || eventName === "event") {
        // Only add if not already present (prevent duplicates from follow stream)
        const isDuplicate = plannerEvents.some(
          (e) => e.node === data.node && e.thought === data.thought && e.latency_ms === data.latency_ms
        );
        if (!isDuplicate) {
          plannerEvents.unshift({ id: randomId(), ...data, event: eventName });
          if (plannerEvents.length > 120) plannerEvents.length = 120;
        }
      } else if (eventName === "done") {
        if (!msg.text || msg.text.trim() === "") {
          msg.text = (data.answer as string) ?? msg.text;
        }
        msg.isStreaming = false;
        activeTraceId = (data.trace_id as string) ?? activeTraceId;
        fetchTrajectory(data.trace_id as string, sessionId);
        startEventFollow();
        isSending = false;
        resetChatStream();
      } else if (eventName === "error") {
        msg.text = (data.error as string) ?? "Unexpected error";
        msg.isStreaming = false;
        isSending = false;
        resetChatStream();
      }
    };

    chatEventSource.addEventListener("chunk", handler("chunk"));
    chatEventSource.addEventListener("artifact_chunk", handler("artifact_chunk"));
    chatEventSource.addEventListener("llm_stream_chunk", handler("llm_stream_chunk"));
    chatEventSource.addEventListener("step", handler("step"));
    chatEventSource.addEventListener("event", handler("event"));
    chatEventSource.addEventListener("done", handler("done"));
    chatEventSource.addEventListener("error", handler("error"));

    chatEventSource.onerror = () => {
      const msg = findAgentMsg();
      if (msg) {
        msg.isStreaming = false;
      }
      isSending = false;
      resetChatStream();
    };

    chatInput = "";
  };

  const fetchTrajectory = async (traceId: string, session: string) => {
    try {
      const resp = await fetch(`/trajectory/${traceId}?session_id=${encodeURIComponent(session)}`);
      if (!resp.ok) return;
      // Race condition protection: only update if this trace is still active
      if (activeTraceId !== traceId) return;
      const payload = await resp.json();
      timeline = parseTrajectory(payload);
    } catch (err) {
      console.error("trajectory fetch failed", err);
    }
  };

  interface TrajectoryStep {
    action?: {
      next_node?: string;
      plan?: { node: string }[];
      thought?: string;
      args?: Record<string, unknown>;
    };
    observation?: Record<string, unknown>;
    latency_ms?: number;
    metadata?: {
      reflection?: { score?: number };
    };
    error?: boolean;
  }

  const parseTrajectory = (payload: { steps?: TrajectoryStep[] }): TimelineStep[] => {
    const steps = payload?.steps ?? [];
    return steps.map((step, idx) => {
      const action = step.action ?? {};
      return {
        id: `step-${idx}`,
        name: action.next_node ?? action.plan?.[0]?.node ?? "step",
        thought: action.thought,
        args: action.args,
        result: step.observation,
        latencyMs: step.latency_ms ?? undefined,
        reflectionScore: step.metadata?.reflection?.score ?? undefined,
        status: step.error ? "error" : "ok",
      };
    });
  };

  const startEventFollow = () => {
    if (!activeTraceId) return;

    // Close any existing follow stream before starting a new one
    resetFollowStream();

    const url = new URL("/events", window.location.origin);
    url.searchParams.set("trace_id", activeTraceId);
    url.searchParams.set("session_id", sessionId);
    url.searchParams.set("follow", "true");

    followEventSource = new EventSource(url.toString());
    const listener = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;
      if (pauseEvents) return;
      const incomingEvent = (evt.type as string) || (data.event as string) || "";
      if (eventFilter.size && !eventFilter.has(incomingEvent)) {
        return;
      }
      if (incomingEvent === "artifact_chunk") {
        const streamId = (data.stream_id as string) ?? "artifact";
        const existing = artifactStreams[streamId] ?? [];
        artifactStreams[streamId] = [...existing, data.chunk];
      }
      if (incomingEvent === "llm_stream_chunk") {
        // Do not flood plannerEvents; streaming is rendered in the chat bubble
        return;
      }
      // Only add if not already present (prevent duplicates)
      const isDuplicate = plannerEvents.some(
        (e) => e.node === data.node && e.thought === data.thought && e.latency_ms === data.latency_ms
      );
      if (!isDuplicate) {
        plannerEvents.unshift({ id: randomId(), ...data, event: incomingEvent || (data.event as string) || "event" });
        if (plannerEvents.length > 200) plannerEvents.length = 200;
      }
    };
    followEventSource.addEventListener("event", listener);
    followEventSource.addEventListener("step", listener);
    followEventSource.addEventListener("chunk", listener);
    followEventSource.addEventListener("llm_stream_chunk", listener);
    followEventSource.addEventListener("artifact_chunk", listener);
    followEventSource.onmessage = listener;
    followEventSource.onerror = () => resetFollowStream();
  };

  const validateSpec = async () => {
    try {
      const resp = await fetch("/ui/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec_text: specContent }),
      });
      const data = await resp.json();
      specValid = data.valid ? "valid" : "error";
      validationStatus = specValid;
      specErrors = (data.errors || []).map((err: { message: string; line?: number | null }, idx: number) => ({
        id: `val-err-${idx}`,
        ...err,
      }));
    } catch (err) {
      console.error("validate failed", err);
    }
  };

  const generateProject = async () => {
    try {
      const resp = await fetch("/ui/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec_text: specContent }),
      });
      if (!resp.ok) {
        specValid = "error";
        validationStatus = "error";
        const errors = await resp.json();
        specErrors = (errors || []).map((err: { message: string; line?: number | null }, idx: number) => ({
          id: `gen-err-${idx}`,
          ...err,
        }));
        return;
      }
      validationStatus = "valid";
    } catch (err) {
      console.error("generate failed", err);
    }
  };

  const safeParse = (raw: string): Record<string, unknown> | null => {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return null;
    }
  };

  // Render markdown to HTML (synchronous for streaming compatibility)
  const renderMarkdown = (text: string): string => {
    if (!text) return "";
    try {
      return marked.parse(text, { async: false }) as string;
    } catch {
      return text;
    }
  };
</script>

<div class="page">
  <aside class="column left">
    <div class="card project-card">
      <div class="row space-between align-center">
        <div>
          <div class="title">{agentMeta.name}</div>
          <div class="muted">{agentMeta.description}</div>
        </div>
        <div class="pill subtle">{agentMeta.version || agentMeta.template}</div>
      </div>
      <div class="badges">
        {#each agentMeta.flags as flag, idx (idx)}
          <span class="pill ghost">{flag}</span>
        {/each}
      </div>
      <div class="stats-row">
        <div class="stat">
          <div class="stat-label">TOOLS</div>
          <div class="stat-value">{agentMeta.tools}</div>
        </div>
        <div class="stat">
          <div class="stat-label">FLOWS</div>
          <div class="stat-value">{agentMeta.flows}</div>
        </div>
        <div class="stat">
          <div class="stat-label">SVCS</div>
          <div class="stat-value">{services.length}</div>
        </div>
      </div>
    </div>

    <div class="card spec-card">
      <div class="tabs">
        <div class="tab active">Spec YAML</div>
        <div class="tab">Validation</div>
        <div class="status-dot {validationStatus}">
          {#if validationStatus === "valid"}
            Valid
          {:else if validationStatus === "error"}
            Errors
          {:else}
            Pending
          {/if}
        </div>
      </div>
      <pre class="spec-view">{specContent}</pre>
      {#if specErrors.length}
        <div class="errors">
          {#each specErrors as err (err.id)}
            <div class="error-row">⚠️ {err.message}</div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="card generator-card">
      <div class="button-row">
        <button class="ghost-btn" onclick={validateSpec}>Validate</button>
        <button class="primary-btn" onclick={generateProject}>Generate</button>
      </div>
      <div class="stepper">
        {#each ["Validation", "Scaffold", "Tools", "Flows", "Planner", "Tests", "Config"] as step, idx (step)}
          <div class="step">
            <div class="step-icon">{idx + 1}</div>
            <div class="step-label">{step}</div>
            <div class="step-status {validationStatus === "valid" && idx === 0 ? "done" : "pending"}"></div>
          </div>
        {/each}
      </div>
    </div>
  </aside>

  <main class="column center">
    <div class="card chat-card">
      <div class="chat-header">
        <div class="pill header-pill">
          <span class="dot active"></span>
          {agentMeta.name}
        </div>
        <div class="pill ghost">DEV PLAYGROUND</div>
      </div>

      <div class="chat-body" bind:this={chatBodyEl}>
        {#if hasNoMessages}
          <div class="empty">
            <div class="empty-icon">✶</div>
            <div class="empty-title">Ready to test agent behavior.</div>
            <div class="empty-sub">Type a message below to start a run.</div>
          </div>
        {:else}
          {#each chatMessages as msg (msg.id)}
            <div class={`message-row ${msg.role === "agent" ? "agent" : "user"}`}>
              <div class={`bubble ${msg.role}`}>
                <div class="markdown-content">{@html renderMarkdown(msg.text)}</div>
                {#if msg.isStreaming || msg.isThinking}
                  <div class="typing">
                    <span></span><span></span><span></span>
                  </div>
                {/if}
              </div>
              <div class="meta-row">
                <span>{formatTime(msg.ts)}</span>
                {#if msg.traceId}
                  <span class="link">#{msg.traceId}</span>
                {/if}
              </div>
            </div>
          {/each}
        {/if}
      </div>

      <div class="chat-input">
        <textarea
          placeholder="Ask your agent something..."
          bind:value={chatInput}
          onkeydown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendChat();
            }
          }}
        ></textarea>
        <button class="send-btn" onclick={sendChat} disabled={isSending || !chatInput.trim()}>
          ➤
        </button>
      </div>
    </div>

    <div class="card trajectory-card">
      <div class="trajectory-header">
        <div class="title-small">Execution Trajectory</div>
        {#if activeTraceId}
          <div class="pill subtle">trace {activeTraceId.slice(0, 8)}</div>
        {/if}
      </div>
      {#if hasNoTimeline}
        <div class="empty inline">
          <div class="empty-title">No trajectory yet</div>
          <div class="empty-sub">Send a prompt to see steps.</div>
        </div>
      {:else}
        {#key activeTraceId}
        <div class="timeline">
          {#each timeline as step (step.id)}
            <div class="timeline-item">
              <div class="line"></div>
              <div class="dot {step.status}"></div>
              <div class="timeline-body">
                <div class="row space-between align-center">
                  <div class="step-name">{step.name}</div>
                  {#if step.latencyMs}<div class="pill subtle">{step.latencyMs} ms</div>{/if}
                </div>
                {#if step.thought}<div class="thought">“{step.thought}”</div>{/if}
                <details>
                  <summary>Details</summary>
                  {#if step.args}
                    <div class="code-block">
                      <div class="label">args</div>
                      <pre>{JSON.stringify(step.args, null, 2)}</pre>
                    </div>
                  {/if}
                  {#if step.result}
                    <div class="code-block">
                      <div class="label">result</div>
                      <pre>{JSON.stringify(step.result, null, 2)}</pre>
                    </div>
                  {/if}
                </details>
              </div>
            </div>
          {/each}
        </div>
        {/key}
      {/if}
    </div>
  </main>

  <aside class="column right">
    <div class="card events-card">
      <div class="row space-between align-center">
        <div class="title-small">Planner Events</div>
        <div class="icon-row">
          <button
            class="icon"
            onclick={() => {
              pauseEvents = !pauseEvents;
            }}
          >
            {pauseEvents ? "▶" : "⏸"}
          </button>
          <select
            class="filter"
            onchange={(e) => {
              const value = e.currentTarget.value;
              if (!value) {
                eventFilter = new Set();
              } else {
                eventFilter = new Set([value]);
              }
            }}
          >
            <option value="">All</option>
            <option value="step">step</option>
            <option value="event">event</option>
            <option value="chunk">chunk</option>
            <option value="artifact_chunk">artifact_chunk</option>
          </select>
        </div>
      </div>
      <div class="events-body">
        {#if hasNoEvents}
          <div class="empty inline">
            <div class="empty-title">No events yet</div>
            <div class="empty-sub">Events will appear during runs.</div>
          </div>
        {:else}
          {#each plannerEvents as evt, idx (evt.id)}
            <div class="event-row" class:alt={idx % 2 === 0}>
              <div class="pill ghost small">{evt.event ?? "event"}</div>
              <div class="event-main">
                <div class="event-name">{evt.node ?? "planner"}</div>
                <div class="muted tiny">{evt.thought ?? ""}</div>
              </div>
              {#if evt.latency_ms}
                <div class="pill subtle small">{(evt.latency_ms / 1000).toFixed(2)}s</div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
      {#if hasArtifacts}
        <div class="section">
          <div class="title-small">Artifact Streams</div>
          {#each Object.entries(artifactStreams) as [streamId, chunks] (streamId)}
            <div class="artifact-row">
              <div class="pill ghost small">{streamId}</div>
              <pre>{JSON.stringify(chunks[chunks.length - 1], null, 2)}</pre>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="card config-card">
      <div class="title-small">Config & Catalog</div>
      <div class="section">
        <div class="section-label">Planner Config</div>
        <div class="tile-grid">
          {#each plannerConfig as item (item.label)}
            <div class="tile">
              <div class="tile-label">{item.label}</div>
              <div class="tile-value">{item.value}</div>
            </div>
          {/each}
        </div>
      </div>
      <div class="section">
        <div class="section-label">Services</div>
        {#each services as svc (svc.name)}
          <div class="service-row">
            <div>
              <div class="service-name">{svc.name}</div>
              <div class="muted tiny">{svc.url || "not configured"}</div>
            </div>
            <div class={`pill ${svc.status === "enabled" ? "subtle" : "ghost"} small`}>{svc.status}</div>
          </div>
        {/each}
      </div>
      <div class="section">
        <div class="section-label">Tool Catalog</div>
        {#each catalog as tool (tool.name)}
          <div class="tool-row">
            <div>
              <div class="tool-name">{tool.name}</div>
              <div class="muted tiny">{tool.desc}</div>
            </div>
            <div class="tag-row">
              {#each tool.tags as tag, tagIdx (`${tool.name}-${tagIdx}`)}
                <span class="pill ghost small">{tag}</span>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div>
  </aside>
</div>
