# Deployment

## What it is / when to use it

This section explains how to run PenguiFlow agents as a **long-lived production system**:

- a `ReactPlanner` behind your own API, a job worker, or an A2A endpoint,
- with durable state (pause/resume, memory, sessions) when you need it,
- observable through planner events, LLM telemetry and structured logs.

## Non-goals / boundaries

- PenguiFlow does not ship a full "platform" (no built-in job queue, metrics exporter, or authz layer).
- It does not prescribe a hosting model; the same planner runs in a web app, a worker, or behind A2A.

## Contract surface (choices you must make)

### 1) Serving surface

- **Your own API**: call `await planner.run(query, tool_context=...)` per request.
- **Sessions**: `SessionManager` / `StreamingSession` for multi-turn chats with background tasks and live updates (see **[Background tasks](../planner/background-tasks.md)**).
- **A2A**: `penguiflow_a2a.A2AService(planner, ...)` mounted with `create_a2a_http_app` / `install_a2a_http`, or the gRPC binding. Other agents call it; your planner can call others with `A2AAgentToolset`.

### 2) Planner budgets

Set `max_iters`, token/cost budgets and deadlines explicitly; see **[Configuration](../planner/configuration.md)**.

### 3) Tool reliability and concurrency

Configure timeouts, retries and `max_concurrency` per external tool source in `ToolNode`; see **[Tools concurrency](../tools/concurrency-guide.md)** and **[Tools configuration](../tools/configuration.md)**.

### 4) Durability and audit (optional)

A **StateStore** persists pause records (resume across processes), short-term memory, session/task state, planner events and A2A remote bindings. See **[State store](../tools/statestore.md)**.

### 5) Tool integrations

- ToolNode for MCP/UTCP/HTTP,
- OAuth / HITL via pause/resume (see **[OAuth & HITL](../tools/oauth-hitl.md)**).

## Operational defaults (recommended)

- Always pass `tenant` and `session_id` in `tool_context`.
- Use a durable `StateStore` in multi-process deployments so pause tokens and memory survive restarts.
- Turn on structured logs: `configure_logging(structured=True)`, and attach an `event_callback`.

## Failure modes & recovery

- **Runs never finish**: budgets are too loose or a tool hangs; set tool timeouts and planner budgets.
- **Resume fails after a restart / on another replica**: pause records live in memory; configure a shared `StateStore`.
- **Duplicate side effects**: tools retried or re-run after resume; make them idempotent and mark `side_effects`.

## Observability

See **[Observability overview](../observability/overview.md)**: planner events, LLM telemetry and task telemetry.

## Security / multi-tenancy notes

- Keep secrets in `tool_context` (never `llm_context`) and out of logs.
- Scope state store keys and memory by tenant/user/session.
- Authenticate A2A endpoints at your app layer; the A2A binding does not ship authn/authz.

## Runnable examples

```bash
uv run python examples/react_minimal/main.py
uv run python examples/a2a_grpc_server/main.py   # requires the a2a-grpc extra
```

## Troubleshooting checklist

- Need production hardening: see **[Production deployment](production-deployment.md)**.
- Need a job-worker pattern: see **[Worker integration](worker-integration.md)**.

## Next steps

- **[Production deployment](production-deployment.md)**
- **[Worker integration](worker-integration.md)**
