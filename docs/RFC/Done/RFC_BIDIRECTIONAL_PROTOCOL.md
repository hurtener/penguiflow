# RFC-003: Bidirectional Streaming & Background Task Execution

**Real-Time Agent Steering and Long-Running Task Management for Penguiflow**

| Field | Value |
|-------|-------|
| RFC Number | RFC-003 |
| Status | Implemented |
| Authors | Santi / Penguiflow Core Team |
| Created | December 2025 |
| Dependencies | RFC-001 (Core Stability - done), RFC-002 (Tool/Protocol Unification - done) |

---

## 1. Executive Summary

This RFC introduces two interconnected capabilities to Penguiflow: **Bidirectional Streaming with Real-Time Steering** and **Background Task Execution**. These capabilities transform Penguiflow from a request-response orchestration framework into a collaborative, real-time agent platform capable of continuous human-agent interaction.

The core insight driving this RFC is that most agent frameworks treat human interaction as discrete checkpoints—approve a tool call, confirm a decision, restart on failure. This creates friction and limits the sophistication of human-agent collaboration. The streaming pattern enables something fundamentally different: *steering rather than gating*.

This RFC defines primitives that enable immediate value through text-based steering while establishing the architectural foundation for future voice and audio capabilities. The design ensures that when real-time audio becomes a priority, the integration requires transport layer changes only—not architectural rebuilds.

---

## 2. Business Motivation & Strategic Context

### 2.1 Market Position

The AI agent landscape is rapidly evolving toward real-time, collaborative interfaces. OpenAI's Realtime API, Google's ADK with BIDI streaming, and emerging protocols like AG-UI all signal a market shift from batch processing to continuous interaction. Penguiflow must evolve to remain competitive and capture emerging use cases.

### 2.2 The Problem with Current Agent Paradigms

Current orchestration patterns suffer from fundamental limitations that reduce effectiveness in production environments:

1. **Opaque Execution:** Users submit requests and wait for completion with no visibility into agent reasoning or progress. When something goes wrong, the entire execution must restart.

2. **Binary Intervention:** Human checkpoints are approve/reject gates. There is no mechanism for nuanced redirection like "good direction, but also consider X" without stopping execution.

3. **Blocking Workflows:** Long-running tasks lock the conversation. Users cannot continue working while waiting for research, analysis, or generation to complete.

4. **Wasted Compute:** When an agent takes a wrong direction, all subsequent computation is wasted. Early course-correction could save significant resources and time.

### 2.3 Alignment With Penguiflow’s Existing Primitives (Important)

This RFC is not proposing a parallel universe. Penguiflow already has strong foundations that should be *extended*, not replaced:

- **Outbound streaming already exists** (Playground SSE + AG-UI adapters) through `PlannerEvent` emission during `ReactPlanner` runs. This RFC formalizes and generalizes that event stream into a task-addressable, bidirectional session protocol.
- **HITL already exists** via `pause`/`resume` semantics. This RFC makes “APPROVE/REJECT” a first-class steering vocabulary, but the *default implementation* should reuse pause/resume tokens (idempotent, audit-friendly) rather than inventing a second approval channel.
- **Cancellation already exists at the runtime level** (trace cancellation, remote task cancellation). This RFC clarifies cancellation semantics at the *task/session* layer and defines how they compose with tool cancellation.

The design goal is: add an optional “coordination layer” that can sit above the planner and tools, while keeping the core planner callable in classic request/response mode.

### 2.4 Terminology & Identifiers

To avoid ambiguity across CLI/Playground/Canvas and future transports:

- **session_id**: A UI/user session. Multi-tenant isolation boundary for task registry and event streams.
- **task_id**: A logical unit of work within a session (foreground run or background job).
- **trace_id**: A correlation ID for observability/durability; may map 1:1 to a foreground task, but background tasks may also have their own trace IDs.
- **event_id**: Monotonic or unique identifier for individual updates (used for replay and dedupe).

This RFC requires that *every outbound update and inbound steering event* carry `session_id` + `task_id` (and ideally `trace_id` when available).

---

## 3. Capability 1: Bidirectional Streaming & Real-Time Steering

### 3.1 Business Goal

> **Enable humans to observe and influence agent execution in real-time, reducing wasted computation, improving output quality, and creating a collaborative rather than transactional relationship between users and AI systems.**

### 3.2 Business Use Cases

#### Use Case 1: ReactPlanner with Live Reasoning Visibility

**Scenario:** A product manager requests competitive analysis across five companies. ReactPlanner begins executing.

**Without Streaming:** The user waits 3-5 minutes with no feedback. The agent focuses heavily on one competitor the PM considers irrelevant. Result is discarded, process restarts.

**With Streaming:** The user sees in real-time: "Planning to analyze Company A's pricing strategy, then Company B's market position..." The PM injects: "Skip Company A, they're exiting this market. Focus on Company C instead." The agent adjusts immediately, producing relevant output on first execution.

**Value:** Reduced time-to-value, eliminated wasted computation, higher output relevance, improved user trust in agent systems.

#### Use Case 2: Long-Running Pipeline Supervision

**Scenario:** An analyst initiates a multi-step data pipeline: extract data, clean it, run analysis, generate visualizations, compile report.

**Without Streaming:** Each step completes before user sees results. By the time the user realizes the wrong date range was used in extraction, four subsequent steps have wasted resources.

**With Streaming:** User watches extraction begin, sees "Processing Q3 data from sales_transactions..." and immediately corrects: "Use Q4, not Q3." Pipeline adjusts before proceeding to cleaning.

**Value:** Resource efficiency, faster iteration cycles, reduced frustration, better alignment between intent and execution.

#### Use Case 3: Graceful Degradation for Weaker Models

**Scenario:** For cost efficiency, certain pipelines use smaller LLMs. These models occasionally produce malformed JSON or misinterpret tool schemas.

**Without Streaming:** Malformed output causes step failure. Retry logic kicks in (with exponential backoff). After N failures, the entire pipeline fails and requires manual intervention.

**With Streaming:** The steering channel surfaces the partial/malformed output immediately. A supervising system (or human) can inject corrections: "The date format should be YYYY-MM-DD." The model incorporates the correction and proceeds.

**Value:** Higher success rates with cheaper models, reduced retry costs, graceful handling of edge cases, extended model viability.

#### Use Case 4: Pengui Canvas as a Live Workspace

**Scenario:** A designer uses Pengui Canvas to create an App. They request: "Build me a dashboard showing sales metrics with regional breakdown."

**Without Streaming:** The agent generates the complete dashboard. The designer wanted a pie chart but got a bar chart. They must articulate corrections and wait for regeneration.

**With Streaming:** The designer watches components materialize. As the chart begins rendering, they interject: "Make that a pie chart, not bar." The agent adjusts mid-generation. The designer sees the corrected chart appear without restart.

**Value:** Fluid creative workflow, reduced iteration loops, improved user experience, differentiated product capability.

### 3.3 Technical Capabilities Enabled

- **Reasoning Stream Export (Policy-Controlled):** ReactPlanner emits structured “thinking/rationale/progress” updates as it occurs, not just final outputs.
  - **Default must be safe**: do not require shipping private chain-of-thought. Treat “deep reasoning trace” as an opt-in diagnostic stream or a redacted “rationale” stream.
- **Steering Event Injection:** External systems can inject redirections, context, or cancellations into running executions
- **Progress Visibility:** Structured progress updates (step N of M, current tool, estimated time) for UI rendering
- **Collaborative Refinement:** Humans can add context ("also consider X") without interrupting execution flow
- **Transport Agnosticism:** Same steering semantics work over WebSocket (text) or audio channels (voice)

---

## 4. Capability 2: Background Task Execution

### 4.1 Business Goal

> **Enable long-running agent tasks to execute independently while users continue conversational interaction, maximizing productivity and enabling sophisticated multi-task workflows.**

### 4.2 Business Use Cases

#### Use Case 1: Research While Conversing

**Scenario:** A consultant asks: "Research the competitive landscape for enterprise AI platforms. While that's running, help me draft an email to my client about our meeting tomorrow."

**Without Background Tasks:** The user must wait for research to complete (potentially 5-10 minutes) before the email can be addressed. Productivity is blocked.

**With Background Tasks:** The research spawns as a background task. The foreground immediately responds: "I've started the research. Now, for the email—what's the main topic for tomorrow's meeting?" Research results surface when complete, integrated into the conversation context.

**Value:** Parallel productivity, eliminated blocking, better user experience, competitive parity with advanced AI assistants.

#### Use Case 2: Multi-Document Analysis

**Scenario:** A legal analyst uploads five contracts and requests: "Analyze each contract for liability clauses and generate a summary matrix."

**Without Background Tasks:** Sequential processing means 15-20 minutes of waiting. The analyst cannot ask clarifying questions or adjust scope mid-process.

**With Background Tasks:** Each contract analysis spawns as a separate background task. The analyst can ask "What's the status?" and get: "Contract 1: complete. Contracts 2-3: in progress. Contracts 4-5: queued." They can also steer: "Prioritize Contract 4, the client mentioned it's urgent."

**Value:** Parallel execution, priority management, progress visibility, interactive long-form workflows.

#### Use Case 3: Continuous Monitoring with Alerts

**Scenario:** A DevOps engineer configures: "Monitor our error logs and alert me if error rate exceeds 1%. Check every 5 minutes."

**Without Background Tasks:** This use case is simply not possible. Each check would block the conversation.

**With Background Tasks:** A persistent background task runs the monitoring loop. When threshold is exceeded, it injects an alert into the conversation: "Alert: Error rate at 1.3% as of 14:32. Top errors: TimeoutException (45%), NullPointer (30%). Investigate?"

**Value:** New capability class (persistent agents), proactive assistance, operational intelligence.

#### Use Case 4: Scheduled Reports (“Cron Jobs”)

**Scenario:** A user says: "Generate this analysis every Monday at 10am Mountain time."

**Without Scheduled Tasks:** The user (or an external system) must remember to re-run the workflow. There is no reliable delivery mechanism, and the agent cannot proactively contact the user.

**With Scheduled Tasks:** The system stores a job definition with a schedule + timezone + task payload. A scheduler spawns background task runs on schedule. Each run streams progress, then emits a notification + result.

**Delivery is configurable by downstream products:** deliver into the same session (if connected), into a new session/thread, into an inbox/notification center, or via external channels (email/slack/push). Penguiflow should define the primitives and the audit trail; product teams decide delivery UX.

**Value:** Persistent workflows, proactive assistance, and an auditable history of runs and deliveries.

### 4.3 Technical Capabilities Enabled

- **Task Registry:** Session maintains registry of spawned tasks with status, progress, and lifecycle management
- **Context Snapshot:** Background tasks receive snapshot of conversation context at spawn time
- **Result Injection:** Completed tasks inject results back into conversation with appropriate context
- **Priority Management:** Users can reprioritize, pause, or cancel background tasks
- **Resource Governance:** Concurrency limits, rate limiting, and resource allocation across tasks
- **Background Task Steering:** Steering events can target specific background tasks, not just foreground

### 4.4 Background Task Notifications (Frontend-First Requirement)

Background execution is only valuable if the user can *see* it. This RFC requires that background tasks:

1. **Emit status updates and progress updates** throughout execution, not only on completion.
2. **Produce user-visible notifications** when they complete, fail, or need input.
3. **Optionally “re-enter” the foreground agent** by injecting a digest/result into the main conversation state, similar to OpenAI Deep Research.

Concretely, the outbound update stream must support:

- `STATUS_CHANGE` updates: task created → queued → running → complete/failed/cancelled.
- `PROGRESS` updates: coarse progress bars or step markers suitable for UI panels.
- `NOTIFICATION` updates: human-facing alerts surfaced in the UI “inbox/toast”.
- `RESULT` updates: the final structured payload plus a compact digest.

And the session must expose two distinct “result surfaces”:

- **UI Surface** (always): push updates to the frontend so users can monitor background work without polling.
- **Agent Surface** (opt-in): inject a compact digest into the foreground’s conversation context so the agent can incorporate it on the next reasoning step.
  - If the foreground task is currently running and supports steering, completion can be delivered as a foreground `INJECT_CONTEXT` steering event to influence the *next* planner iteration (without restarting the run).

### 4.5 Context Branching, Isolation, and Merge (Required)

Background tasks must not share a single mutable context with the foreground. Without isolation, you get race conditions, confusing state, and non-reproducible trajectories.

This RFC defines a **fork/branch model** for background tasks:

#### 4.5.1 Branch Snapshot (What a Task Sees)

On spawn, a background task receives a **TaskContextSnapshot**:

- `llm_context`: a JSON-serializable snapshot of the foreground’s LLM-visible context at spawn time.
- `tool_context`: tool-only context (telemetry handles, callbacks) that may be non-serializable; only safe references should be passed.
- `memory`: either (a) a read-only short-term memory summary, or (b) a branch pointer/key if the memory layer supports branching.
- `artifacts`: references to relevant artifacts (documents, uploads) needed for the task.

The key rule: **a background task sees a stable snapshot unless explicitly steered with additional context.**

This keeps task execution deterministic and debuggable.

#### 4.5.2 Branch Execution Model (Thread Safety)

ReactPlanner is not thread-safe; therefore:

- Each background task must run with its **own planner instance** (or an explicit per-task execution engine) to allow true concurrency.
- The session/task registry owns lifecycle (start, cancel, pause) and ensures cancellation propagates to tools and remote calls.

#### 4.5.3 Merge Model (How Results Come Back)

When a task completes, it produces both:

1. **A user-visible notification payload** (for the UI surface).
2. **A merge payload** for the agent surface (optional, but required for Deep Research-style behavior).

The merge payload is a structured “context patch”, not just raw text, to avoid context bloat and to support conflict handling. Recommended shape:

- `digest`: 1–5 bullet “executive summary” (human + agent friendly).
- `facts`: structured key/value facts discovered (IDs, numbers, citations).
- `artifacts`: new artifacts created by the task (refs).
- `sources`: citations or provenance.
- `recommended_next_steps`: suggested follow-ups for the foreground agent.

Merge strategies must be explicit and deterministic:

- **Append-only**: add an entry to `llm_context.research_results[]` with `{task_id, ts, digest, facts, ...}`.
- **Keyed replace**: update a specific key (only if idempotent and safe).
- **Human-gated merge**: UI shows “Apply to conversation?” and merges only if user approves.

#### 4.5.4 Divergence & Conflict Handling

Foreground conversation may evolve while the background task runs. Therefore, merge payloads must include:

- `spawn_turn_id` or `spawn_event_id` (what context version the task branched from)
- `completed_at` timestamp
- optional `assumptions` list (what the task assumed about the context)

If the foreground has moved on significantly, the UI (or orchestrator) should surface a conflict warning: “This research was started before we clarified X; apply anyway?”

#### 4.5.5 Artifacts, Sources, and “Heavy Outputs”

Background tasks often produce large payloads (PDFs, scraped pages, datasets). To keep the conversation context small and the UI responsive:

- Prefer storing heavy outputs in the existing ArtifactStore and emitting references (`artifact_id`, `mime_type`, `filename`, `size_bytes`).
- Treat artifacts as first-class “result surfaces”:
  - UI can render previews/download links immediately.
  - Agent surface can receive compact refs + short digests rather than raw blobs.
- Sources/provenance should be attached to `ContextPatch.sources` so the foreground agent can cite and the UI can show traceability.

This keeps Deep Research results useful without exploding token budgets.

### 4.6 Resource Governance (Beyond “Concurrency Limits”)

To avoid runaway sessions:

- Per-session caps: max background tasks, max concurrent tasks, max task lifetime.
- Backpressure: bounded outbound queues; drop policy for low-priority progress spam.
- CPU/network budgets: integrate with existing retries/timeouts/circuit breakers at the tool layer.
- Cancellation semantics: cancellation is **best-effort preemptive** (interrupt awaits) when possible; otherwise **cooperative** (checked between steps).

### 4.7 Deep Research Pattern (Concrete Flow)

This is the target UX for “research in the background that comes back to the agent”:

1. User asks: “Research X. While that runs, help me write Y.”
2. Foreground agent:
   - spawns background task `task_research`
   - immediately continues the foreground conversation
3. Background task runs on a **context snapshot** and streams:
   - `STATUS_CHANGE` (running)
   - `PROGRESS` (milestones)
   - `TOOL_CALL` updates (optional)
4. On completion, the background task emits:
   - `RESULT` (structured payload + digest)
   - `NOTIFICATION` (“Research ready”)
   - optionally a `ContextPatch` suitable for “apply to conversation”
5. Foreground re-entry options:
   - **auto-append**: the patch is appended to `llm_context.research_results[]`, so the next foreground step can incorporate it.
   - **human-gated**: user clicks “Apply”, then the patch is merged and the agent acknowledges/incorporates it.

This preserves conversational flow while ensuring background work is visible, attributable, and mergeable.

---

## 5. Architectural Design

### 5.1 Design Principles

1. **Task-Addressable from Day One:** All events, channels, and state carry task identifiers, even when only one task exists. This prevents architectural regret when background tasks arrive.

2. **Transport Agnosticism:** Steering semantics are independent of transport layer. Text over WebSocket today; audio over WebRTC tomorrow. Same underlying protocol.

3. **Minimal Viable Abstraction:** Define the smallest set of primitives that enable both capabilities without over-engineering for hypothetical future needs.

4. **Graceful Degradation:** Systems without steering capability should still function normally. Steering is enhancement, not requirement.

5. **Observable by Default:** All execution emits structured events suitable for logging, debugging, and UI rendering.

### 5.2 Core Architecture

The architecture centers on a **StreamingSession** that manages bidirectional communication and task lifecycle:

```
┌─────────────────────────────────────────────────────────────┐
│                    StreamingSession                         │
├─────────────────────────────────────────────────────────────┤
│  inbound_channel    ──▶  SteeringEvent (human → agent)      │
│  outbound_channel   ◀──  StateUpdate (agent → human)        │
│  task_registry      ──▶  {task_id: TaskState, ...}          │
├─────────────────────────────────────────────────────────────┤
│  Tasks:                                                     │
│    foreground   [RUNNING]  ─── ReactPlanner execution       │
│    task_abc123  [RUNNING]  ─── Background research          │
│    task_def456  [PENDING]  ─── Queued document analysis     │
└─────────────────────────────────────────────────────────────┘
```

#### 5.2.1 Mapping to Existing Penguiflow Internals (No Duplication)

Penguiflow already emits planner execution telemetry via `PlannerEvent`. The recommended implementation is:

- **Internal**: continue emitting `PlannerEvent` (planner-native, stable, used by Playground and AG-UI mapping).
- **Session Protocol**: `StateUpdate` is a task-addressable wrapper/projection that can be generated from `PlannerEvent` plus task registry events.

This keeps the planner core clean and makes the session layer responsible for:

- event fan-out, filtering, buffering, replay
- task lifecycle
- transport (SSE/WebSocket/WebRTC)

**Suggested mapping (PlannerEvent → StateUpdate)**

This is intentionally not 1:1; the session layer can choose what to expose:

| PlannerEvent | StateUpdate.update_type | Notes |
|-------------|--------------------------|------|
| `step_start` / `step_complete` | `PROGRESS` | Render as milestone + step counters. |
| `llm_stream_chunk` (`channel=thinking`) | `THINKING` | Safe rationale stream (policy-controlled). |
| `tool_call_start` / `tool_call_end` | `TOOL_CALL` | Tool call visibility + “pre-execution steering” window. |
| `tool_call_result` | `RESULT` or `ERROR` | Split based on payload classification. |
| `artifact_stored` / `artifact_chunk` | `RESULT` | Treat artifacts as result surface; UI can link/download. |
| `pause` (HITL) | `CHECKPOINT` | Prefer token-bound approve/reject via resume. |

### 5.3 Async Coordination Pattern

Following the proven pattern from Google ADK and similar systems, the session uses Python's `asyncio.TaskGroup` to manage concurrent operations:

```python
async with asyncio.TaskGroup() as tg:
    # Receive steering events from client
    tg.create_task(receive_steering_events(), name="SteeringReceiver")
    
    # Route events to appropriate tasks
    tg.create_task(route_to_tasks(), name="EventRouter")
    
    # Collect state updates and forward to client
    tg.create_task(forward_state_updates(), name="UpdateForwarder")
```

This pattern enables natural interruption semantics—a user can inject steering while the agent is mid-execution, just as in natural conversation.

### 5.4 Event Delivery Semantics (Replay, Ordering, Backpressure)

To be production-usable, the session protocol must define delivery behavior:

- **Ordering:** Updates should be ordered per `(session_id, task_id)` stream. Global ordering across tasks is best-effort.
- **At-least-once delivery:** Transports may re-send; consumers must dedupe by `update_id`.
- **Replay:** Frontends should be able to reconnect and request replay from a cursor (`last_update_id`).
- **Backpressure:** The session layer must bound queues; low-value progress spam may be sampled/dropped, but terminal events (ERROR/RESULT/NOTIFICATION) must be delivered reliably.

### 5.5 Security, AuthZ, and Multi-Tenant Isolation

Bidirectional protocols increase the blast radius of mistakes. Minimum requirements:

- **AuthZ:** Only the session owner (or an authorized operator) can steer tasks in that session.
- **Replay safety:** `event_id` enables dedupe; servers should reject duplicate event IDs per `(session_id, task_id)`.
- **Scope limiting:** Steering events should include an explicit scope (`foreground` vs specific `task_id`); servers must validate targets.
- **Input validation:** Size limits, schema validation, and content policy checks for injected text.
- **Audit logging:** Persist steering events (what was injected, by whom, when, to which task) alongside the task trajectory.
- **Tenant isolation:** Never allow cross-tenant task IDs or artifact refs to be visible across sessions.

### 5.6 Control Plane Policy (Agent Proposes; User Confirms Destructive Actions)

Default policy for Penguiflow surfaces should be:

- The **agent may propose** task control actions (cancel/pause/prioritize/apply-to-chat).
- The **user confirms destructive actions** (at minimum: cancel; optionally: replace-style merges and high-impact reprioritization).
- Downstream products can loosen/tighten this via configuration, but the default should be safe and auditable.

Destructive actions include:

- `CANCEL` (terminates work; may discard partial progress)
- “replace” merge strategies (overwrite existing context keys)
- reprioritization that impacts shared compute budgets (product-defined)

Implementation detail: confirmation should reuse existing HITL primitives (pause/resume tokens) so approvals are replayable and consistent across transports.

### 5.7 Task Registry + Durable Event Log (StateStore-Friendly)

To support reconnection, replay, and scheduled execution, treat task state as:

- **Task registry state**: current status/priority/metadata/result pointers
- **Append-only event log**: an auditable history of what happened, when, and why

Recommended durability model:

- Persist the **event log** in `StateStore` (event-sourced).
- Derive `TaskState` by replay/projection, with a fast in-memory projection for live sessions.

Minimum task events to persist:

- `task.created`
- `task.status_changed` (queued/running/paused/complete/failed/cancelled)
- `task.progress`
- `task.notification_emitted`
- `task.result_ready` (artifact refs / result pointer)
- `task.context_patch_ready` / `task.context_patch_applied`
- `task.control_requested` (cancel/pause/resume/prioritize/apply)
- `task.control_confirmed` / `task.control_rejected` (user confirmation for destructive actions)
- `task.steering_received` (audit)

Important: `StateStore` persistence enables governance and recovery, but it does not itself “stop CPU”. Live cancellation still requires an in-memory cancellation token/task handle; the event log records intent and eventual confirmation.

---

## 6. Data Models & API Specification

### 6.1 Core Event Types

#### SteeringEvent (Inbound: Human → Agent)

```python
class SteeringEvent(BaseModel):
    session_id: str
    task_id: str                 # Target task ("foreground" or specific task ID)
    trace_id: Optional[str] = None
    event_id: str                # For dedupe/ordering (client-generated ok)
    event_type: SteeringType     # REDIRECT | INJECT_CONTEXT | CANCEL | PAUSE |
                                 # RESUME | PRIORITIZE | APPROVE | REJECT
    payload: dict                # Type-specific data
    created_at: datetime
```

**Steering Types Explained:**

| Type | Description |
|------|-------------|
| `REDIRECT` | Change execution direction. Payload includes new goal or modified constraints. |
| `INJECT_CONTEXT` | Add information without changing direction. "Also consider X." |
| `CANCEL` | Terminate task execution immediately. |
| `PAUSE` / `RESUME` | Suspend and continue execution (for background tasks). |
| `PRIORITIZE` | Move task up in queue or allocate more resources. |
| `APPROVE` / `REJECT` | Respond to agent checkpoint requests (backward compatibility with gate pattern). |

**Steering Payload Recommendations (Concrete):**

- `INJECT_CONTEXT`: `{"text": "...", "scope": "foreground|task_only", "severity": "note|correction"}`
- `REDIRECT`: `{"instruction": "...", "constraints": {...optional...}}`
- `CANCEL`: `{"reason": "...", "hard": true|false}`
- `PRIORITIZE`: `{"priority": 10}`
- `APPROVE`/`REJECT`: `{"resume_token": "...", "decision": "...optional..."}` (prefer token-binding for auditability)

**Planner consumption semantics (how INJECT_CONTEXT becomes usable)**

To “fit Penguiflow DNA” (JSON-first prompts + strong separation between user intent and tool observations), steering injections should:

- be appended as a new **user message** (not a system message)
- be wrapped/typed (e.g., `{"steering": {...}}`) so the model can distinguish it from a tool observation
- carry provenance (`event_id`, `task_id`, `created_at`) so debugging/replay remains possible
- be treated as untrusted user input (never override system/tool contracts)

This is the minimal contract that lets a running planner incorporate steering without inventing a separate prompt format.

#### StateUpdate (Outbound: Agent → Human)

```python
class StateUpdate(BaseModel):
    session_id: str
    task_id: str
    trace_id: Optional[str] = None
    update_id: str
    update_type: UpdateType      # THINKING | PROGRESS | TOOL_CALL | RESULT |
                                 # ERROR | CHECKPOINT | STATUS_CHANGE | NOTIFICATION
    content: Any                 # Type-specific content (JSON)
    step_index: Optional[int] = None
    total_steps: Optional[int] = None
    created_at: datetime
```

**Update Types Explained:**

| Type | Description |
|------|-------------|
| `THINKING` | Policy-controlled “thinking/rationale”. Must be safe by default; deep reasoning traces are opt-in. |
| `PROGRESS` | Structured progress indicator. UI can render progress bars, step indicators. |
| `TOOL_CALL` | Tool is being invoked. Includes tool name, arguments, allows pre-execution steering. |
| `RESULT` | Final or intermediate result. May be partial for streaming outputs. |
| `ERROR` | Error occurred. May be recoverable with steering input. |
| `CHECKPOINT` | Agent requesting human input. Blocks until APPROVE/REJECT steering event. |
| `STATUS_CHANGE` | Task status changed (PENDING → RUNNING → COMPLETE). |
| `NOTIFICATION` | A user-visible alert that should surface even if the user is “doing something else”. |

**Recommended `content` shapes (so frontends can build stable UIs):**

- `STATUS_CHANGE`:
  - `{"status": "PENDING|RUNNING|PAUSED|COMPLETE|FAILED|CANCELLED", "reason": "...optional...", "priority": 0}`
- `PROGRESS`:
  - `{"label": "Summarizing contract 3/5", "current": 3, "total": 5, "percent": 0.6, "eta_s": 42, "details": {...}}`
- `TOOL_CALL`:
  - `{"phase": "start|end", "tool_name": "...", "tool_call_id": "...", "args_json": "...optional...", "meta": {...}}`
- `RESULT`:
  - `{"digest": ["..."], "payload": {...small...}, "artifacts": [...refs...], "sources": [...], "task_patch": {...ContextPatch...}}`
- `NOTIFICATION`:
  - `{"severity": "info|warning|error", "title": "Research complete", "body": "Click to review and apply.", "actions": [{"id": "apply_to_chat", "label": "Apply to conversation"}]}`
- `CHECKPOINT`:
  - `{"kind": "approval_required|await_input", "resume_token": "...", "prompt": "...", "options": ["approve","reject"]}`

These shapes are intentionally JSON-first to match Penguiflow’s existing planner conventions and to be transport-agnostic.

#### ContextPatch (Task → Foreground Merge Payload)

Background tasks that should “come back to the agent” produce a structured merge payload.

```python
class ContextPatch(BaseModel):
    task_id: str
    spawned_from_event_id: str | None = None
    completed_at: datetime
    digest: list[str] = []                 # short bullets
    facts: dict[str, Any] = {}             # structured facts
    artifacts: list[dict[str, Any]] = []   # artifact refs
    sources: list[dict[str, Any]] = []     # citations/provenance
    recommended_next_steps: list[str] = []
    assumptions: list[str] = []
```

### 6.2 Task Lifecycle

```python
@dataclass
class TaskState:
    task_id: str
    status: TaskStatus         # PENDING | RUNNING | PAUSED | COMPLETE |
                               # FAILED | CANCELLED
    task_type: TaskType        # FOREGROUND | BACKGROUND
    priority: int              # Higher = more urgent
    context_snapshot: dict     # Conversation state at spawn time (TaskContextSnapshot)
    result: Optional[Any]      # Final result when complete
    error: Optional[str]       # Error message if failed
    created_at: datetime
    updated_at: datetime
```

#### State Transitions

```
PENDING ──spawn()──▶ RUNNING ──complete()──▶ COMPLETE
                         │
                         ├──pause()──▶ PAUSED ──resume()──▶ RUNNING
                         │
                         ├──cancel()──▶ CANCELLED
                         │
                         └──error()──▶ FAILED
```

### 6.3 TaskContextSnapshot (Branch Input Contract)

Background tasks should start from a well-defined snapshot to ensure determinism and good UX.

```python
class TaskContextSnapshot(BaseModel):
    session_id: str
    task_id: str
    trace_id: str | None = None
    spawned_from_task_id: str = "foreground"
    spawned_from_event_id: str | None = None
    spawned_at: datetime
    spawn_reason: str | None = None  # e.g., "deep_research", "doc_analysis", "monitoring_loop"

    # LLM-visible state (must be JSON-serializable)
    llm_context: dict[str, Any] = {}

    # Tool-only state (may include non-serializable objects; should be safe references)
    tool_context: dict[str, Any] = {}

    # Branch-aware memory model
    memory: dict[str, Any] = {}  # e.g., {"strategy": "iceberg", "branch": "...", "read_only_summary": "..."}

    # Artifact references available to the task (e.g., uploaded docs)
    artifacts: list[dict[str, Any]] = []
```

**Interpretation rules:**

- `llm_context` is *frozen* at spawn time. Foreground changes are not visible unless explicitly injected as steering.
- `tool_context` should prefer IDs/handles rather than raw objects to keep snapshots serializable for persistence.
- `memory` must support either:
  - **read-only injection** (cheap and safe): background task gets the summary only, or
  - **branch keys** (rich and consistent): background task gets its own memory branch, which is later merged.

### 6.4 Session API

```python
class StreamingSession:
    """Manages bidirectional communication and task lifecycle."""
    
    async def connect(self, transport: Transport) -> None:
        """Establish connection with transport layer (WebSocket, etc.)."""
    
    async def spawn_task(
        self,
        pipeline: Pipeline,
        task_type: TaskType = TaskType.FOREGROUND,
        priority: int = 0
    ) -> str:
        """Spawn a new task. Returns task_id."""
    
    async def steer(self, event: SteeringEvent) -> None:
        """Inject steering event into target task."""
    
    def get_task(self, task_id: str) -> Optional[TaskState]:
        """Get current state of a task."""
    
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None
    ) -> List[TaskState]:
        """List tasks, optionally filtered by status."""
    
    async def subscribe(
        self,
        task_ids: Optional[List[str]] = None,
        update_types: Optional[List[UpdateType]] = None
    ) -> AsyncIterator[StateUpdate]:
        """Subscribe to state updates with optional filters."""

    async def apply_context_patch(
        self,
        *,
        patch: ContextPatch,
        strategy: Literal["append", "replace", "human_gated"] = "append",
    ) -> None:
        """Merge a task result into the foreground context (agent surface)."""
```

---

## 7. Implementation Phases

### 7.1 Phase 1: Streaming Primitives (Foundation)

**Duration:** 2-3 weeks

**Goal:** Establish channel infrastructure with task-addressable events. Single foreground task only.

**Deliverables:**

1. StreamingSession class with inbound/outbound async channels
2. SteeringEvent and StateUpdate models with all type definitions
3. WebSocket transport adapter
4. ReactPlanner integration: emit THINKING/PROGRESS/TOOL_CALL/RESULT updates via outbound channel
5. Basic steering: CANCEL, INJECT_CONTEXT only
6. Unit tests for channel coordination

**Validation:** ReactPlanner streams safe thinking/progress to a test client. Client can cancel execution mid-step.

### 7.2 Phase 2: Canvas Integration (Validation)

**Duration:** 2 weeks (concurrent with Canvas v0.1)

**Goal:** Wire streaming to Pengui Canvas. Use real UI to validate primitives.

**Deliverables:**

1. Canvas displays reasoning stream in real-time panel
2. Text input in Canvas sends steering events
3. Progress indicators for multi-step executions
4. Error display with retry/cancel options

**Validation:** User can watch App creation in Canvas, inject corrections ("make it blue not red"), see adjustment without restart.

### 7.3 Phase 3: Full Steering Semantics

**Duration:** 2 weeks

**Goal:** Complete steering vocabulary based on Canvas learnings.

**Deliverables:**

1. REDIRECT steering with goal modification
2. CHECKPOINT semantics (agent can request approval)
3. AG-UI protocol adapter for standardized frontend communication
4. Steering event logging and replay for debugging

### 7.4 Phase 4: Background Tasks

**Duration:** 3 weeks

**Goal:** Enable concurrent background execution.

**Deliverables:**

1. Task registry with spawn/cancel/list operations
2. Context snapshot on task spawn
3. UI-first eventing: STATUS_CHANGE/PROGRESS/NOTIFICATION for background tasks
4. Result surfaces:
   - UI Surface: digest + artifacts + sources
   - Agent Surface: ContextPatch + explicit merge strategy
5. Concurrency limits and resource governance
6. PAUSE/RESUME/PRIORITIZE steering for background tasks
7. "What's the status?" support via a first-class task registry read API (and optionally a tool)

**Validation:** User spawns research task, continues conversation, receives results when complete, can check status and reprioritize.

### 7.5 Phase 5: Persistence & Reliability

**Duration:** 2 weeks

**Goal:** Tasks survive session disconnects.

**Deliverables:**

1. Durable task state (Redis or PostgreSQL backend)
2. Session reconnection with state recovery
3. Dead letter handling for failed tasks
4. Task timeout and automatic cleanup

### 7.6 Phase 6: Scheduled Jobs

**Duration:** 2–4 weeks (product-dependent)

**Goal:** Allow downstream products to schedule recurring background task runs (weekly reports, monitoring, etc.).

**Deliverables (conceptual core; downstream-owned execution):**

1. `JobDefinition` model and persistence strategy (likely via `StateStore`)
2. Scheduler contract:
   - list due jobs
   - spawn task run
   - record run outcome
3. Configurable delivery policy hooks:
   - same session (if connected)
   - new session/thread
   - inbox/notification center
   - external channels (email/slack/push)

**Validation:** Define a weekly job, observe scheduled task runs, and confirm result delivery follows the configured policy with a complete audit trail.

---

## 8. Integration Points

### 8.1 ReactPlanner Integration

ReactPlanner integration must be strictly opt-in to preserve the library’s current “call a planner and get a result” ergonomics.

Recommended integration shape:

- Keep `ReactPlanner.run(...)` usable without any session/transport.
- When a session is present, the session layer attaches:
  - an outbound event sink (today: `PlannerEvent` callback; future: `StateUpdate` projection), and
  - an optional inbound steering source for `INJECT_CONTEXT` and `CANCEL` (initially), expanding later.

This keeps the planner core small and places the complexity in the session coordinator.

```python
# Current usage (unchanged)
result = await planner.run(goal="Analyze sales data")

# Streaming usage
async with session.connect(websocket) as channels:
    result = await planner.run(
        goal="Analyze sales data",
        steering_channel=channels
    )
```

### 8.2 Iceberg Memory Integration

Background tasks create their own memory branch at spawn time. When complete, results are consolidated back to the main conversation branch. This preserves Iceberg's branch-aware memory model while enabling concurrent execution.

**Make merge explicit and observable (recommended)**

- Each background task should have its own memory identity (e.g., `MemoryKey(tenant, user, session, task_id)`).
- On completion, the task produces:
  - a **memory digest** (compact summary of what matters), and
  - optionally a **memory merge proposal** if the memory layer supports branch-aware consolidation.
- Merge policies (choose per product surface):
  - **auto-append**: add a new entry to `llm_context.research_results[]` (safe, deterministic).
  - **auto-merge**: merge into the main memory branch (powerful, higher risk).
  - **human-gated**: UI shows “Apply to conversation?” and only merges on approval.

This is the Deep Research pattern: long work happens off to the side; a compact, attributable digest returns to the main agent when ready.

### 8.3 AG-UI Protocol

StateUpdate maps cleanly to AG-UI event types. A thin adapter layer translates between Penguiflow's internal types and the AG-UI wire protocol, enabling standardized frontend communication.

**Important transport note:** SSE is outbound-only. For true bidirectional steering, Penguiflow should support either:

- a WebSocket endpoint for combined inbound/outbound, or
- SSE for outbound + HTTP POST for inbound steering events (simpler infra, still “bidirectional” at the protocol level).

### 8.4 Tool System

Tool calls emit TOOL_CALL updates before execution. This enables pre-execution steering: "Don't call that API, use the cached data instead." Tools also respect task cancellation, terminating cleanly when a CANCEL event is received.

### 8.5 Playground as Reference Implementation (Concrete)

Penguiflow’s Playground is a good “first home” for validating this RFC because it already has:

- outbound event streaming (SSE)
- pause/resume endpoints (HITL)
- AG-UI compatibility layer
- artifact endpoints

Recommended minimal bidirectional wiring:

- **Outbound:** continue using SSE for updates (`StateUpdate` or `PlannerEvent` projection).
- **Inbound:** add an HTTP endpoint to post `SteeringEvent`:
  - `POST /steer` with `{session_id, task_id, event_id, event_type, payload}`
  - the server routes the event to the in-memory channel for that task
- **Background results:** when a task completes, publish:
  - `NOTIFICATION` (“Research complete”)
  - `RESULT` (digest + refs)
  - optionally expose “Apply to conversation” as an action that triggers `apply_context_patch(...)` and emits a follow-up `NOTIFICATION`.

This pattern works even if SSE is retained (no need to switch to WebSockets immediately).

### 8.6 Steering Without a Dedicated Endpoint (Chat-First Targeting)

Downstream products may prefer not to add a separate `/steer` endpoint initially. The protocol can support a “chat-first” steering UX:

- If there is **only one active task**, steering intent inferred from the user message can be applied to that task by default.
- If there are **multiple active tasks**, the agent should ask the user which task to target using an interactive UI element that lists task IDs, statuses, and brief descriptions.

This requires two capabilities:

1. A task registry query surface (for UI and the agent): `list_tasks(...)` / `get_task(...)`.
2. When steering is opt-in, a safe way to expose task registry summaries to the LLM:
   - as a dedicated read-only tool (`tasks.list`, `tasks.get`), or
   - as a compact injected context block (bounded size, non-authoritative, refreshed per turn).

This preserves the “single task by default” mental model while scaling to N background tasks without ambiguity.

---

## 9. Future Extension: Voice & Audio

This architecture is explicitly designed to accommodate real-time voice interaction without architectural changes. When audio becomes a priority:

- **Transport Swap:** Replace WebSocket text transport with WebRTC audio transport. Channel semantics remain identical.

- **Input Processing:** Add speech-to-text pipeline before inbound channel. Steering events are generated from transcribed speech.

- **Output Processing:** Add text-to-speech pipeline after outbound channel. StateUpdates are vocalized.

- **Interruption Handling:** BIDI streaming enables natural interruption. User speaking generates implicit PAUSE; completion generates RESUME.

- **Provider Flexibility:** Abstract audio handling allows swapping between Gemini native audio, OpenAI Realtime API, or other providers.

The key insight: voice is a transport and UX concern, not an architectural one. By building transport-agnostic steering semantics now, voice becomes an incremental addition rather than a rebuild.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| **Complexity Creep:** Steering semantics could grow unbounded. | Strict typing, validation layer, semantic versioning for event types. |
| **Context Divergence:** Background task results may conflict with evolved conversation. | Timestamp-based reconciliation, conflict detection, human resolution prompts. |
| **Resource Exhaustion:** Unbounded background tasks could overwhelm system. | Per-session task limits, global rate limiting, priority queues. |
| **User Confusion:** Multiple concurrent tasks may disorient users. | Clear UI indicators, natural language status queries, sensible defaults (foreground-first). |
| **Reasoning Privacy:** Streaming “reasoning” may expose sensitive chain-of-thought. | Default to safe rationale/progress; make deep traces opt-in and redactable; document policy knobs. |
| **Context Poisoning:** Steering injection could introduce untrusted instructions. | Treat steering as untrusted user input; validate/limit size; store as structured patch; audit log every injection. |
| **Non-Determinism:** Background tasks with live mutable context are hard to debug. | Use snapshot + explicit merge patches; include spawned_from_event_id and assumptions to detect divergence. |
| **Mis-targeted Steering:** User cancels/redirects the wrong task when multiple are active. | Require explicit task selection via UI when multiple tasks are running; provide task summaries; prefer token-bound confirmations. |
| **Policy Drift:** Downstream products implement inconsistent confirmation rules. | Provide a default control policy (agent proposes; user confirms destructive actions) and require explicit overrides in product configuration. |

---

## 11. Success Metrics

### Streaming & Steering

- Reduction in full-restart rate (target: 40% decrease)
- Average time-to-first-update under 500ms
- Steering event adoption rate in Canvas sessions
- User satisfaction scores for interactive sessions vs. batch

### Background Tasks

- Concurrent task usage rate
- Average tasks per session (target: 1.5+)
- Task completion rate (target: 95%+)
- Time saved per session through parallel execution

---

## 12. Appendix: Downstream Team Reference

### 12.1 For Frontend Teams (Canvas, Apps)

**What this enables:** Real-time rendering of agent reasoning, progress indicators, inline steering controls, concurrent task status panels.

**Integration surface:** WebSocket connection to StreamingSession, subscribe to StateUpdate stream, emit SteeringEvent on user action.

**UI model recommendation (practical)**

- Foreground “chat” remains responsive; background tasks never block typing.
- A “Tasks” panel shows each `TaskState` with status, progress, and actions (cancel/prioritize/apply-to-chat).
- Notifications (NOTIFICATION updates) show as toasts/inbox entries even if the user is in the middle of a different interaction.
- Clicking a completed task reveals the digest + sources + “Apply to conversation” (human-gated merge).

**Key types to consume:** StateUpdate (for display), TaskState (for status panels), UpdateType enum (for routing to UI components).

### 12.2 For Backend Teams (Iceberg, Wayfinder, etc.)

**What this enables:** Integration as steerable tools, background execution of long operations, result injection into conversations.

**Integration surface:** Expose operations as tools that respect cancellation signals, emit progress updates, handle context snapshots.

**Key types to implement:** Handle SteeringEvent.CANCEL gracefully, emit StateUpdate.PROGRESS during long operations.

### 12.3 For Platform/Infrastructure Teams

**What this enables:** Durable task queues, session state persistence, resource governance, monitoring dashboards.

**Integration surface:** TaskRegistry persistence layer, session reconnection handling, rate limiting middleware.

**Key considerations:** Task state must survive process restarts, channels need heartbeat/reconnection logic, resource limits need enforcement points.

---

## 13. Conclusion

This RFC defines the foundation for Penguiflow's evolution from batch orchestration to real-time collaboration. By implementing bidirectional streaming with task-addressable events, we enable immediate value through text-based steering while establishing the architectural patterns required for voice interaction.

The phased approach ensures we validate primitives against real usage (Canvas) before expanding to background tasks and persistence. Each phase delivers standalone value while building toward the complete vision.

When the CTO asks for voice, we demonstrate steering-via-text in Canvas and say: "This is the same coordination layer. We just swap the transport." That's the goal.

---

## Changelog

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | December 2025 | Santi | Initial draft |
| 0.2 | January 2026 | Codex | Added frontend-first background notifications, context branching/merge contracts, event delivery/security semantics, and alignment with existing Penguiflow primitives. |
| 0.3 | January 2026 | Codex | Added control-plane default policy (agent proposes; user confirms destructive actions), StateStore-friendly task event log guidance, scheduled jobs concept + delivery policy hooks, and chat-first task targeting semantics. |
