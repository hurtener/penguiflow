<svelte:options runes={true} />
<script lang="ts">
  import { onMount } from "svelte";

  type ChatMessage = {
    id: string;
    role: "user" | "agent";
    text: string;
    isStreaming?: boolean;
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

  let plannerConfig = $state<{ label: string; value: string | number | null }[]>([]);
  let services = $state<{ name: string; status: string; url: string | null }[]>([]);
  let catalog = $state<{ name: string; desc: string; tags: string[] }[]>([]);
  let specContent = $state("");
  let specValid = $state<"pending" | "valid" | "error">("pending");
  let specErrors = $state<{ message: string; line?: number | null }[]>([]);

  const formatTime = (ts: number) =>
    new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const randomId = () => crypto.randomUUID();

  let sessionId = $state(randomId());
  let chatInput = $state("");
  let chatMessages = $state<ChatMessage[]>([]);
  let timeline = $state<TimelineStep[]>([]);
  let plannerEvents = $state<PlannerEventPayload[]>([]);
  let activeTraceId = $state<string | null>(null);
  let isSending = $state(false);
  let validationStatus = $state<"pending" | "valid" | "error">("pending");
   let eventFilter = $state<Set<string>>(new Set());
  let pauseEvents = $state(false);

  let eventSource: EventSource | null = null;

  onMount(async () => {
    await loadMeta();
    await loadSpec();
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
      specErrors = data.errors || [];
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
          ? Object.entries(data.planner).map(([label, value]) => ({ label, value }))
          : [];
      services =
        data.services?.map((svc: any) => ({
          name: svc.name,
          status: svc.enabled ? "enabled" : "disabled",
          url: svc.url || "",
        })) ?? [];
      catalog =
        data.tools?.map((tool: any) => ({
          name: tool.name,
          desc: tool.description,
          tags: tool.tags ?? [],
        })) ?? [];
    } catch (err) {
      console.error("meta load failed", err);
    }
  };

  const resetStream = () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  };

  const sendChat = () => {
    const query = chatInput.trim();
    if (!query || isSending) {
      return;
    }
    isSending = true;
    const userMessage: ChatMessage = {
      id: randomId(),
      role: "user",
      text: query,
      ts: Date.now(),
    };
    chatMessages = [...chatMessages, userMessage];

    const agentMsg: ChatMessage = {
      id: randomId(),
      role: "agent",
      text: "",
      isStreaming: true,
      ts: Date.now(),
    };
    chatMessages = [...chatMessages, agentMsg];

    const url = new URL("/chat/stream", window.location.origin);
    url.searchParams.set("query", query);
    url.searchParams.set("session_id", sessionId);

    resetStream();
    eventSource = new EventSource(url.toString());

    const handler = (eventName: string) => (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;
      if (eventName === "chunk") {
        agentMsg.text = `${agentMsg.text}${data.text ?? ""}`;
        chatMessages = [...chatMessages];
      } else if (eventName === "step" || eventName === "event") {
        plannerEvents = [data, ...plannerEvents].slice(0, 120);
      } else if (eventName === "done") {
        agentMsg.text = data.answer ?? agentMsg.text;
        agentMsg.isStreaming = false;
        activeTraceId = data.trace_id ?? activeTraceId;
        fetchTrajectory(data.trace_id, sessionId);
        startEventFollow();
        chatMessages = [...chatMessages];
        isSending = false;
        resetStream();
      } else if (eventName === "error") {
        agentMsg.text = data.error ?? "Unexpected error";
        agentMsg.isStreaming = false;
        chatMessages = [...chatMessages];
        isSending = false;
        resetStream();
      }
    };

    eventSource.addEventListener("chunk", handler("chunk"));
    eventSource.addEventListener("step", handler("step"));
    eventSource.addEventListener("event", handler("event"));
    eventSource.addEventListener("done", handler("done"));
    eventSource.addEventListener("error", handler("error"));

    eventSource.onerror = () => {
      isSending = false;
      agentMsg.isStreaming = false;
      resetStream();
      chatMessages = [...chatMessages];
    };

    chatInput = "";
  };

  const fetchTrajectory = async (traceId: string, session: string) => {
    try {
      const resp = await fetch(`/trajectory/${traceId}?session_id=${encodeURIComponent(session)}`);
      if (!resp.ok) return;
      const payload = await resp.json();
      timeline = parseTrajectory(payload);
    } catch (err) {
      console.error("trajectory fetch failed", err);
    }
  };

  const parseTrajectory = (payload: any): TimelineStep[] => {
    const steps = payload?.steps ?? [];
    return steps.map((step: any, idx: number) => {
      const action = step.action ?? {};
      return {
        id: `${idx}`,
        name: action.next_node ?? action.plan?.[0]?.node ?? "step",
        thought: action.thought,
        args: action.args,
        result: step.observation,
        latencyMs: step.latency_ms ?? null,
        reflectionScore: step?.metadata?.reflection?.score ?? null,
        status: step.error ? "error" : "ok",
      };
    });
  };

  const startEventFollow = () => {
    if (!activeTraceId) return;
    const url = new URL("/events", window.location.origin);
    url.searchParams.set("trace_id", activeTraceId);
    url.searchParams.set("session_id", sessionId);
    url.searchParams.set("follow", "true");

    const es = new EventSource(url.toString());
    const listener = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;
      if (pauseEvents) return;
      if (eventFilter.size && !eventFilter.has((data.event as string) || "")) {
        return;
      }
      plannerEvents = [data, ...plannerEvents].slice(0, 200);
    };
    es.addEventListener("event", listener);
    es.addEventListener("step", listener);
    es.addEventListener("chunk", listener);
    es.onmessage = listener;
    es.onerror = () => es.close();
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
      specErrors = data.errors || [];
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
        specErrors = await resp.json();
        return;
      }
      validationStatus = "valid";
    } catch (err) {
      console.error("generate failed", err);
    }
  };

  const safeParse = (raw: string): any | null => {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
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
        {#each agentMeta.flags as flag}
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
          {#each specErrors as err}
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
        {#each ["Validation", "Scaffold", "Tools", "Flows", "Planner", "Tests", "Config"] as step, idx}
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

      <div class="chat-body">
        {#if chatMessages.length === 0}
          <div class="empty">
            <div class="empty-icon">✶</div>
            <div class="empty-title">Ready to test agent behavior.</div>
            <div class="empty-sub">Type a message below to start a run.</div>
          </div>
        {:else}
          {#each chatMessages as msg (msg.id)}
            <div class={`message-row ${msg.role === "agent" ? "agent" : "user"}`}>
              <div class={`bubble ${msg.role}`}>
                <div>{msg.text}</div>
                {#if msg.isStreaming}
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
      {#if timeline.length === 0}
        <div class="empty inline">
          <div class="empty-title">No trajectory yet</div>
          <div class="empty-sub">Send a prompt to see steps.</div>
        </div>
      {:else}
        <div class="timeline">
          {#each timeline as step}
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
          </select>
        </div>
      </div>
      <div class="events-body">
        {#if plannerEvents.length === 0}
          <div class="empty inline">
            <div class="empty-title">No events yet</div>
            <div class="empty-sub">Events will appear during runs.</div>
          </div>
        {:else}
          {#each plannerEvents as evt, idx}
            <div class="event-row" class:alt={idx % 2 === 0}>
              <div class="pill ghost small">{evt.event ?? "event"}</div>
              <div class="event-main">
                <div class="event-name">{evt.node ?? "planner"}</div>
                <div class="muted tiny">{evt.thought ?? ""}</div>
              </div>
              {#if evt.latency_ms}
                <div class="pill subtle small">{evt.latency_ms} ms</div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>

    <div class="card config-card">
      <div class="title-small">Config & Catalog</div>
      <div class="section">
        <div class="section-label">Planner Config</div>
        <div class="tile-grid">
          {#each plannerConfig as item}
            <div class="tile">
              <div class="tile-label">{item.label}</div>
              <div class="tile-value">{item.value}</div>
            </div>
          {/each}
        </div>
      </div>
      <div class="section">
        <div class="section-label">Services</div>
        {#each services as svc}
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
        {#each catalog as tool}
          <div class="tool-row">
            <div>
              <div class="tool-name">{tool.name}</div>
              <div class="muted tiny">{tool.desc}</div>
            </div>
            <div class="tag-row">
              {#each tool.tags as tag}
                <span class="pill ghost small">{tag}</span>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div>
  </aside>
</div>
