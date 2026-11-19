# Server Binding: `A2AService` and FastAPI

## Building an `A2AService`

The constructor takes the planner positionally and **all other arguments keyword-only**:

```python
from penguiflow_a2a import A2AConfig, A2AService

service = A2AService(
    planner,                               # ReactPlanner (positional)
    agent_card=card,                       # penguiflow_a2a.models.AgentCard
    config=A2AConfig(
        supported_versions=("0.3",),
        default_version=None,              # defaults to first supported version
        allow_v1_aliases=True,
        allow_tenant_prefix=True,
        default_tenant="default",
        agent_url=None,                    # canonical URL; enables RemoteBinding persistence
    ),
    # Optional:
    # store=InMemoryTaskStore(),            # custom TaskStore
    # state_store=...,                      # where RemoteBindings go (default: the planner's state store)
    # tool_context={...},                   # static tool_context merged into every run
    # tool_visibility=ToolVisibilityPolicy(...),  # applied to every A2A run
    # push_sender=HttpPushNotificationSender(),
    # extended_agent_card=fuller_card,
    # extended_agent_card_auth=lambda headers: <bool>,
)
```

The service:
- holds a reference to your `ReactPlanner` and drives one `planner.run()` (or `planner.resume()`) per A2A task,
- serializes the agent card,
- owns a `TaskStore` (in-memory by default) for task lifecycle,
- exposes `start()` / `stop()` for lifespan integration. `start()` bridges the planner's event callback (chaining any callback you set) so answer chunks can be streamed; `stop()` cancels running tasks and restores the callback.

`A2AConfig` knobs:
- `supported_versions` — A2A protocol versions this service speaks. Must be non-empty.
- `default_version` — falls back to `supported_versions[0]` when unset.
- `allow_v1_aliases` — when True, also serves `/v1/...` aliases of all routes.
- `allow_tenant_prefix` — when True, serves `/{tenant}/...` for every route.
- `default_tenant` — used when no tenant prefix is present.
- `agent_url` — canonical URL of this agent; when set, each task saves a `RemoteBinding(trace_id=task_id, context_id, task_id, agent_url)` to the state store.

## How a task maps to the planner

**Input.** The planner `query` is the message's text parts joined with newlines; with no text, it is the JSON of the data parts (a message with neither is rejected). The per-run `tool_context` is:

| Key | Value |
|---|---|
| `delivery_channel` | `"a2a"` (the static `tool_context` may override it) |
| `tenant` | tenant prefix from the URL, else `A2AConfig.default_tenant` |
| `session_id` | the A2A `contextId` |
| `a2a_task_id`, `a2a_context_id`, `a2a_message_id`, `a2a_role` | identifiers of the request |
| `a2a` | `{"message", "metadata", "configuration"}` envelope of the request |
| `a2a_requested_extensions` | extension URIs the caller requested |
| `a2a_accepted_output_modes` | when the caller sent them |

Static `tool_context` entries are merged in first, so request-derived keys always win. Because `session_id` is the `contextId`, the planner's session dispatch serializes runs within one conversation (and keeps its short-term memory) while different conversations run in parallel.

**Outcomes.**

| Planner result | Task state | Output |
|---|---|---|
| `PlannerFinish(reason="answer_complete")` | `completed` | artifact `output-0`: text part with the answer (`raw_answer`/`answer`/...), full payload under `artifact.metadata["planner_payload"]`; payloads without answer text become a data part |
| `PlannerFinish(reason="no_path" \| "budget_exhausted")` | `failed` | status message from `metadata["error"]`; payload (if any) still stored as `output-0` |
| `PlannerPause` | `input-required` | status message from the pause payload's `prompt`/`message`/`question`; metadata `pause_reason`, `pause_payload` |
| `SteeringCancelled` / cancelled run | `cancelled` | — |
| any other exception | `failed` | status message with the error text |

**Resume.** A `message/send` (or `message/stream`) carrying the `taskId` of an `input-required` task resumes the planner with `planner.resume(token, user_input=<message text>)`. Sending to a task that is still running is rejected.

**Cancel.** `tasks/cancel` pushes a `SteeringEventType.CANCEL` into the run's `SteeringInbox` and cancels the run; the task ends `cancelled`. A paused (`input-required`) task can also be cancelled.

**Streaming.** On `message/stream`, final-answer chunks (`llm_stream_chunk` planner events with `extra["channel"] == "answer"`) are forwarded as `output-0` artifact updates: `append=True`, or `append=False` when the planner restarts its answer (new `action_seq`). When the run finishes, the final artifact replaces the streamed one (`append=False`, `last_chunk=True`). The planner only emits these chunks with `stream_final_response=True`. `message/send` never forwards chunks.

## Agent card schema

`A2AService` requires the full A2A-spec `AgentCard` from `penguiflow_a2a.models` (alongside `AgentCapabilities`, `AgentInterface`, `AgentSkill`):

```python
from penguiflow_a2a.models import (
    AgentCapabilities, AgentCard, AgentInterface, AgentSkill,
)

card = AgentCard(
    protocol_versions=["0.3"],
    name="My Agent",
    description="What I do",
    version="1.0.0",
    supported_interfaces=[AgentInterface(url="https://agent.example.com", protocol_binding="HTTP")],
    capabilities=AgentCapabilities(
        streaming=True,
        push_notifications=True,
        extended_agent_card=False,
        state_transition_history=False,
    ),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[
        AgentSkill(
            id="answer",
            name="Answer",
            description="Answer a question",
            tags=["qa"],
        ),
    ],
)
```

Required fields (`Field(min_length=1)` in the model): `protocol_versions`, `supported_interfaces`, `default_input_modes`, `default_output_modes`, `skills`. `AgentSkill.tags` must also be non-empty. `to_camel` alias generation means JSON fields appear as `protocolVersions`, `defaultInputModes`, etc.

The card is published at `GET /.well-known/agent-card.json` with media type `application/a2a+json`. An optional `GET /extendedAgentCard` returns a fuller card behind an authorization check the host app supplies via `extended_agent_card_auth=Callable[[Mapping[str,str]], bool]`.

## Three binding modes

### Mode A: `install_a2a_http(app, service)`

Use when the host owns the FastAPI app. Adds all A2A routes plus the agent card.

```python
from fastapi import FastAPI
from penguiflow_a2a import install_a2a_http

app = FastAPI()
install_a2a_http(app, service, include_jsonrpc=True, include_agent_card=True)
```

The agent card mounts at `app.add_api_route("/.well-known/agent-card.json", ...)` at the **app** level. The other routes mount via an internal router that handles `service.start()` / `service.stop()` in its lifespan.

Best when:
- You already have a FastAPI app with other routes.
- You want A2A routes alongside health, metrics, business endpoints.

### Mode B: `create_a2a_http_router(service)`

Use when you want explicit composition. Returns an `APIRouter` you mount via `app.include_router(router)`.

```python
from penguiflow_a2a import create_a2a_http_router

router = create_a2a_http_router(
    service,
    include_agent_card=True,
    include_jsonrpc=True,
    _attach_lifespan=True,
)
app.include_router(router)
```

Caveats:
- `_attach_lifespan=True` (default) makes the router responsible for `service.start()` / `service.stop()`. If you mount via `include_router(...)` and want to control lifespan yourself, pass `_attach_lifespan=False`.
- **Practical rule**: keep `/.well-known/agent-card.json` at the **app** level, not inside a reusable router (otherwise multi-prefix mounts duplicate it).

Best when:
- You're using FastAPI's `include_router(prefix=..., ...)` composition.
- You want to mount A2A under a custom prefix.
- You need to disable JSON-RPC or the agent card for a specific deployment.

### Mode C: `create_a2a_http_app(service)`

Convenience wrapper. Use when A2A is the only thing this process serves.

```python
from penguiflow_a2a import create_a2a_http_app
app = create_a2a_http_app(service, include_docs=True)
```

Creates a `FastAPI(title=card.name, ...)` with `/docs` and `/openapi.json` and calls `install_a2a_http` for you. Best for single-purpose specialist deployments.

## Route map (HTTP + JSON-RPC)

When `allow_tenant_prefix=True` and `allow_v1_aliases=True`, every operation route is mounted at:
- `/<route>`
- `/v1/<route>`
- `/{tenant}/<route>`
- `/{tenant}/v1/<route>`

| Method | Path | Purpose |
|---|---|---|
| GET | `/.well-known/agent-card.json` | Public agent card (always at app level) |
| GET | `/extendedAgentCard` | Authorized fuller card |
| POST | `/rpc` | JSON-RPC 2.0 endpoint (all operations) |
| POST | `/message:send` | Single-shot REST send (note colon) |
| POST | `/message:stream` | SSE stream send |
| GET / POST | `/tasks/{task_id}:subscribe` | Re-subscribe to a streaming task |
| POST | `/tasks/{task_id}:cancel` | Cancel a task |
| GET | `/tasks/{task_id}` | Get task snapshot |
| GET | `/tasks` | List tasks (paginated) |
| POST / GET / DELETE | `/tasks/{task_id}/pushNotificationConfigs[...]` | Manage push-notification configs per task |

The JSON-RPC endpoint enforces:
- `jsonrpc == "2.0"` (else `-32600`).
- Non-null `id` of any type (else `-32600`).
- String `method` (else `-32600`).
- Valid JSON body (else `-32700`).

Errors come back as A2A problem-detail JSON for REST and JSON-RPC error objects for `/rpc`.

## Request and response shapes

Requests and responses use the A2A models in `penguiflow_a2a.models` (`SendMessageRequest`, `Message`, `Part`, `Task`, `TaskStatus`, `Artifact`, `StreamResponse`), serialized with camelCase aliases (`messageId`, `contextId`, `taskId`, ...). Incoming messages must have `role: "user"`.

## Validation error handling

The binding's `_wrap_http` converts:
- `A2AError` → A2A problem-detail JSON.
- Pydantic `ValidationError` → A2A validation problem-detail JSON.

**Important**: don't install a host-app `@app.exception_handler(RequestValidationError)` just for A2A — the binding already handles its own validation. App-global handlers can intercept and turn proper A2A errors into FastAPI 422s.

## Lifespan and start/stop

`A2AService.start()` installs the planner event bridge. `A2AService.stop()` cancels running tasks and restores the planner's original event callback.

- Mode A (`install_a2a_http`) wires lifespan inside the internal router.
- Mode B (`create_a2a_http_router(_attach_lifespan=True)`) wires lifespan inside the returned router.
- Mode B (`_attach_lifespan=False`) requires you to call `await service.start()` and `await service.stop()` in your own lifespan.
- Mode C (`create_a2a_http_app`) inherits Mode A's lifespan.

## Hard rules
- Card at app level, not inside a reusable router.
- Don't add app-global validation handlers for A2A routes.
- Treat agent-card endpoints as public; gate `/extendedAgentCard` with `is_extended_agent_card_authorized`.
- Use `application/a2a+json` for card responses; the binding sets it.
- Resolve tenants from the URL prefix or your own auth — `default_tenant` is a fallback, not authz.
- Hide UI-only tools from A2A callers with `tool_visibility=`; downstream agents can't see rendered components.
