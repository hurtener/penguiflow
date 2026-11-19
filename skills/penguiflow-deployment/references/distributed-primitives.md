# Distributed Primitives

A planner deployment spreads across processes through two protocols — `StateStore` (durability) and `RemoteTransport` (agent-to-agent calls) — plus the A2A bindings built on them. You provide the backends; the planner, sessions and A2A toolsets call them at the right hooks.

## `StateStore` (durability + audit)

Required for any feature that must survive a process restart or move between workers.

Base protocol (`penguiflow.state.StateStore`):
- `save_event(event: StoredEvent) -> None` — idempotent persistence of runtime events (sessions write task/steering events here).
- `load_history(trace_id: str) -> Sequence[StoredEvent]` — read events for a trace (audit, `penguiflow-admin`).
- `save_remote_binding(binding: RemoteBinding) -> None` — persist agent-to-agent bindings.

Optional capabilities (duck-typed, detected at runtime):

| Capability | Methods | Used by |
|---|---|---|
| Planner pause state | `save_planner_state`, `load_planner_state` | `PlannerPause` → resume on any worker ([[penguiflow-hitl-pause-resume]]) |
| Memory | `save_memory_state`, `load_memory_state` | short-term memory ([[penguiflow-memory]]) |
| Conversation bindings | `find_binding`, `list_bindings`, `mark_binding_terminal` | `A2AAgentToolset` continuity ([[penguiflow-a2a-integration]]) |
| Tasks / updates | `save_task`, `list_tasks`, `save_update`, `list_updates` | `StreamingSession` persistence and replay ([[penguiflow-background-tasks]]) |
| Steering | `save_steering`, `list_steering` | steering audit |
| Trajectories / planner events | `save_trajectory`, `get_trajectory`, `save_planner_event`, `list_planner_events` | run inspection |
| Artifacts | artifact store methods | rich output / large payloads ([[penguiflow-rich-output]]) |

Wire it once:
```python
planner = ReactPlanner(..., state_store=store)
sessions = SessionManager(state_store=store)
service = A2AService(planner, agent_card=card, config=A2AConfig(agent_url=...))   # uses the planner's store for bindings
```

Backends: in-memory (`InMemoryStateStore`, dev only), Redis, Postgres, custom. Full contract in [[penguiflow-statestore]].

### Idempotency rule
`save_event` and `save_update` may be called more than once for the same record (retries, recovery). Implementations must dedupe (unique constraint on event/update id). Event persistence is best-effort; errors are logged and execution continues.

## `RemoteTransport` (agent-to-agent calls)

`penguiflow.remote.RemoteTransport`:
```python
class RemoteTransport(Protocol):
    async def send(self, request: RemoteCallRequest) -> RemoteCallResult: ...
    def stream(self, request: RemoteCallRequest) -> AsyncIterator[RemoteStreamEvent]: ...   # not async; returns iterator directly
    async def cancel(self, *, agent_url: str, task_id: str) -> None: ...                    # keyword-only
```

Optional richer surface: `SupportsRemoteTasks` (same module) adds `send_task`, `get_task`, `list_tasks`, `subscribe_task` and the four `*_task_push_notification_config*` methods. `A2AHttpTransport` implements both.

`RemoteCallRequest` carries a `penguiflow.types.Message` (`payload` + `Headers(tenant=...)`), the remote `skill`, `agent_url`, optional `context_id` / `task_id` for continuity, and `metadata`.

Planners consume a transport through `A2AAgentToolset(agent_url=..., transport=...)` / `A2ARouterToolset`, which turn remote skills into catalog tools, persist `RemoteBinding`s, map remote `input-required` / `auth-required` to planner pauses, and propagate cancellation (`cancel_on_cancel=True`).

### Implementations
- `A2AHttpTransport` (in `penguiflow_a2a`) for HTTP A2A endpoints.
- For gRPC, internal RPC or message queues — implement the protocol yourself.

## A2A handoff

- **Manager**: `A2AAgentToolset` / `A2ARouterToolset` tools in its catalog.
- **Specialist**: `A2AService(planner, ...)` + `install_a2a_http(app, service)` (or the gRPC binding).

Each specialist task is one planner run keyed by the A2A `contextId` (`tool_context["session_id"]`), so conversations stay coherent across calls. See [[penguiflow-a2a-integration]]; don't duplicate here.

## Recovery semantics

On worker restart you can recover:
- **Paused runs** — `load_planner_state(token)`; call `planner.resume(token, user_input=...)` on any worker.
- **Memory** — `load_memory_state(key)` on the next run for that `session_id`.
- **Session history** — `list_tasks` / `list_updates` for reconnecting clients.
- **Remote conversations** — `find_binding` returns the remote `context_id` / `task_id`.

What's NOT recovered automatically:
- In-flight planner runs. They die with the worker; re-run the job (or resume from the last pause).
- In-flight tool calls. Their side effects may or may not have happened.
- Remote tasks started by a dead run — cancel them via the recorded bindings if needed.

Implication: design for at-least-once. Anything sensitive (writes, sends) needs idempotency keys.

## At-least-once + idempotency rules

1. **Every external write** has an idempotency key (job id, or `trace_id:tool_name:args-hash`).
2. **Plan/commit split** for high-stakes side effects — gate the commit behind HITL.
3. **Dead-letter** terminal failures (`no_path`, repeated errors) to a human-reviewed queue.
4. **Re-execute only after verifying** the previous attempt didn't already succeed.

The library can't enforce idempotency for you — it's a property of the surrounding system.

## A practical distributed setup

1. **Postgres `StateStore`** — pause state, memory, tasks/updates, bindings, events.
2. **Redis** (or any queue) for inbound jobs.
3. **Stateless worker pool** building planners from a factory.
4. **`A2AHttpTransport`** for outbound agent calls; `A2AService` for inbound.
5. **LLM tracing** (`PENGUIFLOW_LLM_TRACING=mlflow`) and planner-event metrics.
6. **OpenTelemetry** for cross-service traces (your request id ↔ `tool_context["trace_id"]`).

Pauses survive worker swaps. Memory persists. A2A bindings recover across restarts.

## Anti-patterns

- **`StateStore` only in-memory** in multi-worker production — defeats pause/resume, memory and bindings.
- **No `agent_url` on specialists** — no bindings recorded for their tasks.
- **Mixing tenants under one `session_id`** — memory and bindings merge.
- **No idempotency on external writes** — duplicates on retry and recovery.
