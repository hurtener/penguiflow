# RFC: A2A Router, Conversation Continuity, and Full Task-Oriented Remote Orchestration

**Status:** Draft v2
**Author:** Santiago Benvenuto
**Reviewer:** Claude (code-grounded review, 2026-04-22)
**Created:** 2026-04-22
**Revised:** 2026-04-22
**Target Version:** v3.7+

---

## Summary

This RFC proposes the missing pieces required to make PenguiFlow's A2A support fully usable for a production router agent.

Today, PenguiFlow already has enough A2A plumbing to support:
- A2A server bindings over HTTP+JSON, JSON-RPC, and gRPC
- task-oriented remote execution with streaming and cancellation
- task polling, subscription, and push notification configuration on the server side
- planner-facing wrappers that let remote A2A agents be used as bounded capabilities
- a first-class `RemoteBinding` model persisted through `StateStore.save_remote_binding` whenever a `RemoteNode` discovers a remote `task_id` (`penguiflow/remote.py:160-166`, `penguiflow/state/models.py:52-58`)
- server-side meta injection of `a2a_context_id` / `a2a_task_id` into `flow_message.meta` (`penguiflow_a2a/core.py:700-709`)

What it does not yet provide as a cohesive product surface is:
- a first-class router architecture
- stable conversation continuity across remote agent turns
- client-side task APIs beyond `send`, `stream`, and `cancel`
- end-to-end `input-required` and `auth-required` lifecycle mapping
- a first-class agent registry and scoring layer
- a durable production task store for A2A task state that does not fragment state across `penguiflow_a2a.TaskStore` and `StateStore.SupportsTasks`

This RFC defines a phased plan to close those gaps while preserving a low-risk first iteration:

1. adopt a manager-router architecture where specialists are bounded remote capabilities,
2. standardize conversation continuity by persisting the remote `context_id` against the router's own session — not by overloading `session_id` at the router boundary,
3. extend the A2A client surface to support full task-oriented orchestration via a dedicated `A2ATaskClient` that is a superset of the existing `RemoteTransport`,
4. map PenguiFlow pause/auth states to A2A lifecycle states, introducing a new Phase 3 runtime-trigger API (sketched in § Proposed Architecture 4 as `ctx.request_input(...)` / `ctx.request_auth(...)`; exact surface is a Phase 3 design deliverable),
5. add a registry and routing policy layer with concurrency caps and failure-routing,
6. harden persistence and recovery by unifying A2A task persistence with the existing `StateStore` capabilities rather than inventing a parallel registry.

### Recommended decision for v1

The recommended first production posture remains:
- the manager-router is the only user-facing agent,
- specialists are called as bounded remote capabilities,
- conversation continuity is opt-in per specialist,
- `context_id` is the remote conversation key,
- the router persists `(router_session_id, remote_agent_url, remote_skill) → remote_context_id` as an extended `RemoteBinding` — the router does **not** overwrite its own `session_id` with the remote `context_id`,
- specialists that run their own `A2AService` may set `session_id = a2a_context_id` internally as an ingress rule (this is a specialist-side choice, not a router-side one),
- and `task_id` reuse is reserved for explicit resume of a live non-terminal remote task.

---

## Motivation

### Current state

PenguiFlow's current A2A implementation is strongest when used in one of two modes:

1. PenguiFlow as an A2A server that exposes a flow or orchestrator to other agents.
2. PenguiFlow as a planner that treats remote A2A agents as bounded tools.

This is already enough to build a first manager-router, but it falls short of a full remote task orchestration story.

Key limitations in the current implementation (verified against the codebase):

- `A2AHttpTransport._build_send_message` sends only `messageId`, `role`, `parts`, and `metadata` on outbound messages; it never populates `message.contextId` or `message.taskId`, even though both fields are parsed from responses (`penguiflow_a2a/transport.py:49-60`, `185-199`).
- `A2AAgentToolset` propagates `session_id` through metadata (`penguiflow_a2a/planner_tools.py:87`), and surfaces `remote_context_id` / `remote_task_id` in streaming chunk metadata (`planner_tools.py:145-153`), but never captures those IDs back into tool context or persists them beyond the chunk.
- `A2AService` task status transitions in the runtime emit only `submitted`, `working`, `completed`, `failed`, and `cancelled`. The data model supports `input-required` and `auth-required` (`penguiflow_a2a/models.py:19-28`), but no runtime path currently flips a task to those states.
- The built-in A2A *client* surface does not expose `get_task`, `list_tasks`, `subscribe_task`, or push notification config APIs. Those operations exist only on the server side (`penguiflow_a2a/bindings/http.py:620-799`).
- `InMemoryTaskStore` (A2A-side) and `InMemoryStateStore.SupportsTasks` (runtime-side) are two independent in-memory task persistence surfaces with no reconciliation — a production deployment cannot durably recover without solving both.
- There is no first-class agent registry or skill-scored routing layer.
- The generated A2A template (`penguiflow/templates/new/rag_server/src/__package_name__/a2a.py.jinja:67-82`) reads `session_id` from payload, but does **not** consume `a2a_context_id` from `flow_message.meta`, so the documented "stock A2AService injects `a2a_context_id`" pathway is not actually exercised by the default scaffold.

### Why this matters

The target use case is no longer just "tool calling over HTTP."

An A2A router in production needs to support:
- remote specialist conversations that persist across turns,
- delayed or long-running specialist tasks,
- clean clarification loops when a specialist requires more input,
- recovery after process restarts,
- routing based on agent metadata rather than hardcoded endpoint names,
- shared artifact continuity across agents.

Without these pieces, PenguiFlow can host remote A2A calls, but the router still has to improvise too much of the control plane.

---

## Goals

- Define the recommended v1 architecture for router agents in PenguiFlow.
- Standardize conversation continuity across manager-router and specialists using the identities each side already owns (`router_session_id` vs `remote_context_id`) rather than conflating them.
- Add a full client-side A2A task API surface as a superset of `RemoteTransport`.
- Make `input-required` and `auth-required` first-class runtime states with a concrete, non-payload-overloading trigger.
- Introduce a first-class agent registry, scoring layer, concurrency cap, and failure-routing contract.
- Reuse the existing `RemoteBinding`, `StateStore`, and `Session` systems rather than inventing parallel persistence.
- Keep the rollout incremental and compatible with the current manager-router-as-tools pattern.

## Non-Goals

- Replace the current manager-router pattern with a peer-to-peer swarm model.
- Implement dynamic marketplace discovery in the first release.
- Make specialists own the user-facing clarification UX.
- Redesign StateStore or ArtifactStore from scratch.
- Require breaking schema migrations in the first continuity phase. Phase 1 does add nullable fields to `RemoteBinding` (`router_session_id`, `remote_skill`, `tenant_id`, `user_id`, `last_remote_task_id`, `is_terminal`, `metadata`); structured backends should handle these as additive `ALTER TABLE ADD COLUMN` operations with safe defaults. Backends that do not support online additive migration should stage the change behind a feature flag until rollout is complete. Any removal or rename is explicitly out of scope for v1.
- Introduce a separate router-side registry abstraction that duplicates `StateStore`.

---

## Current Implementation

### What already exists

#### Server-side A2A task surface

PenguiFlow already supports the spec-shaped server-side operations required for task-oriented A2A (`penguiflow_a2a/bindings/http.py:620-799`):
- `SendMessage`
- `SendStreamingMessage`
- `GetTask`
- `ListTasks`
- `CancelTask`
- `SubscribeToTask`
- push notification config CRUD
- Agent Card and Extended Agent Card support

The A2A service also maintains task state, artifacts, history, and subscriptions.

#### Client-side remote invocation

The current client-side A2A transport supports:
- unary send
- streaming send
- cancel

This is enough for treating specialists as bounded capabilities, but not enough for a router that wants to manage remote tasks explicitly after the initial call.

#### Continuity primitives already present

The codebase already has the core ingredients for continuity, and the RFC proposals should build on them rather than replace them:

- A2A `context_id` groups tasks into a conversation.
- `RemoteBinding(trace_id, context_id, task_id, agent_url)` is already persisted via `StateStore.save_remote_binding` (`penguiflow/state/models.py:52-58`, `penguiflow/state/protocol.py:40-44`, `penguiflow/remote.py:160-166`).
- `StateStore.save_remote_binding` is a *required* method of the `StateStore` Protocol (`state/protocol.py:40-44`). Whether any given durable backend implements it today is out of scope for this RFC to assert; the requirement is that the Protocol is already the contract, so Phase 1 does not introduce a new persistence surface.
- `StateStore.SupportsTasks` already models `TaskState(task_id, session_id, status, task_type, priority, context_snapshot, ...)` with a lifecycle (`PENDING`, `RUNNING`, `PAUSED`, `COMPLETE`, `FAILED`, `CANCELLED`) (`penguiflow/state/models.py:117-135`).
- `StateStore.SupportsPlannerState` already backs planner pause/resume via `save_planner_state` / `load_planner_state` — the mechanism that Phase 3 must build on.
- PenguiFlow application persistence already keys memory and artifacts on `session_id`.
- `A2AAgentToolset` already propagates `session_id` in metadata/tool context.
- The generated A2A template reads `session_id` from payload, but not `a2a_context_id` — this gap is addressed in Phase 0.
- The stock `A2AService` already injects `a2a_context_id` and `a2a_task_id` into `flow_message.meta`.

---

## Problem Statement

The gap is not the A2A wire format anymore. The gap is the router control plane.

To make A2A "fully available" for router agents, PenguiFlow needs:

1. a stable conversation identity that persists across agent-to-agent turns,
2. a router-owned, durable record of remote task and context bindings — expressed through the *existing* `RemoteBinding` and `StateStore`, not a parallel registry,
3. richer client APIs for task polling and subscription,
4. runtime mapping for `input-required` and `auth-required` with a well-defined trigger that does not overload the payload,
5. registry-based routing, scoring, concurrency limits, and failure policy,
6. durable task persistence that does not fragment between `TaskStore` (A2A-side) and `StateStore.SupportsTasks` (runtime-side).

---

## Design Principles

### 1. Manager-router owns the conversation

The recommended first architecture remains:
- the manager-router is the user-facing ReAct agent,
- specialists are remote bounded capabilities,
- the manager owns final synthesis and user clarifications.

This should remain true even after full A2A task orchestration is added.

### 2. Three identities, kept distinct

There are three identities at play in a router topology, and they must not be conflated:

| Identity | Owner | Lifetime | Purpose |
|---|---|---|---|
| `router_session_id` | Manager-router | User conversation lifetime | Memory, artifacts, HITL, user-facing audit |
| `remote_context_id` | Specialist A2A server | Agent-to-agent conversation lifetime (per specialist) | Remote history grouping, remote artifact scope, resume target |
| `remote_session_id` (optional) | Specialist application | Specialist-internal | Only relevant if the specialist itself runs a session model |

**Router-side rule:** `router_session_id` is stable; persist one `RemoteBinding` per `(router_session_id, remote_agent_url, remote_skill)` recording the effective `remote_context_id`. On the next turn to the same binding, reuse the stored `remote_context_id`.

**Server-side ingress rule (specialist only):** if `a2a_context_id` is present in `flow_message.meta` and no application `session_id` is present, the specialist may set `session_id = a2a_context_id` to bridge A2A continuity to its own session-scoped memory. This is a specialist choice, not a router one.

### 3. Fresh task, stable context

- `context_id` is stable across turns.
- `task_id` is unique per turn unless explicitly resuming a live non-terminal task.
- Reusing `task_id` across turns is forbidden; the server's terminal-task protections are the source of truth.

### 4. Reuse existing state abstractions

Conversation continuity, remote bindings, and task persistence should reuse:
- `StateStore` + `RemoteBinding` (router-side binding persistence),
- `StateStore.SupportsTasks` + `Session` (manager-router's own task/turn log),
- session-scoped memory and artifacts,
- existing tenant/user/session isolation.

New capabilities are added as optional `StateStore` extensions (e.g., `SupportsConversationBindings`), not as freestanding registries.

### 5. Progressive enhancement

The rollout should preserve the current working path:
- if raw A2A `context_id` is not yet exposed on the client, continuity must still work through `session_id` in payload or metadata,
- when both are present, `a2a_context_id` wins; metadata `session_id` is legacy.

### 6. Continuity is opt-in, not the default for every specialist

Not every remote specialist needs a long-lived conversation.

Recommended default:
- one-shot bounded call for pure request/response specialists,
- conversation continuity only for specialists that benefit from memory, follow-up turns, or artifact reuse,
- task resume only for specialists that can legitimately pause in a non-terminal state.

### 7. Session identity must stay out of LLM-visible context

`session_id`, `context_id`, `tenant_id`, and related routing identities should be propagated through:
- tool context,
- flow metadata,
- payload fields intended for orchestration,
- or A2A message metadata.

They should not be derived from or stored only in `llm_context`, which is an LLM-visible surface. `TaskContextSnapshot` (`penguiflow/state/models.py:86-102`) already enforces the `llm_context` / `tool_context` split; router code should follow that boundary.

### 8. Router-to-specialist trust is explicit

The router must declare how it authenticates to a specialist: delegated user token, service credential, tenant-scoped assertion, or none. Trust tier and auth scheme live on the registry entry. Specialists returning artifacts through the router are untrusted by default; the router is responsible for sanitization before user-facing exposure.

---

## Proposed Architecture

### 1. Extend `RemoteBinding`, do not introduce a parallel registry

The existing `RemoteBinding` already gives us `(trace_id, context_id, task_id, agent_url)` and is persisted through `StateStore.save_remote_binding`. Extend it additively rather than introducing a new `RemoteConversationBinding` type:

```python
@dataclass(slots=True)
class RemoteBinding:
    # Existing fields (already shipped):
    trace_id: str
    context_id: str | None
    task_id: str
    agent_url: str

    # Additive fields (this RFC):
    router_session_id: str | None = None
    remote_skill: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    last_remote_task_id: str | None = None
    is_terminal: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

For router use cases that need to look up a binding by `(router_session_id, agent_url, skill)` (rather than by trace), add an **optional** `StateStore` capability protocol alongside `SupportsTasks`, `SupportsPlannerState`, etc.:

```python
@runtime_checkable
class SupportsConversationBindings(Protocol):
    async def find_binding(
        self,
        *,
        router_session_id: str,
        agent_url: str,
        remote_skill: str,
    ) -> RemoteBinding | None: ...

    async def list_bindings(
        self, *, router_session_id: str
    ) -> Sequence[RemoteBinding]: ...

    async def mark_binding_terminal(
        self, *, trace_id: str, context_id: str | None, task_id: str
    ) -> None: ...
```

Durable backends adopt this the same way they adopt `SupportsTasks` today. The in-memory `StateStore` implementation already keys bindings by `(trace_id, context_id, task_id)` (`state/in_memory.py:234`) and can add a secondary index cheaply.

**Binding visibility contract:**
- Every `RemoteBinding` persisted via `save_remote_binding` is retrievable by its primary key `(trace_id, context_id, task_id)`.
- A binding participates in conversation lookup (`find_binding`, `list_bindings`) only when `router_session_id` is non-`None`. `RemoteNode` calls that do not run under a router session (e.g., one-shot flows) still persist a binding for trace-level lookup but are invisible to `SupportsConversationBindings`.
- `find_binding` requires a non-`None` `router_session_id`; it is not valid to probe for "any binding for this agent." This prevents accidental cross-session context reuse.

Purpose:
- the router can continue a specialist conversation without inventing continuity rules each time,
- subscriptions, resume paths, and follow-up turns all reference the same stored remote context,
- durable implementations restore bindings after restart using the StateStore backend they already ship.

### 2. Continuity Contract (router vs server)

#### Router-side contract

1. For each `(router_session_id, agent_url, remote_skill)` pair:
   - look up an existing `RemoteBinding` via `SupportsConversationBindings.find_binding`,
   - if found and non-terminal, reuse its `remote_context_id` on the next turn,
   - always mint a new `task_id` server-side by omitting `message.taskId` on the outbound send,
   - after the response, update `last_remote_task_id` and `is_terminal`.
2. Resume of an existing live task is a separate API (`A2AResumeOptions`), guarded by the binding's `is_terminal=False` flag and the terminal-task server-side protection.
3. `router_session_id` is *not* rewritten to match `remote_context_id` at the router boundary.

#### Server-side ingress contract (specialist)

A specialist built on PenguiFlow's stock `A2AService` already receives `a2a_context_id` / `a2a_task_id` in `flow_message.meta`. The specialist's entry adapter should apply the following precedence when resolving its own `session_id`:

1. `flow_message.meta["a2a_context_id"]` (A2A envelope `contextId`, stock-injected),
2. `message.metadata["session_id"]` (A2A message metadata, legacy router callers),
3. `payload["session_id"]` / `payload["sessionId"]` (legacy in-band payload convention),
4. a newly-minted session ID (fall-through).

Adapters must not silently reconcile disagreements between these signals — the higher-precedence value always wins, and a lower-precedence value is ignored rather than merged.

Optionally setting the specialist's application `session_id` equal to the resolved value is a specialist-internal decision, but the ingress rule above applies to *resolving* identity, not to adopting A2A semantics wholesale.

#### First-turn behavior

There are two valid first-turn patterns, both supported:

1. Client-minted context — the router generates the conversation ID up front, sends it as `context_id`, and reuses it on later turns.
2. Server-minted context — the first remote call omits `context_id`, the server returns a `context_id`, the router persists that returned value and reuses it on later turns.

Recommendation:
- allow both,
- standardize router persistence around the effective `remote_context_id` after the first successful turn,
- Phase 1 acceptance tests must cover both paths including server-side behavior under "known contextId, absent taskId" (see Phase 1).

### 3. Full Client Task API

Split the client-side surface into two tiers:

- **`RemoteTransport`** stays minimal (`send`, `stream`, `cancel`) so generic remote nodes and non-A2A transports remain interoperable.
- **`A2ATaskClient`** is a superset protocol extending `RemoteTransport` with A2A-specific task operations:
  - `get_task`
  - `list_tasks`
  - `subscribe_task`
  - `set_task_push_notification_config` — for the router to register its own callback
  - `get_task_push_notification_config` — deferred; no strong router use case in v1
  - `list_task_push_notification_configs` — deferred
  - `delete_task_push_notification_config` — for cleanup on task terminal

Router code and `A2AAgentToolset` accept `RemoteTransport | A2ATaskClient` and opportunistically use the richer surface when available via `isinstance` or capability probing.

Rationale for deferring get/list push configs: a router is a push *receiver*, not a push *configurator of third parties*. Set + delete (lifecycle ownership of the router's own callback) are useful; enumeration is not, and will not ship in Phase 2.

**Transport parity.** Phase 2 ships an HTTP implementation of `A2ATaskClient` (`A2AHttpTaskClient`). A gRPC client implementation is deferred to a follow-up phase; the existing server-side gRPC binding (`penguiflow_a2a/bindings/grpc.py`) continues to work for inbound traffic independently. Routers that need gRPC parity can implement `A2ATaskClient` themselves against the existing `a2a_pb2_grpc` stubs in the meantime.

### 4. Input-Required and Auth-Required Mapping

Map PenguiFlow runtime states to A2A task states:

| PenguiFlow condition | A2A state |
|---|---|
| task created | `submitted` |
| actively running | `working` |
| node calls `ctx.request_input(prompt, schema)` | `input-required` |
| node calls `ctx.request_auth(scheme, scopes)` | `auth-required` |
| final answer emitted | `completed` |
| unrecoverable planner/tool error | `failed` |
| explicit rejection/policy refusal | `rejected` |
| cancelled locally or remotely | `cancelled` |

#### Trigger contract (proposed; new in Phase 3)

`input-required` / `auth-required` are not payload overloads. Phase 3 proposes introducing a new, node-facing runtime API on `Context` to trigger them. **These APIs do not exist today** — the current pause mechanism is planner- and state-store-oriented, not core-Context-oriented. The shape below is a sketch of what Phase 3 must define before implementation begins; the exact method names, module location (on `Context`, on a new mixin, or behind an injectable helper), and error types are open for Phase 3 design review.

Sketch:

```python
# Illustrative — signatures below are proposed, not shipped.
SchemaT = TypeVar("SchemaT", bound=BaseModel)

class Context:
    async def request_input(
        self,
        *,
        prompt: str,
        schema: type[SchemaT],
        hint: str | None = None,
        timeout_s: float | None = None,
    ) -> SchemaT: ...

    async def request_auth(
        self,
        *,
        scheme: str,
        scopes: Sequence[str] = (),
        instructions: str | None = None,
        timeout_s: float | None = None,
    ) -> AuthArtifact: ...
```

Intended node-side use:

```python
async def my_node(msg: Message, ctx: Context) -> Message:
    choice = await ctx.request_input(
        prompt="Which project should I file this under?",
        schema=ProjectChoice,
    )
    ...
```

Intended behavior of a `request_input` call (each step is a Phase 3 design obligation, not a reference to existing code):

1. Persist a planner pause token through the existing `SupportsPlannerState.save_planner_state` surface.
2. Transition the runtime `TaskState.status` to `PAUSED` via `SupportsTasks.save_task`. **This is the authoritative state.**
3. On tasks that entered through `A2AService`, project the runtime `PAUSED` state to A2A `input-required`. The projection needs a status-transition hook on `A2AService` that is **not part of the stable surface today** — Phase 3 must introduce it (either by promoting the internal transition path to a supported method, or by publishing a new event that the A2A layer observes).
4. The structured status message attached to the projection carries `{"prompt", "schema", "hint"}` so the router can render the prompt and validate the reply.
5. Suspend the flow for this trace.
6. The router reads task status + structured payload, surfaces user UX, and resumes via the client-side resume API (`A2AResumeOptions`) with the user reply in `message.parts`.
7. On resume, parts are validated against `schema` (Pydantic `TypeAdapter`); a validation failure re-enters `input-required` with the error echoed in the status message rather than failing the task.

`ctx.request_auth` is analogous, with an auth scheme + scope payload and an `AuthArtifact` return type (opaque token container).

**Runtime-vs-protocol authority (proposed).** Two representations exist for a paused task, and their relationship mirrors § 7's TaskStore-over-StateStore pattern: the runtime `TaskState.status = PAUSED` is the source of truth; A2A `input-required` / `auth-required` are the spec-visible projection. Flows that never entered through `A2AService` stay `PAUSED` without an A2A projection. A2A resume sends transition the projection back to `working` *and* the runtime state back to `RUNNING` atomically.

**Cancellation during pause (proposed).** If the trace is cancelled (local cancel, remote `CancelTask`, or deadline exceeded) while paused:
- the planner pause token is invalidated,
- the runtime `TaskState` transitions `PAUSED` → `CANCELLED`,
- the A2A projection transitions `input-required` → `cancelled`,
- the awaiting `request_input` / `request_auth` call raises `TraceCancelled` (or Phase 3's chosen cancellation exception type).

**Timeout (proposed).** `timeout_s` is wall-clock from the moment of pause. On timeout the task transitions to `failed` with a stable error code (`input_required_timeout` / `auth_required_timeout`), not `cancelled` — a timeout is a runtime decision, not a user action.

**Phase 3 design obligations, before implementation:**
- Decide whether the trigger lives on `Context` or a separate mixin/helper injected via `ToolContext`.
- Decide how the A2A status projection is driven (direct coupling vs. event-observed). Preference: event-driven, so non-A2A ingress paths remain unaffected.
- Define the concrete error types and stable error codes used by timeouts and validation failures.
- Define the structured status-message schema the router observes (proposed: `A2AStatusPromptPayload` with `{prompt, schema_name, schema_json_schema, hint}`).

Design rule:
- specialists may surface `input-required`,
- the manager-router translates that into clean user clarification,
- the user never sees raw protocol state.

### 5. Agent Registry and Routing Policy

Introduce a first-class router registry with explicit metadata:

```python
@dataclass
class RegisteredRemoteAgent:
    agent_id: str
    agent_url: str
    agent_card: dict[str, Any]
    skills: list[str]
    input_modes: list[str]
    output_modes: list[str]
    supports_streaming: bool
    supports_task_client: bool  # true => A2ATaskClient is available
    auth_scheme: Literal["none", "service_bearer", "delegated_user_bearer", "tenant_assertion"]
    tenant_compatibility: frozenset[str] | Literal["*"]  # allowlist or wildcard
    trust_tier: Literal["first_party", "verified", "third_party"]
    latency_tier: Literal["interactive", "standard", "batch"]
    cost_tier: Literal["low", "standard", "premium"] | None = None
    max_in_flight: int = 1  # per-router concurrency cap
    enabled: bool = True
```

**`trust_tier` v1 values:**
- `"first_party"` — same team/org; router may pass specialist output verbatim to users.
- `"verified"` — external but audited/contractually trusted; router passes output through a lightweight sanitizer.
- `"third_party"` — untrusted; router treats output as user-generated content and applies full sanitization before user-facing use.

**`latency_tier` v1 values** (advisory for scoring, not enforced deadlines): `"interactive"` (<2s p95), `"standard"` (<30s p95), `"batch"` (no p95 guarantee).

Initial scoring dimensions:
- skill match
- tenant compatibility (set membership, not single-value)
- required auth satisfied
- streaming support
- trust tier
- latency tier

Failure-routing contract (v1, required):
- On `failed` or `rejected` from specialist A, the registry policy declares whether to retry on an alternate specialist with matching skill, and how many times.
- **Failover always mints a new context on the alternate specialist.** A `remote_context_id` issued by specialist A is meaningless to specialist B; carrying it across a failover would either error server-side or silently create an unbound context. The router discards the original binding's `remote_context_id` when constructing the failover call and persists a new `RemoteBinding` for specialist B keyed to the same `router_session_id`.
- The original `RemoteBinding` for specialist A is marked `is_terminal=True` on failover decisions, not reused on subsequent turns.
- On `cancelled`, no retry — cancellation is authoritative.
- On transport error, retry is controlled by `NodePolicy`, not the registry; transport retries reuse the same `remote_context_id` because the call never reached the specialist's decision layer.

Rate control (v1, required):
- `max_in_flight` is enforced via a per-agent semaphore at router call time,
- a registry entry that is saturated short-circuits to "try next candidate" rather than queueing forever.

Deferred dimensions:
- dynamic health
- cost optimization
- learned routing
- marketplace discovery
- persisted registry-learned state (until Phase 5 unifies persistence)

### 6. Shared Artifact Continuity

When one specialist produces data that another specialist or the manager should continue working with, the router is the mediator. Specialists never address each other's artifacts directly.

Rule:
- A2A task artifacts are transport-visible execution outputs belonging to the emitting specialist.
- When the router ingests a specialist artifact it intends to reuse, it rewrites the ref into the router's `artifact_store` scope keyed by `router_session_id` (and `tenant_id`, `user_id` when those apply).
- Subsequent specialists receive a router-minted artifact ref, not the original.

Minimum artifact handoff contract (router-minted refs):
- `artifact_id`
- `router_session_id`
- `tenant_id`
- `user_id` when user scoping is used

Optional additions:
- `namespace`
- `artifact_type`
- `display_name`
- `source_agent_url` (provenance; opaque to consumers)

This keeps artifact scope consistent with router session isolation even when specialists run under distinct `remote_context_id`s.

### 7. Durable, Unified A2A Task Store

The default `InMemoryTaskStore` (A2A side) and `InMemoryStateStore.SupportsTasks` (runtime side) are two parallel task-persistence surfaces. Running them as independent durable backends produces exactly the continuity split-brain this RFC warns against.

Phase 5 must pick one of three relationships and document it:

1. **Projection (recommended).** A2A `TaskStore` becomes a read/write projection over `StateStore`. The durable backend is a single `StateStore` adapter implementing `SupportsTasks` + `SupportsConversationBindings` + an A2A-shaped `TaskStoreView`. A2A-specific concerns (history messages, artifacts, subscription fanout, push configs) live in additional optional capabilities (`SupportsA2AHistory`, `SupportsA2ASubscriptions`, `SupportsA2APushConfigs`).
2. **Canonical adapter.** `TaskStore` stays its own abstraction but ships a `StateStoreBackedTaskStore` canonical implementation that persists into `StateStore.SupportsTasks`. Downstream teams plug this in once.
3. **Separate with a reconciliation contract.** Only acceptable if option 1 or 2 creates unworkable coupling. Must document which store is authoritative for which field, and define a reconciliation job.

Recommendation: adopt option 1 and add the optional capability protocols listed above. The A2A spec does not require a standalone task-store abstraction; its surface is the HTTP/JSON-RPC/gRPC layer.

**Option 1 is not "free."** A projection still has to carry A2A-specific data that is not native to `TaskState`:
- ordered A2A history messages (multi-part, per-role),
- per-task artifact collections with A2A artifact IDs distinct from PenguiFlow `artifact_store` refs,
- subscription fanout state (client cursors, resumable delivery),
- push notification config records.

The optional capability protocols (`SupportsA2AHistory`, `SupportsA2ASubscriptions`, `SupportsA2APushConfigs`) exist precisely to carry this data in a typed, testable way. Teams adopting option 1 implement all three alongside `SupportsTasks`; the `TaskStoreView` composes them. Deployments that do not need A2A-specific features (subscription, push) may implement only the subset they use, with the `TaskStoreView` degrading gracefully (e.g., subscribe returns a one-shot terminal state if `SupportsA2ASubscriptions` is absent).

Durable requirements in any case:
- survives process restart
- supports filtering by `context_id`
- supports pagination and timestamp-based listing
- supports subscription fanout through a broker or resumable mechanism
- supports recovery replay: given persisted `RemoteBinding`s and tasks, the router can resume `input-required` tasks without re-sending user turns

### 8. Router-to-Specialist Auth Boundary

Router-to-specialist auth is declared on the registry entry and resolved per call. v1 supports:

- `auth_scheme = "none"` — no credential.
- `auth_scheme = "service_bearer"` — router presents a service credential (value resolved from a secret provider at call time).
- `auth_scheme = "delegated_user_bearer"` — router forwards a user token already carried in `tool_context["user_auth"]`.
- `auth_scheme = "tenant_assertion"` — router signs a short-lived tenant-scoped assertion (JWT with `tenant_id`, `user_id`, `router_session_id`).

Tenant scope propagation:
- `tenant_id` from tool context is forwarded in `message.metadata["tenant_id"]`.
- Specialists reject calls whose tenant is not in `RegisteredRemoteAgent.tenant_compatibility` (enforced router-side before sending).

Sanitization:
- Artifacts and text returned by specialists at `trust_tier != "first_party"` must pass through a router-side sanitizer before being included in user-facing output. The sanitizer contract is application-defined; the RFC only requires the extension point.

### 9. Manager Conversation Persistence

The manager-router persists its own turn log (user turns, specialist calls, synthesis outputs) using the *existing* `StateStore.SupportsTasks` + `StateUpdate` surface. The router's `Session` is the single source of truth for user-level conversation state.

This means the manager-router does not invent a fourth store: it reuses what `penguiflow.sessions.Session` already provides, and binds remote calls to it via `RemoteBinding.router_session_id`.

---

## API Changes

### 1. Extend A2A request construction

Update the client request path so raw A2A message fields can be supplied explicitly.

Recommended extension for normal sends:

```python
@dataclass
class A2AConversationOptions:
    context_id: str | None = None
    history_length: int | None = None
    accepted_output_modes: list[str] | None = None
    push_notification_config: PushNotificationConfig | None = None
    context_strategy: Literal["fresh", "reuse", "explicit"] = "fresh"
    session_bridge: bool = False
```

Recommended extension for explicit resume:

```python
@dataclass
class A2AResumeOptions:
    context_id: str
    task_id: str
    # Message parts carry the user reply for input-required, or
    # an auth artifact for auth-required.
```

`context_strategy` and `session_bridge` replace the earlier `conversation_mode` single knob because they gate two orthogonal behaviors:

- `context_strategy` controls which `context_id` (if any) goes on the outbound message:
  - `"fresh"` — never send a `context_id`; let the server mint one and capture it.
  - `"reuse"` — look up an existing `RemoteBinding` and send its `remote_context_id`.
  - `"explicit"` — send the `context_id` provided in `A2AConversationOptions`.
- `session_bridge` controls whether the *specialist server* should set `session_id = a2a_context_id` on ingress. Router code does not consume this flag; it is advisory metadata for specialist-side adapters that want to honor it.

Design rule:
- `context_id` is available for normal multi-turn continuity.
- `task_id` is reserved for explicit resume APIs rather than ordinary sends, because reusing `task_id` is semantically dangerous and only valid for live non-terminal tasks.

### 2. Extend planner-facing tool wrappers

`A2AAgentToolset` should support:
- explicit continuity strategy,
- optional reuse of prior `context_id` through `RemoteBinding` lookup,
- optional capture of returned `context_id` / `task_id` into `StateStore`,
- optional resume of active non-terminal tasks through a dedicated resume path,
- task-mode helpers for routers.

Potential additions:

```python
tool(
    ...,
    context_strategy="fresh" | "reuse" | "explicit",
    conversation_key_builder=...,      # router_session_id builder
    task_client=...,                   # A2ATaskClient when richer surface is needed
    session_bridge=False,              # advisory for specialist-side ingress
    max_in_flight=None,                # per-call override of registry cap
    on_task_terminal=None,             # hook to mark RemoteBinding terminal
)
```

When `context_strategy="reuse"` is set, the wrapper:
1. computes `router_session_id` via `conversation_key_builder(ctx)` (default: `ctx.tool_context.get("session_id")`),
2. looks up an existing `RemoteBinding` via `SupportsConversationBindings.find_binding`,
3. sends the stored `remote_context_id` (if any, non-terminal),
4. updates the binding after the response.

### 3. Server-side flow metadata contract

Standardize the metadata that the stock `A2AService` injects into `flow_message.meta` and document it as supported ingress context:
- `a2a_task_id`
- `a2a_context_id`
- `a2a_message_id`
- `a2a_role`
- `a2a_requested_extensions`

Recommended specialist-side boundary rule:
- if application `session_id` is absent in tool context,
- and `a2a_context_id` is present in meta,
- set `session_id = a2a_context_id`.

Phase 0 updates the generated template to implement this rule (`templates/new/rag_server/src/__package_name__/a2a.py.jinja`).

### 4. Server-side resume semantics

The server-side `SendMessage` path must accept:
- a `taskId` referencing a non-terminal task (merge user reply into that task, transition `input-required` → `working`),
- a `contextId` with no `taskId` (mint a new task under the existing context),
- a message with neither (default: new context + new task).

Terminal-task reuse remains a hard error (the current protection is correct and stays).

### 5. Observability contract

Router features emit `FlowEvent`s (`penguiflow/metrics.py`) alongside the existing `remote_call_start` / `remote_call_success` / `remote_call_error` events that `RemoteNode` already emits (`penguiflow/remote.py:389-472`). New event types, scoped per phase:

| Event | Phase | Payload |
|---|---|---|
| `remote_binding_reuse` | 1 | `router_session_id`, `agent_url`, `remote_skill`, `remote_context_id` |
| `remote_binding_miss` | 1 | `router_session_id`, `agent_url`, `remote_skill`, reason (`"not_found"`, `"terminal"`) |
| `remote_task_poll` | 2 | `task_id`, `status`, `latency_ms` |
| `remote_task_subscribe` | 2 | `task_id`, lifecycle (`"open"`, `"close"`, `"error"`) |
| `a2a_pause` | 3 | `task_id`, kind (`"input-required"` \| `"auth-required"`), `timeout_s` |
| `a2a_resume` | 3 | `task_id`, `latency_ms` (pause → resume), outcome (`"success"` \| `"validation_failed"` \| `"timeout"`) |
| `remote_failover` | 4 | `router_session_id`, `from_agent_url`, `to_agent_url`, `skill`, `reason` |
| `remote_cap_wait` | 4 | `agent_url`, `wait_ms`, outcome (`"acquired"` \| `"short_circuited"`) |
| `remote_binding_recovered` | 5 | `router_session_id`, `agent_url`, `remote_context_id`, `last_remote_task_id` |

All events carry `trace_id` via the standard `FlowEvent` fields. Existing `on_event` middleware hooks consume them without modification; no new subscription API is required.

---

## Phased Rollout

### Phase 0: Formalize the current recommended pattern

Deliverables:
- document manager-router as the recommended v1 architecture,
- document the three-identity rule (`router_session_id`, `remote_context_id`, optional `remote_session_id`),
- document "fresh task, stable context" as the required rule,
- document shared artifact refs for cross-agent handoff (router-mediated),
- update the `rag_server` jinja template (`templates/new/rag_server/src/__package_name__/a2a.py.jinja`) so the adapter prefers `a2a_context_id` from `flow_message.meta` over payload `session_id`,
- add a Phase 0 test that exercises this precedence.

Expected outcome:
- teams can build safe v1 router agents using the current tool-style remote invocation path,
- the shipped template reflects the RFC.

### Phase 1: Client continuity support

Deliverables:
- add explicit `context_id` support to `A2AHttpTransport._build_send_message` (populate `message.contextId` when provided),
- extend `RemoteBinding` with the additive fields in § Proposed Architecture 1,
- add `SupportsConversationBindings` to `penguiflow/state/protocol.py` and implement in `InMemoryStateStore`,
- have `A2AAgentToolset` / `RemoteNode` capture returned `context_id` and `task_id` into `RemoteBinding` (extending the existing `save_remote_binding` call),
- keep payload/metadata `session_id` fallback for backward compatibility; document precedence,
- add tests for multi-turn continuity across the same specialist conversation.

Acceptance tests (Phase 1, all required):
1. **Client-minted context, two turns:** turn 1 sends explicit `context_id`, turn 2 reuses it; both tasks exist server-side under the same context with distinct `task_id`s.
2. **Server-minted context, two turns:** turn 1 omits `context_id`, router captures it from response, turn 2 sends it back; binding is updated.
3. **Server accepts known `contextId` with no `taskId`:** server mints a new task and associates it with the existing context; `ListTasks?contextId=...` returns both.
4. **MessageId dedupe:** retrying the same `SendMessage` (same `messageId`) does not produce duplicate tasks.
5. **Precedence:** when payload `session_id` and `a2a_context_id` disagree, specialist-side ingress honors `a2a_context_id`.

Expected outcome:
- routers can preserve specialist conversation identity without custom payload conventions,
- server-side continuity semantics are verified, not assumed.

### Phase 2: Full client task APIs

Deliverables:
- define `A2ATaskClient` Protocol extending `RemoteTransport`,
- implement client-side `get_task`, `list_tasks`, `subscribe_task`,
- implement `set_task_push_notification_config` and `delete_task_push_notification_config` on the client side (router callback lifecycle),
- explicitly defer `get_task_push_notification_config` / `list_task_push_notification_configs` with a short rationale in the module docstring,
- add convenience methods for router use cases (e.g., `wait_for_terminal(task_id, timeout_s)`),
- add a canonical `A2AHttpTaskClient` implementation.

Expected outcome:
- remote agents can be treated as full task-oriented services, not just remote tools,
- a router can observe a non-blocking specialist task through poll *or* subscribe, and clean up its own push config on terminal.

### Phase 3: Input/Auth-required runtime mapping

Phase 3 begins with a design review that resolves the obligations listed at the end of § Proposed Architecture 4 (trigger surface location, A2A projection mechanism, error types, status payload schema). No implementation work begins until those are decided.

Deliverables (following the design review):
- introduce the node-facing runtime-trigger API for `input-required` / `auth-required` (sketch: `Context.request_input(prompt, schema, ...)` / `Context.request_auth(scheme, scopes, ...)`; final surface decided in Phase 3 design review),
- wire both to the existing `SupportsPlannerState` pause mechanism,
- introduce a supported A2A status-transition hook (replacing direct use of `A2AService`'s internal `_set_status` path) and drive it from the pause transition — preferably event-observed so non-A2A ingress is unaffected,
- extend server-side `SendMessage` to accept resume sends (known `taskId` + non-terminal state) and merge user reply into the pending task,
- add client-side `A2AResumeOptions` and a resume API on `A2ATaskClient`,
- define the structured status-message schema the router consumes,
- add manager-router patterns for user clarification and resume,
- add tests for clarification loops and auth-gated flows.

Acceptance tests (Phase 3, all required):
1. **Clarification round-trip:** specialist enters `input-required`; router observes via `get_task` and `subscribe_task`; router sends a resume with user reply; task transitions to `working` and then `completed`.
2. **Terminal-task guard:** resume against a `completed` task is rejected with a stable error code.
3. **Auth round-trip:** `auth-required` → router acquires auth artifact → resume succeeds.
4. **Pause persistence:** planner state is durable across a simulated process restart within Phase 3's in-memory `StateStore` (full durability test is Phase 5).

Expected outcome:
- specialists can safely ask for more information while the manager retains UX ownership,
- pause/resume is end-to-end, not just a state field in the data model.

### Phase 4: Agent registry and scoring

Deliverables:
- implement a static registry format (`RegisteredRemoteAgent`) with the enumerated `auth_scheme` / `trust_tier` / `latency_tier` value sets,
- implement scoring utilities (skill match, tenant set membership, auth, streaming, trust/latency tiers),
- implement per-agent `max_in_flight` concurrency caps via `asyncio.Semaphore` at the router call boundary,
- implement the failure-routing contract, including: `failed` / `rejected` → retry on alternate candidate up to N times with **a fresh context on the alternate**, original binding marked `is_terminal=True`; `cancelled` → no retry; transport-level retry reuses the original context,
- emit the observability events listed in § Proposed Architecture 5 (`remote_failover`, `remote_cap_wait`, `remote_binding_miss`),
- add planner/router helpers that choose specialists from registry metadata,
- support trust, latency, and tenancy filters.

Expected outcome:
- routing moves from hardcoded remote nodes to a first-class routing layer with explicit failure behavior and backpressure.

### Phase 5: Durable A2A task persistence + recovery

Deliverables:
- pick and implement the unification option from § Proposed Architecture 7 (recommended: option 1, projection over `StateStore`),
- add `SupportsA2AHistory`, `SupportsA2ASubscriptions`, `SupportsA2APushConfigs` optional capability protocols,
- add a durable `StateStore`-backed `TaskStoreView`,
- recovery logic for router conversation bindings,
- restart and replay tests,
- production guidance (migration from `InMemoryTaskStore` to the unified backend).

Acceptance tests (Phase 5):
1. **Task store durability:** tasks survive restart; `list_tasks?contextId=...` returns pre-restart tasks.
2. **Binding recovery:** on restart the router reloads `RemoteBinding`s and can continue a conversation without a user-visible reset.
3. **Resume replay:** restart in the middle of an `input-required` pause; after restart, the router can still deliver the resume send and the task completes.
4. **No split-brain:** the same `task_id` appears with identical status in both the A2A task read path and the underlying `StateStore.SupportsTasks` view.

Expected outcome:
- task and conversation continuity survive process restarts and deploy cycles,
- a single durable backend powers both A2A task visibility and runtime session state.

---

## Backward Compatibility

### Compatible behaviors to preserve

- Existing `A2AAgentToolset` usage should continue working without explicit continuity options (defaults to `context_strategy="fresh"`, `session_bridge=False`).
- Existing `A2AHttpTransport` usage should continue working for one-shot bounded remote calls.
- Existing template-based orchestrators that rely on `session_id` in payload should keep working; Phase 0 additively prefers `a2a_context_id` when present.
- `RemoteBinding` additions are additive and default-safe; existing `StateStore` implementations continue to satisfy the Protocol.
- `SupportsConversationBindings` is *optional*; missing it degrades gracefully to "no binding reuse, fresh context every turn."

### Migration strategy

Phase 1 is additive:
- if `context_id` is supplied, send it as raw A2A `message.contextId`,
- if not, keep sending metadata/payload-based `session_id`,
- server-side apps continue to map `session_id` into orchestrator sessions.

Phase 3 introduces new `Context` methods (`request_input`, `request_auth`); existing flows that never call them are unaffected.

Phase 5 introduces a new durable backend; the in-memory default remains for local development and tests. Teams running the A2A `InMemoryTaskStore` in production were never supported.

---

## Testing Strategy

Every phase should land with targeted tests.

### Required acceptance tests

1. Multi-turn continuity (Phase 1)
   - first call creates a new remote `context_id`
   - second call reuses that `context_id`
   - second call creates a fresh `task_id`

2. Server accepts known `contextId` with no `taskId` (Phase 1)
   - new task minted under existing context
   - both tasks listable by `context_id`

3. Non-terminal task resume (Phase 3)
   - specialist enters `input-required`
   - manager stores `task_id` and `context_id`
   - resume continues the same remote task

4. Terminal task guard (Phase 3)
   - router attempts to reuse a completed `task_id`
   - operation is rejected cleanly with a stable error code

5. Session continuity bridge (Phase 0/1)
   - client sends `session_id` but no raw `context_id` → specialist falls back to `session_id`
   - client sends both → specialist prefers `a2a_context_id`
   - memory/artifact continuity is preserved in both cases

6. Continuity mode isolation (Phase 1)
   - `context_strategy="fresh"` calls do not reuse prior session state
   - `context_strategy="reuse"` reuses stored `remote_context_id`
   - two specialists under the same `router_session_id` do not cross-contaminate contexts

7. Shared artifact handoff (Phase 0/1)
   - specialist A emits an A2A artifact
   - router rewrites the ref into its own artifact store under `router_session_id`
   - specialist B resolves the router-minted ref without needing A's original scope

8. Task polling and subscription (Phase 2)
   - router can fetch remote task state after a non-blocking start
   - router can subscribe to updates and observe final completion
   - push config set/delete round-trips cleanly

9. Restart recovery (Phase 5)
   - durable task store survives restart
   - router can reload `RemoteBinding`s
   - remote task list by `context_id` remains available
   - mid-pause restart still allows resume delivery

10. Auth and clarification lifecycle (Phase 3)
    - `ctx.request_input` maps to `input-required`
    - `ctx.request_auth` maps to `auth-required`
    - manager converts those into proper user-facing next steps

11. Idempotency (Phase 1)
    - retrying a `SendMessage` with the same `messageId` does not create a duplicate task

12. Failure routing (Phase 4)
    - `failed` / `rejected` from specialist A triggers retry on specialist B per policy
    - `cancelled` never triggers retry
    - `max_in_flight` exhaustion short-circuits to the next candidate instead of queueing
    - **failover on A mints a fresh context on B; specialist A's `remote_context_id` is not forwarded**; the original binding is marked terminal
    - transport-level retry (no specialist decision reached) reuses the original `remote_context_id`

13. No split-brain (Phase 5)
    - status, history, and artifacts are consistent between the A2A task view and the underlying `StateStore` view

14. Pause cancellation (Phase 3)
    - cancelling a trace while paused on `request_input` raises `TraceCancelled` at the awaiting node
    - the runtime task transitions `PAUSED` → `CANCELLED` and the A2A projection transitions `input-required` → `cancelled` atomically

15. Pause timeout (Phase 3)
    - `request_input(timeout_s=…)` with no reply transitions the task to `failed` with error code `input_required_timeout` (not `cancelled`)

---

## Risks and Mitigations

### Risk: leaking protocol details into UX

If specialists expose `input-required` directly to end users, the manager loses ownership of the conversation.

Mitigation:
- require manager translation of remote clarification states,
- keep specialists bounded and non-user-facing.

### Risk: conflating task identity with conversation identity

Reusing `task_id` across turns will break task semantics and conflict with terminal-task protections.

Mitigation:
- make `context_id` the only conversation-level stable key,
- document "fresh task, stable context" as a hard rule.

### Risk: continuity split-brain between A2A and app sessions

If `context_id` and `session_id` diverge silently, memory and artifacts may fragment.

Mitigation:
- the router persists `remote_context_id` against `router_session_id` explicitly via `RemoteBinding`,
- specialists apply `session_id = a2a_context_id` only as an ingress rule,
- artifact handoff is always router-mediated.

### Risk: persistence split-brain between `TaskStore` and `StateStore`

Two independent durable task stores will drift.

Mitigation:
- Phase 5 unifies them (recommended option: projection over `StateStore`),
- Phase 5 includes a "no split-brain" acceptance test.

### Risk: non-durable availability claims

Without a durable task store, polling and context grouping disappear on restart.

Mitigation:
- do not call the feature fully available for production until Phase 5 lands with recovery replay coverage.

### Risk: duplicate tasks on retry

Transient network errors can cause a router to retry a `SendMessage`, producing duplicate tasks under the same context.

Mitigation:
- server dedupes by `messageId` within a context,
- Phase 1 acceptance test #4 enforces this.

### Risk: router-side DoS through fan-out

A misconfigured registry lets a router flood a specialist.

Mitigation:
- per-agent `max_in_flight` semaphore,
- saturation short-circuits to the next candidate rather than queueing without bound.

### Risk: untrusted specialist output flowing to users

A specialist at a low `trust_tier` can inject content the manager-router forwards verbatim.

Mitigation:
- artifacts and text from `trust_tier != "first_party"` pass through a router-side sanitizer before user-facing use,
- sanitizer is an application extension point; the RFC only requires the hook.

### Risk: auth identity confusion

Unclear router-to-specialist auth produces either over-permissioned calls or silent 401 loops.

Mitigation:
- `auth_scheme` is declared per registry entry,
- tenant scope is enforced router-side before the outbound send,
- no auth scheme defaults to "none" with an explicit opt-in.

---

## Open Questions

1. **Should the richer client API live inside `A2AHttpTransport`, or beside it as a dedicated `A2ATaskClient`?**

   **Resolution (this RFC):** keep `RemoteTransport` minimal; add a dedicated `A2ATaskClient` Protocol that is a superset of `RemoteTransport`. `A2AHttpTransport` ships as the default HTTP implementation of `A2ATaskClient`; generic `RemoteTransport` implementations remain valid first-class citizens.

2. **Should router conversation bindings live inside `StateStore`, a dedicated registry, or both?**

   **Resolution (this RFC):** reuse the existing `RemoteBinding` model, extend it additively, and surface lookup-by-conversation through an optional `SupportsConversationBindings` capability on `StateStore`. No separate registry abstraction.

3. **Should `input-required` be represented only as task status, or also as a structured payload contract?**

   **Resolution (this RFC):** status-first in Phase 3 using the `ctx.request_input(prompt, schema)` trigger. The schema is carried as a structured clarification payload in the task status message from the start (not deferred); `prompt` is the user-facing string, `schema` is machine-readable. This avoids reworking the contract between Phase 3 and a follow-up release.

4. **Should registry entries be static config only, or also support Agent Card fetch/refresh?**

   **Resolution (this RFC):** static config in Phase 4; optional refresh in a later phase. Dynamic registry state (observed latency, health) is deferred until persistence can back it (post-Phase 5).

5. **Should the router binding store be a separate abstraction or a StateStore projection?**

   **Resolution (this RFC):** a StateStore projection via `SupportsConversationBindings`. The in-memory `StateStore` already carries the primary `RemoteBinding` index; adding the secondary index by `(router_session_id, agent_url, skill)` is cheap.

6. **Does the A2A `TaskStore` remain its own abstraction?**

   **Open for Phase 5 resolution.** Recommended answer: no — it becomes a projection over `StateStore` with A2A-specific optional capabilities. Alternatives (canonical adapter, separate with reconciliation) are documented in § Proposed Architecture 7 and must be resolved when Phase 5 begins implementation.

7. **Who sanitizes specialist output before the router shows it to the user?**

   **Resolution (this RFC):** the application, through a router-side sanitizer extension point. The RFC does not ship a default sanitizer; it only requires that specialists at `trust_tier != "first_party"` pass through one before user-facing use.

---

## Done Definition

PenguiFlow should consider A2A router support "fully available" when all of the following are true:

- a manager-router can preserve remote specialist conversations across turns via extended `RemoteBinding` + `SupportsConversationBindings`,
- the client can poll, list, subscribe to, and cancel remote tasks through `A2ATaskClient`,
- specialists can surface `input-required` and `auth-required` states via the Phase 3 runtime-trigger API (final surface decided in Phase 3 design review), with round-trip resume semantics enforced server-side,
- routing decisions can be made from a first-class registry with scoring, concurrency caps, and a failure-routing contract,
- shared artifacts are router-mediated and keyed by `router_session_id`, preserving session isolation across specialists,
- task/conversation state survives service restarts via a durable backend that does not split-brain between A2A and runtime persistence,
- router-to-specialist auth is explicit per registry entry, and low-trust specialist output is sanitized before user-facing use.

Until then, the supported production posture is:
- manager-router owns the user conversation,
- specialists are bounded capabilities,
- continuity uses extended `RemoteBinding` keyed by `router_session_id`,
- specialists may bridge `session_id = a2a_context_id` on ingress if they choose,
- and richer task orchestration is phased in incrementally.
