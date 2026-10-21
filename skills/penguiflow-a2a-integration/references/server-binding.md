# Server Binding: `A2AService` and FastAPI

## Building an `A2AService`

The constructor takes `flow` positionally and **all other arguments keyword-only**:

```python
from penguiflow_a2a import A2AConfig, A2AService, PayloadMode

service = A2AService(
    flow,                                  # PenguiFlow runtime (positional)
    agent_card=card,                       # penguiflow_a2a.models.AgentCard
    config=A2AConfig(
        supported_versions=("0.3",),
        default_version=None,              # defaults to first supported version
        allow_v1_aliases=True,
        allow_tenant_prefix=True,
        default_tenant="default",
        payload_mode=PayloadMode.AUTO,      # AUTO | ENVELOPE
        agent_url=None,                     # optional canonical URL for the card
    ),
    # Optional:
    # store=InMemoryTaskStore(),            # custom TaskStore
    # target=...,                            # specific ingress node(s)
    # registry=...,                          # ModelRegistry for validated nodes
    # default_headers={...},
    # push_sender=HttpPushNotificationSender(),
    # extended_agent_card=fuller_card,
    # extended_agent_card_auth=lambda headers: <bool>,
)
```

The service:
- holds a reference to your `PenguiFlow` instance,
- serializes the agent card,
- owns a `TaskStore` (in-memory by default) for task lifecycle,
- exposes `start()` / `stop()` for lifespan integration.

`A2AConfig` knobs:
- `supported_versions` — A2A protocol versions this service speaks. Must be non-empty.
- `default_version` — falls back to `supported_versions[0]` when unset.
- `allow_v1_aliases` — when True, also serves `/v1/...` aliases of all operation routes (default tenant).
- `allow_tenant_prefix` — when True, serves `/{tenant}/...` for every operation route (and `/{tenant}/v1/...` with v1 aliases on).
- `default_tenant` — used when no tenant prefix is present (root and `/v1/...`).
- `payload_mode` — `AUTO` infers from request shape; `ENVELOPE` forces `Message` envelopes.
- `agent_url` — explicit canonical URL for the card (otherwise derived).

These aliases are mounted by `install_a2a_http` / `create_a2a_http_app`; `create_a2a_http_router` alone mounts only the root routes.

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

(The lightweight `A2AAgentCard` / `A2ASkill` types in `penguiflow_a2a.server` are a legacy surface used by the older `A2AServerAdapter`. For modern HTTP/gRPC bindings via `A2AService`, use the `AgentCard` shown above.)

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

When `allow_tenant_prefix=True` and `allow_v1_aliases=True` (the defaults), `install_a2a_http` mounts every operation route at, in this order:
- `/<route>` (default tenant)
- `/v1/<route>` (default tenant)
- `/{tenant}/v1/<route>`
- `/{tenant}/<route>`

Fixed prefixes come first so `/v1/...` is the version alias, not tenant `v1`; a tenant literally named `v1` is reachable only via `/v1/v1/...`. With `allow_v1_aliases=False`, `/v1/...` is an ordinary path for tenant `v1`. The agent card is served only at the root.

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

`A2AMessagePayload`:
- `payload: Any`
- `headers: Mapping[str, Any]`
- `meta: dict[str, Any]`
- `trace_id: str | None` (alias `traceId`)
- `context_id: str | None` (alias `contextId`)
- `task_id: str | None` (alias `taskId`)
- `deadline_s: float | None` (alias `deadlineSeconds`)

`A2ATaskCancelRequest`:
- `task_id: str` (alias `taskId`)

## Validation error handling

The binding's `_wrap_http` converts:
- `A2AError` → A2A problem-detail JSON.
- Pydantic `ValidationError` → A2A validation problem-detail JSON.

**Important**: don't install a host-app `@app.exception_handler(RequestValidationError)` just for A2A — the binding already handles its own validation. App-global handlers can intercept and turn proper A2A errors into FastAPI 422s.

## Lifespan and start/stop

`A2AService.start()` initializes the task broker and any internal resources. `A2AService.stop()` drains and releases them.

- Mode A (`install_a2a_http`) wires lifespan inside the internal router.
- Mode B (`create_a2a_http_router(_attach_lifespan=True)`) wires lifespan inside the returned router.
- Mode B (`_attach_lifespan=False`) requires you to call `await service.start()` and `await service.stop()` in your own lifespan.
- Mode C (`create_a2a_http_app`) inherits Mode A's lifespan.

## Hard rules
- Card at app level, not inside a reusable router.
- Don't add app-global validation handlers for A2A routes.
- Treat agent-card endpoints as public; gate `/extendedAgentCard` with `is_extended_agent_card_authorized`.
- Use `application/a2a+json` for card responses; the binding sets it.
- Set `Headers.tenant` on incoming envelopes — `default_tenant` is a fallback, not authz.
