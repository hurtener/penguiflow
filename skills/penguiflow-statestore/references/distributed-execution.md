# Distributed execution & remote calls (persistence view)

## What it is / when to use it

A ReactPlanner agent runs in one process, but production deployments usually need:

- multiple worker processes (or machines) serving the same conversations,
- paused runs that resume on a different worker,
- agent-to-agent calls (A2A) with conversation continuity,
- durable task/update history for UIs.

All of that is persistence: the `StateStore` capabilities below. Use this page when moving from "local dev" to "multi-worker production".

## Non-goals / boundaries

- PenguiFlow does not ship a distributed scheduler. You provide queueing, worker management and service discovery ([[penguiflow-deployment]]).
- There is no single "official" backend; the library exposes protocols (`StateStore` and its optional capabilities, `RemoteTransport`).
- Distributed systems require idempotency and strict limits; the library helps but cannot guarantee correctness if you violate those contracts.

## Contract surface

### 1) `StateStore` (durability + audit)

At minimum, a `StateStore` must implement:

- `save_event(event: StoredEvent) -> None` (idempotent)
- `load_history(trace_id: str) -> Sequence[StoredEvent]`
- `save_remote_binding(binding: RemoteBinding) -> None`

Optional capabilities (duck-typed) enable:

- planner pause/resume across workers (`save_planner_state`, `load_planner_state`)
- memory state persistence (`save_memory_state`, `load_memory_state`)
- A2A router-to-specialist conversation continuity (`find_binding`, `list_bindings`, `mark_binding_terminal`)
- session tasks, updates and steering (`save_task`, `list_tasks`, `save_update`, `list_updates`, `save_steering`, `list_steering`)
- trajectories and planner events (`save_trajectory`, `get_trajectory`, `list_traces`, `save_planner_event`, `list_planner_events`)
- artifact integration

See `statestore-implementation-spec.md`.

### 2) `RemoteTransport` (remote agent calls)

Remote calls are modeled as (`penguiflow.remote`):

- `RemoteTransport.send(request) -> RemoteCallResult` (unary)
- `RemoteTransport.stream(request) -> AsyncIterator[RemoteStreamEvent]` (streaming)
- `RemoteTransport.cancel(agent_url=..., task_id=...)` (cancellation)
- optional `SupportsRemoteTasks` task operations (send/get/list/subscribe tasks, push configs)

Planners use transports through `A2AAgentToolset` / `A2ARouterToolset` tools, which persist and reuse `RemoteBinding`s.

Key operational point: remote cancellation requires a durable `task_id` and a transport that supports cancellation.

### 3) A2A bindings (`penguiflow_a2a`)

- server: `A2AService(planner, ...)` + `create_a2a_http_app(...)` / `install_a2a_http(...)` / gRPC binding
- client transport: `A2AHttpTransport` (implements `RemoteTransport` and `SupportsRemoteTasks`)
- planner/router client: `A2AAgentToolset` and `A2ARouterToolset`

Persistence touchpoints:
- **Server side** — with `A2AConfig(agent_url=...)` set, each task saves `RemoteBinding(trace_id=task_id, context_id, task_id, agent_url)` via `save_remote_binding` on the service's `state_store` (default: the planner's store). Paused tasks resume through the planner's `save_planner_state` / `load_planner_state`.
- **Client side** — the toolset saves bindings after each call and looks them up with `find_binding` to continue a remote conversation.

Install extras:

- server: `pip install "penguiflow-lite[a2a-server]"`
- client: `pip install "penguiflow-lite[a2a-client]"`
- gRPC bindings: `pip install "penguiflow-lite[a2a-grpc]"`

## Operational defaults (recommended)

- Pass the same durable store to the planner (`ReactPlanner(state_store=...)`), sessions (`SessionManager(state_store=...)`) and, if separate, `A2AService(state_store=...)`.
- Put `tenant`, `user_id` and `session_id` in `tool_context` on every run; bindings and memory are scoped by them.
- Time-bound every remote boundary: tool `timeout_s` / `A2AAgentToolset(default_timeout_s=...)`, planner `deadline_s`.
- For A2A router continuity, persist the additive `RemoteBinding` fields:
  - `router_session_id`, `remote_skill`, `tenant_id`, `user_id`, `last_remote_task_id`, `is_terminal`, `metadata`.
- Implement optional conversation binding lookup when an `A2AAgentToolset` manager/router should reuse a specialist conversation:
  - `find_binding(router_session_id, agent_url, remote_skill, tenant_id=None, user_id=None)`,
  - `list_bindings(router_session_id=...)`,
  - `mark_binding_terminal(trace_id, context_id, task_id)`.
- Assume at-least-once delivery in the surrounding platform:
  - make remote calls idempotent where possible,
  - avoid side effects on retry without idempotency keys.

## Failure modes & recovery

- **Remote tasks leak** (can't cancel): `task_id` bindings not persisted or the transport doesn't support cancel.
  - Fix: implement `save_remote_binding` and use a transport with cancellation; enforce deadlines so leaks are bounded.
- **A2A specialist loses conversation continuity**: the store only saves legacy remote bindings, does not implement `find_binding`, or is not visible to `A2AAgentToolset` through `tool_context["state_store"]` / planner `_state_store`.
  - Fix: add nullable conversation binding columns, index active bindings by `(router_session_id, agent_url, remote_skill, tenant_id, user_id)`, return the newest non-terminal scoped binding, and ensure router tool context includes `session_id`.
- **Pause resumes fail on another worker**: `save_planner_state` / `load_planner_state` missing, or records expire before the human answers.
  - Fix: implement both with a TTL that matches your HITL UX.
- **Cross-tenant leakage**: shared auth tokens or reused session ids across tenants.
  - Fix: per-tenant credentials; include `tenant_id` and `user_id` in binding lookup so a remote context is not reused across tenants/users.
- **Retry storms**: transient remote failures trigger retries across multiple workers.
  - Fix: cap retries, add backoff, and add circuit-breaker behavior at the boundary.

## Observability

Distributed execution needs two layers:

1. **Planner events** per worker (`PlannerEvent`): steps, tool calls, finish reasons, repairs — and, when persisted, `save_planner_event` / `list_planner_events`.
2. **Remote calls**: remote call latency, remote status and task ids, cancellation success/failure (`RemoteEventSink` on `A2AAgentToolset` receives `RemoteTaskEvent`s).

Recommended dashboards/alerts: [[penguiflow-observability]].

## Security / multi-tenancy notes

- Treat remote agent URLs as sensitive and validate/allowlist them.
- Do not log raw remote payloads by default; log ids + summaries + structured metadata.
- If you accept callback URLs (webhooks), validate them (SSRF protection; `is_safe_webhook_url`).

## Runnable examples

```bash
uv run python examples/a2a_grpc_server/main.py
```

Manager↔specialist and binding tests: `tests/a2a/test_a2a_planner_tools.py`, `tests/a2a/test_a2a_planner_service.py`.

## Troubleshooting checklist

- If a system "works locally" but fails distributed:
  - confirm `StateStore` durability and idempotency,
  - confirm queue/worker semantics (visibility timeout, retries),
  - confirm every run carries `session_id` / `tenant` in `tool_context`,
  - confirm the planner, sessions and A2A service share the same store.
