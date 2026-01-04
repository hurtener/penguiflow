# RFC: Agent Background Tasks & Subagent Orchestration

> **Status**: Draft
> **Related**: RFC_BIDIRECTIONAL_PROTOCOL.md
> **Version**: v2.10

## Overview

This document defines how agents spawn, manage, and interact with background tasks. Background tasks in Penguiflow are essentially **subagents**—independent planner instances with their own context, trajectory, and steering inbox.

Two complementary mechanisms enable background task creation:

1. **Tool-level declaration**: Tools marked as inherently long-running automatically execute in background
2. **Agent-level decision**: The agent dynamically spawns background work via meta-tools

---

## Design Principles

### Background Tasks Are Subagents

Every background task receives:
- Its own ReactPlanner instance
- A frozen context snapshot from the moment of spawning
- An independent steering inbox for control
- A separate trajectory and memory

This means spawning a background task is semantically equivalent to spawning a virtual subagent with a specific goal.

### Two Task Execution Modes

Background work can run in two modes. This keeps the system flexible for downstream teams:

1. **Planner-backed subagent** (default for agent-level spawning)
   - Spins up a ReactPlanner loop
   - Can call tools, reason, and iteratively refine
   - Best for research, planning, multi-step tasks

2. **Tool-backed job** (default for tool-level background)
   - Executes a single tool call asynchronously
   - No planner loop, no additional reasoning steps
   - Best for long-running but deterministic tools (file processing, indexing, batch requests)

The mode is explicit in spawn configuration. This avoids accidental cost inflation and keeps tool-level background behavior predictable.

### Two Decision Points

| Decision Point | Who Decides | When | Use Case |
|----------------|-------------|------|----------|
| Tool-level | Tool author | Development time | Inherently slow operations (deep research, large file processing) |
| Agent-level | ReactPlanner/LLM | Runtime | Dynamic parallelization based on user request complexity |

Both mechanisms coexist. A tool can be marked as always-background, AND the agent can spawn ad-hoc background tasks for any goal.

---

## Mechanism 1: Tool-Level Background Declaration

### Concept

Some tools are inherently long-running and should never block the foreground conversation. The tool author declares this at development time through tool metadata.

### Behavior

When the planner executes a tool marked as background:
1. The runtime detects the background flag in tool metadata
2. Instead of awaiting the tool inline, it spawns a background task
3. The foreground receives an immediate acknowledgment with the task ID
4. The tool executes asynchronously (typically as a tool-backed job; subagent if explicitly configured)

### Agent Awareness

The tool's description must communicate this behavior to the LLM:
> "This tool runs in background. Results will be available asynchronously. Use task management to check status."

This ensures the agent plans accordingly—it knows the tool won't return results immediately.

### Tool Metadata Contract (Proposed)

Tool-level background behavior should be declared explicitly in tool metadata. Suggested fields:

```json
{
  "background": {
    "enabled": true,
    "mode": "job",
    "default_merge_strategy": "HUMAN_GATED",
    "notify_on_complete": true
  }
}
```

- The metadata lives alongside the tool schema (e.g., ToolNode config or internal tool registry) and is surfaced through the planner's tool catalog.
- `mode`: `"job"` (single tool call) or `"subagent"` (full planner loop)
- `default_merge_strategy`: how results should be merged if not overridden by caller
- `notify_on_complete`: whether to emit UI notification on completion

This keeps tool behavior explicit and predictable across downstream products.

If `mode="subagent"`, the tool invocation becomes the first action for that subagent (i.e., the subagent is seeded with the tool call intent and can perform additional reasoning steps before or after execution).

---

## Mechanism 2: Agent-Level Dynamic Spawning

### Concept

The agent receives meta-tools that allow it to make runtime decisions about parallelization. When facing a complex request with independent subtasks, the agent can choose to delegate work to background subagents.

### Meta-Tools

The agent has access to task management capabilities through dedicated tools:

**Spawning**
- Create a new background task with a specific goal/query
- The spawned task receives a context snapshot and operates independently
- Returns immediately with a task identifier

**Querying**
- List all tasks (active, completed, failed) spawned by the current session
- Get detailed status of a specific task including progress and results
- Check if a task has completed and retrieve its output

**Lifecycle Management**
- Request cancellation of a running task
- Adjust priority of pending/running tasks

### Meta-Tool Surface (Proposed)

To make this concrete and consistent across integrations, define a minimal tool surface exposed in `tool_context`:

```json
{
  "tasks.spawn": {
    "query": "string",
    "task_type": "background|foreground",
    "mode": "subagent|job",
    "priority": 0,
    "merge_strategy": "APPEND|REPLACE|HUMAN_GATED",
    "context_depth": "full|summary|none",
    "propagate_on_cancel": "cascade|isolate"
  }
}
```

```json
{
  "tasks.list": {
    "status": "PENDING|RUNNING|PAUSED|COMPLETE|FAILED|CANCELLED|any"
  }
}
```

```json
{
  "tasks.get": {
    "task_id": "string"
  }
}
```

```json
{
  "tasks.cancel": {
    "task_id": "string",
    "reason": "string"
  }
}
```

```json
{
  "tasks.prioritize": {
    "task_id": "string",
    "priority": 0
  }
}
```

```json
{
  "tasks.apply_patch": {
    "patch_id": "string",
    "action": "apply|reject"
  }
}
```

These tools should be exposed as regular ToolContext calls so downstream teams can implement them via API, database, or orchestration layer.

### Subagent Tooling Gate

Subagents must not receive task-management capabilities. To prevent runaway spawning:
- `tasks.*` meta-tools are only exposed to the **foreground** agent
- Subagents receive a reduced ToolContext without task registry access
- Subagents cannot spawn additional subagents, list tasks, or inspect sibling status

This keeps responsibility centralized and avoids recursive delegation loops.

### Decision Flow

When the agent receives a multi-part request:
1. Agent analyzes the request and identifies independent subtasks
2. Agent decides which subtasks benefit from parallel execution
3. Agent spawns background tasks for suitable subtasks
4. Agent continues foreground work on remaining items
5. Agent periodically checks or waits for background results
6. Agent synthesizes final response from all outputs

### Optional Human Confirmation

The agent may ask the user before spawning background work, especially for:
- Resource-intensive operations
- Tasks with potential side effects
- Operations that might take significant time
- Operations that might overflown its own context windows

This leverages the existing HITL pause mechanism.

---

## The Connection Layer

### Session as Orchestrator

The StreamingSession maintains the task registry—a live view of all foreground and background tasks. The main agent accesses this registry through its tool context.

### Visibility Model

```
Session
├── Task Registry
│   ├── Foreground Task (main agent)
│   │   └── Can query all sibling tasks
│   ├── Background Task A (subagent)
│   │   └── Isolated context, own steering
│   └── Background Task B (subagent)
│       └── Isolated context, own steering
└── Update Broker
    └── Broadcasts all task state changes
```

The main agent sees:
- All tasks it spawned (directly or via background tools)
- Status, progress, and result digest of each task (raw outputs opt-in)
- Ability to send steering commands to any task

Background tasks see:
- Only their own context (snapshot from spawn time)
- Their parent task ID (for result routing)
- No direct visibility into sibling tasks

### Task Registry Exposure Policy

Not all task data should be visible to the LLM by default. Suggested exposure levels:

**Default LLM-visible fields**
- `task_id`, `status`, `task_type`, `priority`
- `spawn_reason` and short `description`
- `progress` (structured, low-risk)
- `result_digest` (short, human-written summary)

**Restricted / opt-in fields**
- Raw tool outputs and full result payloads
- Error traces, stack traces, or raw logs
- Tool arguments that include secrets or PII

Downstream teams can override this policy, but Penguiflow should ship with a safe default.

### User Interaction

When the user asks about background work:
1. The question goes to the foreground agent
2. Agent queries task registry via meta-tools
3. Agent synthesizes status information for the user
4. User can steer tasks through the chat (agent translates to steering events)

---

## Context Isolation

### Snapshot at Spawn Time

When a background task is created, it receives a **frozen snapshot** of:
- LLM context (read-only knowledge)
- Tool context (session IDs, configuration)
- Relevant conversation history (configurable depth)

### No Shared Mutation

Background tasks cannot modify the foreground context. They operate in isolation. Results are explicitly merged back through:
- The merge strategy defined at spawn time (append, replace, human-gated)
- Context patches that require approval before application

### Isolation Policy (Proposed)

To make isolation explicit and safe:
- Snapshot uses deep copy for all nested structures
- Memory namespace is derived from `{session_id}:{task_id}` (no shared writes)
- Optional memory seeding is allowed, but write-back is opt-in
- Background tasks cannot mutate shared artifacts directly; they emit their own artifacts

### Context Depth Options

When spawning tasks, allow callers to specify how much conversation context is captured:
- `full`: entire conversation context snapshot
- `summary`: short summary + recent turns
- `none`: only explicit query + tool context

### Divergence Handling

If a background task's context becomes stale (foreground advanced significantly), the system can:
- Flag the divergence to the user
- Require human approval before merging results
- Allow the agent to re-run with fresh context

---

## Result Integration

### Automatic Notification

When a background task completes:
1. A notification appears in the UI
2. The foreground agent receives an update (if subscribed)
3. Results are held pending based on merge strategy

### Merge Strategies

| Strategy | Behavior |
|----------|----------|
| APPEND | Results automatically added to context |
| REPLACE | Results overwrite specific context keys |
| HUMAN_GATED | Results require user approval before merge |

### Agent-Driven Synthesis

For complex workflows, the foreground agent may:
1. Wait for multiple background tasks to complete
2. Query results from each
3. Synthesize a unified response
4. Present to the user with attribution to each subtask

---

## Resource Governance

### Limits

The session enforces limits on background task creation:
- Maximum concurrent background tasks
- Maximum total tasks per session
- Timeout for individual tasks

### Priority Queue

Background tasks execute based on priority. The agent or user can adjust priority to:
- Expedite important research
- Defer less urgent work
- Cancel obsolete tasks

### Scheduling Policy (Proposed)

Define a simple, predictable policy:
- Higher priority runs first
- FIFO within the same priority
- Optional fairness window to prevent starvation

---

## Steering Integration

Steering is the bidirectional control mechanism that allows users and agents to influence running tasks. With background tasks, steering becomes a multi-target system.

### Steering Architecture

Every task—foreground or background—has its own SteeringInbox. The session routes steering events to the appropriate inbox based on task ID.

```
User / UI
    │
    │  "Cancel the research task"
    │  "Pause everything"
    │  "Prioritize task B"
    ▼
Steering Router (Session)
    │
    │  Routes by task_id or broadcasts session-wide
    │
    ├─────────────────┬─────────────────┐
    ▼                 ▼                 ▼
Foreground        Background        Background
Task Inbox        Task A Inbox      Task B Inbox
```

### Two Steering Paths

**Path 1: Direct (UI/API)**

The user interacts directly with task controls in the interface. Button clicks translate to steering events sent via the API endpoint. The event routes to the target task's inbox, and the task reacts accordingly.

This path is immediate and explicit—the user sees the task, clicks a control, the task responds.

**Path 2: Agent-Mediated (Chat)**

The user expresses intent through natural language in the chat. The foreground agent interprets the request, identifies the target task, and emits the appropriate steering event.

This path is conversational—the agent translates human intent into system commands.

Example flow:
1. User: "Actually, stop that research and focus on the competitor analysis"
2. Foreground agent parses intent: cancel task X, prioritize task Y
3. Agent emits CANCEL to task X's inbox
4. Agent emits PRIORITIZE to task Y's inbox
5. Agent confirms to user: "Stopped the research. Prioritizing competitor analysis."

### Steering Event Types

| Event | Applies To | Behavior |
|-------|------------|----------|
| CANCEL | All tasks | Terminates execution, marks as cancelled |
| PAUSE | All tasks | Suspends execution until RESUME |
| RESUME | All tasks | Continues a paused task |
| PRIORITIZE | Background only | Adjusts position in execution queue |
| INJECT_CONTEXT | All tasks | Adds information to task's context mid-run |
| REDIRECT | All tasks | Changes the task's goal/query mid-execution |
| APPROVE | All tasks | Confirms a HITL checkpoint, allows continuation |
| REJECT | All tasks | Denies a HITL checkpoint, may terminate or retry |

### Session-Wide vs Task-Specific

Some steering commands target a specific task, others affect the entire session:

**Task-specific**: CANCEL, PAUSE, RESUME, PRIORITIZE, INJECT_CONTEXT, REDIRECT
- Require a task_id
- Only affect the targeted task
- Other tasks continue unaffected

**Session-wide**: Emergency stop, resource limits exceeded
- Broadcast to all active tasks
- Coordinated shutdown or pause
- Used for critical situations

### The Agent as Steering Proxy

The foreground agent can act as a steering proxy for background tasks. This enables natural language control over the entire task ecosystem.

Capabilities:
- Translate user intent into steering events
- Target specific tasks by understanding their purpose
- Coordinate multiple steering actions atomically
- Provide feedback on steering outcomes

The agent uses meta-tools or direct session access to emit steering events. It maintains awareness of all tasks through the task registry.

### Steering and HITL Checkpoints

Background tasks can pause for human approval just like foreground tasks. When a background task reaches a checkpoint:

1. Task emits a THINKING or CHECKPOINT state update
2. Notification appears in the UI
3. Task pauses, waiting on its steering inbox
4. User reviews and clicks Approve/Reject (or speaks to agent)
5. APPROVE or REJECT event routes to task's inbox
6. Task continues or terminates based on response

This enables human oversight of autonomous background work.

### Steering and Context Patches

When a background task completes with HUMAN_GATED merge strategy:

1. Task produces results
2. Results packaged as a ContextPatch
3. Session stores patch, emits notification
4. UI shows: "Research complete. Apply results?"
5. User decision triggers steering:
   - Approve → Patch applied to foreground context
   - Reject → Patch discarded, optional retry
6. Foreground agent can now access the merged results

This pattern ensures humans remain in control of what information flows back into the main conversation.

### Steering Propagation to Subagents

When a parent task is cancelled or paused, its spawned children may need coordinated handling:

**Cascade option**: Steering event propagates to all child tasks
- Parent CANCEL → All children cancelled
- Parent PAUSE → All children paused

**Isolate option**: Children continue independently
- Parent CANCEL → Children orphaned (continue or timeout)
- Useful when child work has independent value

The spawn configuration determines propagation behavior.

**Default**: `cascade` for CANCEL, `isolate` for PAUSE/RESUME. This prevents accidental runaway tasks while avoiding unnecessary global stalls.

---

## Integration with Existing Patterns

### Follows Penguiflow DNA

This design uses established patterns:
- **Protocol-based**: Meta-tools implement the standard ToolContext protocol
- **Context objects**: Session and task state passed through tool_context
- **Event emission**: Task spawns and completions emit observable events
- **Proxy pattern**: Task registry access through session facade

### Complements RFC-003

This RFC builds on the bidirectional streaming infrastructure:
- Steering events control background tasks
- State updates broadcast task progress
- The UI reflects task status in real-time

---

## Summary

Background tasks in Penguiflow are subagents—independent planner instances that execute work asynchronously. They can be triggered automatically (tool-level declaration) or dynamically (agent-level meta-tools).

The main agent maintains visibility and control through:
- Meta-tools for spawning and querying tasks
- The session's task registry
- Steering events for lifecycle control

This enables sophisticated multi-agent workflows while maintaining the simplicity of the single-agent programming model.

---

## Non-Goals

- Implementing a fully autonomous multi-agent hierarchy with unlimited recursive spawning
- Auto-injecting full artifact payloads into the LLM context
- Mandating a specific persistence or scheduling backend for downstream teams
- Forcing background task usage in products that only need foreground execution

---

## Implementation Contract (Proposed)

This section defines the minimum behavior required for compliant implementations, plus optional knobs and prompt additions.

### Required Behaviors

- **Artifact handling**: Background tasks emit artifact metadata in `StateUpdate.RESULT` and in `ContextPatch`. Raw artifact payloads are not injected into LLM context by default.
- **Result integration**: Results are merged only via the configured merge strategy (APPEND/REPLACE/HUMAN_GATED). HUMAN_GATED must block until user action.
- **Context isolation**: Background tasks run on a frozen snapshot with deep-copy semantics, isolated memory namespace, and no shared mutable state.
- **Steering routing**: Each task has its own steering inbox; session routes events by task ID with audit logging.
- **Subagent gating**: Subagents do not receive task-management meta-tools or task registry visibility.

### Platform Implementation Details (Required)

These must ship in Penguiflow as part of the core library/platform.

#### Task + Steering Model

- Implement `TaskState`, `TaskContextSnapshot`, `ContextPatch`, and `SteeringEvent` as stable models.
- Enforce steering payload validation per event type.
- Persist steering events for audit; include `session_id`, `task_id`, `event_id`, `trace_id`, and `created_at`.
- Ensure steering inputs are wrapped (e.g., `{"steering": {...}}`) before injection into planner prompts.

#### Background Task APIs

- `tasks.spawn` creates a background task and returns `{task_id, session_id, status}`.
- `tasks.list` returns tasks with filters by status and optional pagination/limit.
- `tasks.get` returns status + digest + metadata for a single task.
- `tasks.cancel` transitions task to CANCELLED and signals its steering inbox.
- `tasks.prioritize` updates priority and triggers scheduling reorder.
- `tasks.apply_patch` applies or rejects a pending context patch.

These must be available via the Session API and the ToolContext surface.

#### Context Isolation

- Snapshot uses deep copy of all nested structures.
- Background tasks use isolated memory namespace (`{session_id}:{task_id}` by default).
- No shared mutable state between foreground and background tasks.
- Background artifacts are stored separately from foreground artifacts.

#### Merge Strategies + Divergence

- Support `APPEND`, `REPLACE`, `HUMAN_GATED`.
- Track `context_version` and `context_hash` in snapshot.
- If `context_version` or `context_hash` differs at merge time, set `context_diverged=True` and notify.

#### Steering Routing

- Per-task `SteeringInbox` with dedupe of `event_id`.
- Route to inbox by `task_id`; support session-wide broadcast for emergency stop.
- Audit log each steering event and its acceptance/rejection.

#### Artifact Contract

- Results must include artifact **metadata** only (name, type, size, summary, id).
- Raw bytes must never be injected into LLM context by default.
- Full payload retrieval requires explicit tool call (e.g., `artifacts.get`).

#### Subagent Gating

- Subagents must not receive `tasks.*` meta-tools.
- Subagents must not access task registry summaries.
- Foreground agent acts as sole orchestrator for task management.

#### Safe Defaults

- Conservative session limits (max tasks, max background tasks, timeout).
- Background tasks default to `HUMAN_GATED` merge strategy unless overridden.

#### Prompt Policy Hooks

- Prompt additions are injected only when background tasks are enabled.
- No prompt changes for users who do not opt in.

### Optional Configuration Knobs (ReactPlanner)

These are opt-in and only affect behavior when background tasks are enabled.

- `enable_background_tasks: bool`
- `allow_tool_background: bool`
- `background_default_mode: "subagent|job"`
- `background_default_merge_strategy: "APPEND|REPLACE|HUMAN_GATED"`
- `background_context_depth: "full|summary|none"`
- `background_result_digest_max_chars: int`
- `background_artifact_visibility: "metadata|full"`
- `background_artifact_autofetch: bool`
- `background_propagate_on_cancel: "cascade|isolate"`
- `background_propagate_on_pause: "cascade|isolate"`
- `background_memory_namespace: "isolated|shared"`
- `background_spawn_requires_confirmation: bool`
- `expose_task_registry: "none|summary|full"` (foreground only)
- `background_result_digest_schema: "summary|facts|custom"`
- `background_result_ttl_seconds: int`
- `background_retry_policy: "none|simple|custom"`
- `background_spawn_idempotency: "client|server|disabled"`
- `background_requires_confirmation_at_cost: bool`
- `background_cost_threshold: int`

### Prompt Additions (Gated)

Only include these prompt fragments when background tasks are enabled:

- **Async tools**: “Some tools run in background and return a task ID; results arrive later.”
- **Meta-tools**: “You can call `tasks.spawn`, `tasks.list`, `tasks.get`, `tasks.cancel`, `tasks.prioritize`, `tasks.apply_patch`.”
- **Artifacts**: “Background tasks may produce artifacts. You see metadata only; fetch full content explicitly if needed.”
- **Merge rules**: “Background results may require human approval before merging into context.”
- **Steering proxy**: “If the user requests task control, identify the task and emit the corresponding steering event.”
- **Divergence warning**: “If results are from an older context, warn and prefer HUMAN_GATED merge.”

### Platform Interfaces + Default Stubs (Required)

These must ship in the library as interfaces + in-memory defaults. Production implementations are left to downstream teams.

#### Persistence Adapter

- `SessionStateStore` interface for tasks, steering events, and updates.
- In-memory default implementation for local use.
- Adapter for existing StateStore where available.

#### Scheduler Interface

- A minimal job runner interface for scheduled background work.
- In-memory runner or noop runner as default.
- Hooks for downstream schedulers to plug in (cron, queues, etc.).

#### Cost/Consent Hook

- Optional estimation hook before spawn.
- If estimated cost exceeds threshold, trigger a confirmation checkpoint.
- Default: off unless configured.

#### Observability Schema

- Emit telemetry with:
  - `session_id`, `task_id`, `parent_task_id`, `trace_id`
  - `mode` (subagent|job), `spawn_reason`, `duration_ms`, `outcome`
- Provide a minimal event schema to keep downstream tooling consistent.

#### Idempotency Support

- Accept client-provided `task_id` or idempotency key.
- Best-effort dedupe: if task already exists, return existing task status.
- Default behavior: allow duplicates unless idempotency key is provided.

### Optional Policies (Recommended)

These are not required, but clarify expected behavior for downstream teams.

**Lifecycle + Retention**
- Completed tasks can be retained for a configurable TTL (default: 24h)
- On session close, remaining tasks are cancelled or orphaned based on policy
- Task updates beyond TTL are pruned from replay streams

**Error Semantics + Retries**
- Retry policy is opt-in and scoped to background tasks
- Recommended default: no retries unless explicitly configured
- Errors should emit `StateUpdate.ERROR` with safe summaries only

**Ownership/ACLs**
- User: allowed to steer/cancel tasks in their session
- Agent: allowed to propose actions; destructive actions require confirmation
- System/admin: can enforce shutdown or limits

**Idempotency + Dedupe**
- Support idempotency keys for spawning to avoid duplicates
- Client-supplied `task_id` is preferred when available

**Observability**
- Emit telemetry with `session_id`, `task_id`, `parent_task_id`, `trace_id`, `mode`, `spawn_reason`, `duration_ms`, `outcome`

**Cost/Consent Guardrails**
- If estimated cost exceeds a threshold, trigger a confirmation checkpoint
- Thresholds and estimator are configurable per product

**Result Digest Shape**
- Default digest is a short summary + key facts
- Avoids full payload injection into LLM context

---

## Phased Implementation Plan (Proposed)

Each phase is a deliverable unit. Checkboxes are meant to be ticked as implementation completes.

### Phase 1: Core Contract

- [ ] Task + steering models stabilized (TaskState, TaskContextSnapshot, ContextPatch, SteeringEvent)
- [ ] Steering payload validation per event type
- [ ] Per-task steering inbox routing + dedupe + audit logging
- [ ] Merge strategies (APPEND/REPLACE/HUMAN_GATED) with divergence flagging
- [ ] Context isolation (deep-copy snapshot + isolated memory namespace)
- [ ] Artifact metadata contract in updates and patches
- [ ] Subagent gating (no tasks.* tools for background tasks)
- [ ] Safe defaults (limits + HUMAN_GATED background merge)
- [ ] Prompt policy hooks (gated fragments only when enabled)

### Phase 2: Platform Interfaces

- [ ] SessionStateStore interface + in-memory implementation
- [ ] StateStore adapter integration
- [ ] Scheduler interface + in-memory/noop runner
- [ ] Idempotency support in task spawn
- [ ] Observability event schema + emit hooks

### Phase 3: Tooling + UX Wiring

- [ ] Meta-tools exposed to foreground agent (spawn/list/get/cancel/prioritize/apply_patch)
- [ ] Playground or API endpoints aligned with meta-tool surface
- [ ] UI notifications for background results + apply/reject flows
- [ ] Steering proxy flow from chat (agent-mediated control)

### Phase 4: Optional Policies

- [ ] Cost/consent hook + confirmation checkpoint
- [ ] Retry policy hook for background tasks
- [ ] Retention/TTL policy for task updates and artifacts
- [ ] ACL/ownership policy integration for multi-tenant products
