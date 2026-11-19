---
name: penguiflow-a2a-integration
description: Expose a ReactPlanner agent as an A2A-spec service or call remote A2A agents from a planner — wrap the planner in `A2AService(planner, ...)`, mount it on a FastAPI app via `install_a2a_http`/`create_a2a_http_router`/`create_a2a_http_app`, serve the agent card at `/.well-known/agent-card.json`, handle JSON-RPC + REST + SSE + push-notification transports, optionally enable the gRPC binding, build manager↔specialist topologies with `A2AAgentToolset` and an `AgentRegistry`, map planner pauses to `input-required` and resume them, persist conversation continuity via `RemoteBinding`/`StateStore`, and choose blocking/stream/task execution modes. Use when a user says "expose as A2A", "agent-to-agent", "call a remote agent", "agent card", "manager/specialist", "JSON-RPC", "SSE for agents", "push notifications for agents", or names `A2AService`, `RemoteBinding`, `A2AAgentToolset`.
---

# PenguiFlow A2A Integration

## When to use
- Exposing an existing `ReactPlanner` agent over the A2A protocol (HTTP + JSON-RPC, SSE, gRPC, push).
- Calling a remote A2A agent as a planner tool (specialist).
- Building a manager agent that routes to specialist A2A agents.
- Needing conversation continuity across calls to the same remote skill.

## When NOT to use
- Custom (non-A2A) remote transports → implement the `penguiflow.remote.RemoteTransport` protocol; see [[penguiflow-deployment]].
- Wiring MCP servers as planner tools → use [[penguiflow-mcp-integration]].
- Configuring the host planner itself → use [[penguiflow-reactplanner-config]].
- Persisting bindings in production → use [[penguiflow-statestore]] (this skill names the methods but the durable backend lives there).

## Hard boundaries
This skill is the A2A wire contract: agent cards, transports, conversation bindings, planner-side toolsets. Persistence lives in statestore. Generic remote execution lives in deployment. Planner config lives in reactplanner-config. Don't duplicate.

## Workflow

### 1) Decide your role: server, client, or both
- **Server (specialist)**: another agent will call you. Wrap your `ReactPlanner` in an `A2AService`, mount it on FastAPI (or gRPC), publish your agent card.
- **Client (manager)**: you will call other A2A agents from your planner. Use `A2AAgentToolset` to wrap remote skills as planner tools.
- **Hybrid**: both — common in router-and-specialist topologies.

See `references/server-binding.md` for the server path, `references/client-and-toolset.md` for the client path.

### 2) Server path — wrap the planner in an `A2AService`
```python
from penguiflow_a2a import A2AConfig, A2AService

service = A2AService(
    planner,                      # your ReactPlanner
    agent_card=agent_card,        # penguiflow_a2a.models.AgentCard
    config=A2AConfig(             # supported versions, tenant prefix, agent URL
        supported_versions=("0.3",),
        allow_v1_aliases=True,
        allow_tenant_prefix=True,
        default_tenant="default",
    ),
    tool_context={"db": db},       # optional static tool_context merged into every run
    tool_visibility=a2a_policy,    # optional ToolVisibilityPolicy (e.g. hide UI-only tools)
)
```
Each A2A task drives one planner run:
- The query is the joined text parts of the message (JSON of the data parts if there is no text).
- `tool_context` gets `tenant`, `session_id` (= A2A `contextId`), `a2a_task_id`, `a2a_context_id`, `a2a` (the request envelope) and `delivery_channel="a2a"`. Using `contextId` as `session_id` serializes runs within one conversation and runs different conversations in parallel.
- `PlannerFinish(answer_complete)` → `completed`, answer text as artifact `output-0` (full payload in `artifact.metadata["planner_payload"]`). `no_path` / `budget_exhausted` / exceptions → `failed`.
- `PlannerPause` → `input-required` with the pause `prompt` as the status message; the next `message/send` to that `taskId` resumes the planner with the message text as `user_input`.
- `tasks/cancel` cancels the run through its `SteeringInbox` → `cancelled`.

Full constructor, outcome mapping and agent-card schema in `references/server-binding.md`.

### 3) Mount it on FastAPI
Three options, ordered by integration depth:
- `install_a2a_http(app, service)` — host owns the FastAPI app, A2A adds routes (recommended for existing apps).
- `create_a2a_http_router(service)` — host composes via `app.include_router(...)` (most explicit; lifespan-aware).
- `create_a2a_http_app(service)` — convenience standalone FastAPI app (single-purpose deployment).

All three publish `GET /.well-known/agent-card.json` and `GET /extendedAgentCard`, plus JSON-RPC at `POST /rpc` and REST routes for `message/send`, `message/stream`, `tasks/cancel`, etc. The router supports tenant prefix (`/{tenant}/...`) and v1 aliases (`/v1/...`) when enabled in `A2AConfig`. See `references/server-binding.md` for the full route list.

### 4) (Optional) Add SSE / push / gRPC
- **SSE**: built-in for `message/stream`; the router uses FastAPI `StreamingResponse`.
- **Push notifications**: `HttpPushNotificationSender` with safe-URL gating (`is_safe_webhook_url`) blocks SSRF to RFC1918 / loopback.
- **gRPC**: `from penguiflow_a2a.bindings.grpc import add_a2a_grpc_service` — adds the A2A protobuf service to a `grpc.aio.server`. Requires `penguiflow-lite[a2a-grpc]`.

Worked gRPC example: `examples/a2a_grpc_server/main.py`. Push/SSE deep dive: `references/transports-push-sse-grpc.md`.

### 5) Client path — wrap a remote agent as planner tools
```python
from penguiflow_a2a import A2AAgentToolset, A2AHttpTransport

toolset = A2AAgentToolset(
    agent_url="https://specialist.example.com",
    transport=A2AHttpTransport(...),
    agent_card=card,
    default_timeout_s=30.0,
)
node_spec = toolset.tool(
    name="ask_specialist",
    skill="answer",
    args_model=AskArgs,
    out_model=AnswerOut,
    desc="Delegate to the specialist agent",
    streaming=False,
    execution_mode="auto",   # auto|blocking|stream|task
)
```
Add `node_spec` to your planner's tool catalog. A plain-text remote answer is wrapped into `out_model` when it has exactly one field. Full pattern + `execution_mode` semantics + cancel-on-cancel + chunk channels in `references/client-and-toolset.md`.

### 6) Conversation continuity (manager↔specialist)
The toolset auto-persists a `RemoteBinding` per `(agent_url, skill, tenant, user)` so the next call to the same skill resumes the same remote `task_id` / `context_id`. Requires a `StateStore` exposing `save_remote_binding`, `find_binding`, `mark_binding_terminal`. Behavior is best-effort if no store is present (the planner falls back to fresh calls). See [[penguiflow-statestore]] for the durable backend.

### 7) Router topology — route across many specialists
`AgentRegistry` + `A2ARouterToolset` + `RouterPolicy` let a manager pick the right specialist by skill/tenant/trust/latency tier. Load registries from config with `load_agent_registry_config(path)` or `AgentRegistry.from_config(mapping)`. Score with `AgentRouteRequest`. See `references/router-and-registry.md`.

## Troubleshooting (fast checks)
- **`/.well-known/agent-card.json` 404** — you used `create_a2a_http_router` without keeping `include_agent_card=True` at the app-level mount; install the card at app level, not inside a reusable router.
- **A2A routes return 422 instead of A2A problem details** — host-app `RequestValidationError` handler is intercepting; the binding maps errors inside its own `_wrap_http` helper, so don't add app-global handlers just for A2A routes.
- **Specialist loses context across calls** — no `StateStore` configured or `find_binding` returns nothing; verify the toolset can see a store via `tool_context["state_store"]` or the planner's `_state_store` attribute.
- **Push notification rejected** — webhook URL points to loopback/RFC1918; `is_safe_webhook_url` blocks SSRF by design. Use a public URL.
- **`A2AService` start/stop ordering** — when using `install_a2a_http`/`create_a2a_http_router` with `_attach_lifespan=True` (default), the router owns startup/shutdown; if you mount it via `app.include_router(...)` and want explicit lifespan control, pass `_attach_lifespan=False` and call `service.start()`/`service.stop()` yourself.
- **JSON-RPC `-32600 Request payload validation error`** — missing `id`, non-string `method`, or `jsonrpc != "2.0"`. The router enforces JSON-RPC 2.0 strictly.
- **gRPC import errors** — `pip install "penguiflow-lite[a2a-grpc]"`; the optional import in `penguiflow_a2a/__init__.py` is wrapped in `try/except RuntimeError`.
- **Tenant prefix routes 404** — `A2AConfig.allow_tenant_prefix=False`; flip it to `True` and remount.
- **`message/stream` sends no partial chunks** — the planner only streams its final answer with `stream_final_response=True`; otherwise the stream carries status updates and the final artifact only.
- **Task stuck in `input-required`** — the planner paused; send another `message/send` with the same `taskId` (and `contextId`) to resume it, or `tasks/cancel` it.

## Worked examples
- `examples/a2a_grpc_server/main.py` — a `ReactPlanner` served over gRPC, plus a client call.

## References (load only as needed)
- `references/server-binding.md` — `A2AService`, `A2AConfig`, `AgentCard`, planner outcome mapping, REST + JSON-RPC route map, three binding modes.
- `references/client-and-toolset.md` — `A2AAgentToolset`, `A2AHttpTransport`, `RemoteCallRequest`, execution modes, conversation bindings.
- `references/transports-push-sse-grpc.md` — SSE encoding, `HttpPushNotificationSender` + SSRF guards, gRPC binding setup.
- `references/router-and-registry.md` — `AgentRegistry`, `RouterPolicy`, `A2ARouterToolset`, `AgentRouteRequest` scoring.
