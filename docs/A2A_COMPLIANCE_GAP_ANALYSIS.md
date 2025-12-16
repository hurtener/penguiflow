# A2A Protocol Compliance Gap Analysis & Implementation Plan (Spec-Accurate)

> **Document role**: This is the canonical, spec-grounded plan for bringing PenguiFlow to **A2A Protocol** compliance and keeping it current. It is intended to be used as the shared implementation reference by engineering, docs, and QA.
>
> **Document version**: 2.1 (grounded to `docs/spec/a2a_specification.md`)
>
> **Last updated**: 2025-12-16
>
> **Target spec**: A2A Protocol **v0.3.0** (latest released), per `docs/spec/a2a_specification.md` (“A2A Protocol Specification (DRAFT v1.0)” document with latest released version note).
>
> **Normative source of truth** (per spec): `a2a.proto`. This repo vendors the proto at `docs/spec/a2a.proto` (and a render-helper copy at `docs/specification/grpc/a2a.proto`). This plan is grounded to that proto plus the narrative spec in `docs/spec/a2a_specification.md`.

---

## Executive Summary

PenguiFlow currently provides a **legacy, custom REST-ish adapter** (`penguiflow_a2a/server.py`) that is *A2A-inspired* but **not wire-compatible** with the current A2A spec’s canonical data model, endpoints/methods, error model, version negotiation, or Agent Card structure.

To become **100% A2A-compatible and up to version**, we must:

1. Implement the **canonical A2A data model** (Task/Message/Part/Artifact/events) with correct JSON conventions.
2. Implement all **core A2A operations** with the correct **HTTP+JSON/REST binding** (recommended first) and streaming semantics (SSE).
3. Provide correct **Agent Card discovery** at `/.well-known/agent-card.json` using the spec’s AgentCard schema (including `supportedInterfaces`, `capabilities`, `securitySchemes`, etc.).
4. Implement **service parameter** handling (`A2A-Version`, `A2A-Extensions`) and **VersionNotSupportedError** behavior.
5. Implement **error mapping** via RFC 9457 Problem Details for HTTP and the defined code mappings for JSON-RPC/gRPC (even if those bindings are phased in later).
6. Add a **Task store** (in-memory default + interface for durable stores) to support `GetTask`, `ListTasks`, `SubscribeToTask`, and push notification configs.

This is a substantial refactor: the current adapter is effectively a different protocol surface.

---

## Table of Contents

1. [Scope & Compliance Definition](#scope--compliance-definition)
2. [Current Implementation (What We Have)](#current-implementation-what-we-have)
3. [Target Spec (What We Must Implement)](#target-spec-what-we-must-implement)
4. [Spec Ambiguities & Project Decisions](#spec-ambiguities--project-decisions)
5. [Gap Analysis (Spec vs PenguiFlow Today)](#gap-analysis-spec-vs-penguiflow-today)
6. [Target Architecture](#target-architecture)
7. [Implementation Plan (Phased Roadmap)](#implementation-plan-phased-roadmap)
8. [Acceptance Criteria & Compliance Checklist](#acceptance-criteria--compliance-checklist)
9. [Testing Strategy](#testing-strategy)
10. [Migration & Backward Compatibility](#migration--backward-compatibility)
11. [Risk Assessment](#risk-assessment)

---

## Scope & Compliance Definition

### What “100% compatible” means for PenguiFlow

To claim “A2A compatible” for a given binding, PenguiFlow must implement:

1. **Canonical semantics** of the abstract operations (Send Message, Stream Message, Get Task, List Tasks, Cancel Task, Subscribe to Task, Push Notification Config CRUD, Agent Card, Extended Agent Card).
2. **Binding-accurate** endpoints/method names and payload shapes for the chosen binding(s).
3. **Spec-defined error behavior and mappings**, including capability validation rules and version negotiation.
4. **Canonical JSON conventions** (camelCase field names, enum string forms, timestamps).

### Binding strategy

The spec defines three “standard” bindings:

- **HTTP+JSON/REST** (`/v1/message:send`, `/v1/tasks/{id}`, etc.) with SSE for streaming.
- **JSON-RPC 2.0 over HTTP** with SSE for streaming.
- **gRPC** based on the proto.

**Project goal**: Implement **HTTP+JSON/REST binding first** (closest to the existing FastAPI adapter), then add JSON-RPC and gRPC bindings on top of the same core operation layer to satisfy “functional equivalence” if/when we advertise multiple bindings.

### Capability scope

Some features are optional and gated by AgentCard capabilities:

- Streaming: gated by `AgentCard.capabilities.streaming`
- Push notifications: gated by `AgentCard.capabilities.pushNotifications`
- Extended Agent Card: gated by `AgentCard.supportsExtendedAgentCard`

**Compliance rule**: If we advertise a capability as unsupported, we must return the correct spec error when the corresponding operation is attempted.

**Project goal**:
- Streaming: **supported** (we already stream in PenguiFlow; we must change the wire format).
- Push notifications: **supported** (required for “full-feature” A2A agents; can be phased but must be implemented before advertising `pushNotifications: true`).
- Extended Agent Card: **optional** (we can advertise `supportsExtendedAgentCard: false` until implemented).

---

## Current Implementation (What We Have)

### Server surface (legacy adapter)

`penguiflow_a2a/server.py` exposes:

| Endpoint | Method | Current purpose |
|---|---:|---|
| `/agent` | GET | Returns a legacy “Agent Card”-like object |
| `/message/send` | POST | Unary execution; returns custom `{status, taskId, output...}` |
| `/message/stream` | POST | SSE streaming with custom events `status/chunk/artifact/done` |
| `/tasks/cancel` | POST | Cancels a running trace by legacy `taskId` |

This is **not** the A2A HTTP+JSON binding.

### Current “A2A” models (legacy)

The current adapter uses:

- `A2AAgentCard` (legacy fields like `schema_version`, `capabilities: list[str]`)
- `A2AMessagePayload` (legacy `{payload, headers, meta, traceId, contextId, taskId}`)
- `A2ATaskCancelRequest` (legacy `{taskId}`)

These do not match the canonical A2A Task/Message/Part model.

### Current client story

PenguiFlow provides a generic remote abstraction:

- `RemoteTransport` protocol (`penguiflow/remote.py`) and `RemoteNode(...)`

…but **does not ship an A2A transport**. This document focuses on **server compliance** first; client transport can be implemented as a separate plan once the server side is correct.

---

## Target Spec (What We Must Implement)

This section summarizes the required shape of the current A2A spec for implementers.

### Layering model (important for architecture)

1. **Canonical data model** (Task/Message/AgentCard/Parts/Artifacts/events)
2. **Abstract operations** (Send Message, Stream Message, Get Task, etc.)
3. **Protocol bindings** (HTTP+JSON, JSON-RPC, gRPC)

**Architecture consequence**: implement a binding-independent “A2A core” first, then thin binding adapters.

### Canonical wire conventions (must follow)

These requirements apply to all JSON-based bindings (HTTP+JSON and JSON-RPC over HTTP):

#### Media types and content negotiation

The spec contains both:
- an IANA registration for `application/a2a+json` (intended for HTTP+JSON/REST), and
- binding text that states “Content-Type: application/json” for HTTP+JSON/REST.

**Implementation requirement (pragmatic + interoperable):**
- Servers MUST accept both `application/a2a+json` and `application/json` request bodies.
- Servers SHOULD respond with `application/a2a+json` for A2A success responses when possible.
- For errors, servers MUST respond with `application/problem+json` (RFC 9457).
- Streaming endpoints respond with `text/event-stream`.

#### JSON field naming

- All JSON fields MUST use **camelCase** (e.g., `contextId`, `defaultOutputModes`).

#### Enum JSON values

- Enum values MUST be serialized as their string names using **lower kebab-case**, after removing type prefixes.
  - Example: `TASK_STATE_INPUT_REQUIRED` → `"input-required"`
  - Example: `ROLE_USER` → `"user"`

#### Timestamps

- All timestamps MUST be ISO 8601 UTC strings with a `Z` suffix (millisecond precision recommended):
  - Example: `"2025-10-28T10:30:00.000Z"`

#### Oneof/union constraints (critical)

- `Part` MUST contain **exactly one** of: `text`, `file`, `data`.
- `FilePart` MUST contain **exactly one** of: `fileWithUri`, `fileWithBytes`.
- `StreamResponse` MUST contain **exactly one** of: `task`, `message`, `statusUpdate`, `artifactUpdate`.
- `SecurityScheme` MUST contain **exactly one** scheme variant (apiKey/httpAuth/oauth2/openIdConnect/mtls).

#### History length semantics (shared across operations)

- Unset: server default (implementation-defined)
- `0`: no history; `history` SHOULD be omitted
- `> 0`: return up to N most recent messages

#### Extensions (negotiation + required enforcement)

- Agents declare supported extensions in `AgentCard.capabilities.extensions[]` (each entry has `uri`, `description`, `required`, `params`).
- Clients opt in via the `A2A-Extensions` service parameter header (comma-separated URIs).
- If the agent declares an extension as `required: true` and the client does **not** declare support for it in the request, the server MUST return `ExtensionSupportRequiredError`.
- Unrecognized extensions in requests SHOULD be ignored unless required by the Agent Card.

#### Forward compatibility

- Implementations SHOULD ignore unrecognized fields (both for objects and for enum values where possible), to remain forward compatible.

#### Idempotency (operational)

- Get operations are naturally idempotent.
- Cancel is idempotent.
- SendMessage MAY be treated as idempotent by servers using `messageId` to detect duplicates.

---

### Canonical data model reference (spec fields)

This is the binding-agnostic shape that all bindings MUST be equivalent to.

#### Task

| Field | Required | Notes |
|---|---:|---|
| `id` | Yes | Server-generated unique id |
| `contextId` | Yes | Server-generated for new conversations; must be preserved thereafter |
| `status` | Yes | `TaskStatus` |
| `artifacts` | No | Omit if none; in ListTasks omit entirely unless `includeArtifacts=true` |
| `history` | No | Controlled by `historyLength` semantics |
| `metadata` | No | Free-form key/value |

#### TaskStatus

| Field | Required | Notes |
|---|---:|---|
| `state` | Yes | `TaskState` (kebab-case string) |
| `message` | No | Optional `Message` (often agent role) |
| `timestamp` | No | ISO 8601 UTC string |

#### TaskState (JSON values)

`unspecified`, `submitted`, `working`, `completed`, `failed`, `cancelled`, `input-required`, `rejected`, `auth-required`

#### Message

| Field | Required | Notes |
|---|---:|---|
| `messageId` | Yes | Creator-generated unique id |
| `contextId` | No | Optional on input; server must include on output (via Task contextId, and in message status where used) |
| `taskId` | No | Optional on input; if provided, references an existing task |
| `role` | Yes | `user` or `agent` |
| `parts` | Yes | At least one Part |
| `metadata` | No | Free-form key/value |
| `extensions` | No | List of extension URIs present/contributed |
| `referenceTaskIds` | No | Explicit related task ids |

#### Part (oneof)

- Text: `{ "text": "..." }`
- File: `{ "file": { ... } }`
- Data: `{ "data": { "data": { ... } } }`

#### FilePart (oneof)

| Field | Required | Notes |
|---|---:|---|
| `fileWithUri` | oneof | URL to content |
| `fileWithBytes` | oneof | Base64-encoded bytes |
| `mediaType` | No | Example: `application/pdf` |
| `name` | No | Example: `document.pdf` |

#### DataPart

| Field | Required | Notes |
|---|---:|---|
| `data` | Yes | JSON object; in Part it is wrapped as `{ "data": { "data": {...} } }` |

#### Artifact

| Field | Required | Notes |
|---|---:|---|
| `artifactId` | Yes | Unique within a task |
| `parts` | Yes | At least one Part |
| `name` | No | Human readable |
| `description` | No | Human readable |
| `metadata` | No | Free-form key/value |
| `extensions` | No | Extension URIs present/contributed |

#### Streaming events

**TaskStatusUpdateEvent**
- `taskId` (required)
- `contextId` (required)
- `status` (required TaskStatus)
- `final` (required boolean)
- `metadata` (optional)

**TaskArtifactUpdateEvent**
- `taskId` (required)
- `contextId` (required)
- `artifact` (required Artifact)
- `append` (optional boolean)
- `lastChunk` (optional boolean)
- `metadata` (optional)

**StreamResponse** (oneof wrapper)
- Exactly one of: `task`, `message`, `statusUpdate`, `artifactUpdate`

#### Push notifications

**PushNotificationConfig**
- `id` (optional)
- `url` (required)
- `token` (optional)
- `authentication` (optional `AuthenticationInfo`)

**AuthenticationInfo** (push notification authentication)
- `schemes` (required list of strings, e.g., `["Bearer"]`)
- `credentials` (optional string)

Webhook payload: JSON `StreamResponse` (exactly one of task/message/statusUpdate/artifactUpdate).

#### AgentCard (public discovery)

AgentCard is the discovery manifest. Key required fields (per provided spec text):

| Field | Required | Notes |
|---|---:|---|
| `name` | Yes | Human readable |
| `description` | Yes | Human readable |
| `version` | Yes | Agent version (not protocol version) |
| `capabilities` | Yes | AgentCapabilities |
| `defaultInputModes` | Yes | Media types |
| `defaultOutputModes` | Yes | Media types |
| `skills` | Yes | AgentSkill list |
| `protocolVersion` | Optional | Latest released is `0.3.0` (examples use `"0.3.0"`); some draft text mentions a `"1.0"` default—treat proto as normative and negotiate via `A2A-Version` (Major.Minor). |
| `supportedInterfaces` | No | Preferred + additional; first is preferred |
| `supportsExtendedAgentCard` | Optional | Gate for `/v1/extendedAgentCard` |
| `securitySchemes` | No | Map of scheme name → SecurityScheme |
| `security` | No | Security requirements |

AgentCapabilities includes:
- `streaming` (optional bool)
- `pushNotifications` (optional bool)
- `extensions` (optional list)
- `stateTransitionHistory` (optional bool)

AgentInterface includes:
- `url` (required)
- `protocolBinding` (required: `JSONRPC`, `GRPC`, `HTTP+JSON`, etc.)
- `tenant` (optional)

#### Backward compatibility fields (deprecated in spec)

The spec allows agents to populate deprecated fields alongside `supportedInterfaces`:

- `url` (deprecated): should match the URL of the first `supportedInterfaces` entry
- `preferredTransport` (deprecated): should match the first entry’s `protocolBinding`
- `additionalInterfaces` (deprecated): should contain all entries from `supportedInterfaces`

We SHOULD populate these deprecated fields for broad ecosystem compatibility until we confirm all clients we care about are `supportedInterfaces`-aware.

#### Security fields (high-level requirements)

AgentCard may declare authentication requirements via:

- `securitySchemes`: map of scheme name → `SecurityScheme`
- `security`: array of security requirement objects

`SecurityScheme` is a oneof aligned to OpenAPI 3.2 concepts:

- API key: `apiKeySecurityScheme { location: query|header|cookie, name: ... }`
- HTTP auth: `httpAuthSecurityScheme { scheme: "Bearer"|"Basic"|..., bearerFormat?: ... }`
- OAuth2: `oauth2SecurityScheme { flows: ... , oauth2MetadataUrl?: ... }`
- OpenID Connect: `openIdConnectSecurityScheme { openIdConnectUrl: ... }`
- mTLS: `mtlsSecurityScheme { ... }` (the provided spec text flags a missing proto message; treat the proto as normative here)

#### Agent Card signatures (optional)

Agent Cards MAY include `signatures` (JWS) and require canonicalization via RFC 8785 (JCS) before signing.
We should treat signature support as a separate phase unless required by a target deployment.

---

### Core operations (binding-independent requirements)

The agent must implement the following abstract operations with the spec-defined semantics and error behaviors:

- Send Message
- Send Streaming Message
- Get Task
- List Tasks
- Cancel Task
- Subscribe to Task
- Set/Get/List/Delete Push Notification Config
- Get Agent Card (public discovery)
- Get Extended Agent Card (if supported)

#### Capability validation (normative)

When the Agent Card indicates a capability is not supported, the server MUST return:

- Push notifications (`capabilities.pushNotifications` is false/absent) → `PushNotificationNotSupportedError` for push config operations
- Streaming (`capabilities.streaming` is false/absent) → `UnsupportedOperationError` for stream/subscribe operations
- Extended Agent Card (`supportsExtendedAgentCard` is false/absent) → `UnsupportedOperationError` for `GetExtendedAgentCard`

---

### Binding method mapping reference (spec Section 5.3)

The spec text uses `/v1/...` endpoints, while the proto in `docs/spec/a2a.proto` uses unversioned HTTP annotations (e.g., `/message:send`, `/tasks`, `/extendedAgentCard`) with optional tenant-prefixed bindings.

**Implementation requirement (interop-first):**
- Expose the **`/v1/...`** endpoints (matching published examples), AND
- Provide **proto-annotation aliases** (unversioned paths) for compatibility with proto-generated REST clients.

| Operation | JSON-RPC method | gRPC method | HTTP+JSON (`/v1`) | HTTP+JSON (proto alias) |
|---|---|---|---|---|
| Send message | `SendMessage` | `SendMessage` | `POST /v1/message:send` | `POST /message:send` |
| Stream message | `SendStreamingMessage` | `SendStreamingMessage` | `POST /v1/message:stream` | `POST /message:stream` |
| Get task | `GetTask` | `GetTask` | `GET /v1/tasks/{id}` | `GET /tasks/{id}` (resource `name=tasks/{id}`) |
| List tasks | `ListTasks` | `ListTasks` | `GET /v1/tasks` | `GET /tasks` |
| Cancel task | `CancelTask` | `CancelTask` | `POST /v1/tasks/{id}:cancel` | `POST /tasks/{id}:cancel` |
| Subscribe to task | `SubscribeToTask` | `SubscribeToTask` | `POST /v1/tasks/{id}:subscribe` | `GET /tasks/{id}:subscribe` |
| Set push config | `SetTaskPushNotificationConfig` | `SetTaskPushNotificationConfig` | `POST /v1/tasks/{id}/pushNotificationConfigs` | `POST /tasks/{id}/pushNotificationConfigs` |
| Get push config | `GetTaskPushNotificationConfig` | `GetTaskPushNotificationConfig` | `GET /v1/tasks/{id}/pushNotificationConfigs/{configId}` | `GET /tasks/{id}/pushNotificationConfigs/{configId}` |
| List push configs | `ListTaskPushNotificationConfig` | `ListTaskPushNotificationConfig` | `GET /v1/tasks/{id}/pushNotificationConfigs` | `GET /tasks/{id}/pushNotificationConfigs` |
| Delete push config | `DeleteTaskPushNotificationConfig` | `DeleteTaskPushNotificationConfig` | `DELETE /v1/tasks/{id}/pushNotificationConfigs/{configId}` | `DELETE /tasks/{id}/pushNotificationConfigs/{configId}` |
| Get extended Agent Card | `GetExtendedAgentCard` | `GetExtendedAgentCard` | `GET /v1/extendedAgentCard` | `GET /extendedAgentCard` |

---

### HTTP+JSON/REST binding details (normative)

#### Content negotiation

- Accept `Content-Type: application/a2a+json` and `application/json` for requests.
- Respond with `Content-Type: application/a2a+json` when possible (clients may still send/expect `application/json`).
- For errors, return `Content-Type: application/problem+json` (RFC 9457).
- For streaming endpoints, return `Content-Type: text/event-stream`.

#### Path aliases (proto-grounded)

The proto in `docs/spec/a2a.proto` defines HTTP annotations without a `/v1` prefix. To avoid breaking clients that generate REST calls from proto annotations, we serve both:

- `/v1/...` paths (matching published spec examples), and
- unversioned aliases (matching proto http annotations), including tenant-prefixed variants where applicable.

Examples:
- `POST /v1/message:send` and `POST /message:send`
- `GET /v1/tasks/{id}` and `GET /tasks/{id}`
- `GET /v1/tasks` and `GET /tasks`

#### Query parameter naming (GET/DELETE)

- Query parameters MUST use **camelCase** to match JSON field names:
  - `contextId`, `pageSize`, `pageToken`, `historyLength`, `lastUpdatedAfter`, `includeArtifacts`

#### ListTasks required pagination semantics

- `pageSize` must be 1–100 (default 50).
- Results MUST be sorted by “last update time” descending.
- Response MUST include `nextPageToken` always (empty string if final page).
- If `includeArtifacts=false` (default), the `artifacts` field MUST be omitted entirely from each Task in the response.

#### Streaming (SSE) framing

- Each SSE event MUST include a `data:` line containing a JSON object equivalent to `StreamResponse`.
- The server SHOULD avoid reordering events and MUST preserve generation order.
- For task lifecycle streams:
  - First event: `{"task": { ... }}` (Task snapshot)
  - Then: zero or more `{"statusUpdate": ...}` / `{"artifactUpdate": ...}`
  - Stream closes when task reaches a terminal state

#### Service parameters

Service parameters are transmitted as HTTP headers:

- `A2A-Version`: requested protocol version (Major.Minor)
- `A2A-Extensions`: comma-separated extension URIs requested by the client

If `A2A-Version` is unsupported, respond with `VersionNotSupportedError`.

#### Authentication & authorization (HTTP)

The A2A spec treats authn/authz as “enterprise application” concerns:

- If credentials are missing/invalid for an endpoint that requires auth, respond with HTTP `401` and include an appropriate challenge (implementation-specific).
- If credentials are valid but insufficient, respond with HTTP `403`.
- For task/resource access control, the server MUST NOT leak existence of resources across authz boundaries:
  - Prefer returning `TaskNotFoundError` (404) for “not found OR not authorized” cases, rather than revealing a forbidden resource exists.
- `ListTasks` MUST be scoped to the authenticated caller’s authorization boundaries even without filters.

---

### JSON-RPC binding details (normative if/when implemented)

- Transport: JSON-RPC 2.0 over HTTP(S)
- Method names: PascalCase (e.g., `SendMessage`)
- Streaming: SSE; each `data:` contains a JSON-RPC response object with `result` wrapping the stream payload
- Service parameters: HTTP headers (`A2A-Version`, `A2A-Extensions`)
- Error mapping: JSON-RPC error codes -32001..-32009 per spec

**Parameter naming note (spec examples):**
The JSON-RPC section examples use a simplified `id` parameter for task operations (e.g., `GetTask` params `{ "id": "task-uuid" }`), while the gRPC binding uses resource `name` fields (`tasks/{task_id}`).
When implementing JSON-RPC, follow the normative `docs/spec/a2a.proto` JSON field names; accept both `id` and `name` forms where feasible to maximize interop with clients built against older examples.

---

### gRPC binding details (normative if/when implemented)

- Implement the `A2AService` service (SendMessage, SendStreamingMessage, GetTask, ListTasks, CancelTask, SubscribeToTask, push config methods, GetExtendedAgentCard).
- Service parameters: gRPC metadata keys `a2a-version` and `a2a-extensions` (lowercase).
- A2A-specific errors: include `google.rpc.ErrorInfo` in `google.rpc.Status.details` with:
  - `domain = "a2a-protocol.org"`
  - `reason` = uppercase snake case without “Error” suffix (e.g., `TASK_NOT_FOUND`)

---

## Spec Ambiguities & Project Decisions

The provided spec text includes a few internal inconsistencies. Because this document must be actionable, we explicitly record resolution decisions here.

### A2A protocol version string (“0.3” vs “1.0”)

`docs/spec/a2a_specification.md` states:
- **Latest released version:** `0.3.0`
- `A2A-Version` header value MUST be `Major.Minor` (example: `0.3`)
- AgentCard examples include `"protocolVersion": "0.3.0"`
- There is also text asserting `protocolVersion` defaults to `"1.0"` and an appendix describing a breaking change “Version 1.0…”

**Decision (for implementation planning):**
- Primary compliance target is **A2A v0.3.0**.
- Implement strict version negotiation:
  - Accept `A2A-Version: 0.3` as supported.
  - Return `VersionNotSupportedError` for other requested versions unless explicitly added to a supported list.
- Publish `AgentCard.protocolVersion` as `"0.3.0"` (matching the latest released spec), and treat any patch component as non-semantic for negotiation (Major.Minor drives compatibility per spec).
- Track “DRAFT v1.0” changes separately and do not advertise v1.0 support unless the vendored `docs/spec/a2a.proto` and the published “latest released” version guidance are aligned for the targeted major version.

### Service parameter prefix (“a2a-” vs “A2A-”)

Spec text says service parameters “will be prefixed with a2a-”, but the HTTP/IANA sections use `A2A-Version` and `A2A-Extensions`.

**Decision**:
- Accept both `A2A-Version`/`A2A-Extensions` and lowercase variants (HTTP headers are case-insensitive).
- Emit `A2A-Version` and `A2A-Extensions` in documentation/examples.

### Tenant path parameter

The spec includes an optional `tenant` “provided as a path parameter”, but the REST endpoint table does not show a tenant-prefixed path pattern.

**Decision**:
- Implement tenant as **optional** and support both:
  - Non-tenant routes exactly as spec (`/v1/...`)
  - A tenant-prefixed form for deployments that need it: `/{tenant}/v1/...`
- Reflect tenant presence in AgentCard `supportedInterfaces[].tenant` when used.

### Skill selection

The spec defines `AgentCard.skills`, but does not define a standard request field to select a specific skill.

**Decision**:
- Treat skills as discovery metadata and route to a **single configured PenguiFlow entrypoint** by default.
- Support a **non-normative** skill selector via `SendMessageRequest.metadata` (documented and optional) for multi-skill agents.
- Do not require any non-spec fields for basic operation.

---

## Detailed HTTP+JSON Requirements (Implementation Reference)

This section is intentionally verbose and should be treated as the “definition of done” for the HTTP+JSON binding.

> Note: This repo now includes `docs/spec/a2a.proto` (proto3) which defines the authoritative message schemas we implement. The JSON shapes and endpoint semantics below are aligned to that proto’s `google.api.http` annotations and JSON field mappings.

### 1) `GET /.well-known/agent-card.json` (public Agent Card)

**Response**
- Body: `AgentCard` JSON (camelCase fields)
- Should be stable and cacheable; avoid including secrets

**Minimum correctness requirements**
- `protocolVersion` is set to the latest supported released version (for this plan: `"0.3.0"`), and version negotiation uses `A2A-Version: 0.3` (Major.Minor) per spec.
- `supportedInterfaces` includes at least one entry for our HTTP+JSON base URL with `protocolBinding: "HTTP+JSON"`.
- `capabilities.streaming` and `capabilities.pushNotifications` match actual support.
- `defaultInputModes` / `defaultOutputModes` contain media types (e.g., `text/plain`, `application/json`).
- `skills[]` is populated (even if only a single default skill).

**Example response (skeleton)**

```json
{
  "protocolVersion": "0.3.0",
  "name": "PenguiFlow Agent",
  "description": "Exposes a PenguiFlow graph via A2A.",
  "supportedInterfaces": [
    {
      "url": "https://agent.example.com/a2a/v1",
      "protocolBinding": "HTTP+JSON"
    }
  ],
  "provider": {
    "organization": "Your Org",
    "url": "https://example.com"
  },
  "version": "2.6.6",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": false
  },
  "defaultInputModes": ["application/a2a+json", "text/plain", "application/json"],
  "defaultOutputModes": ["application/a2a+json", "text/plain", "application/json"],
  "skills": [
    {
      "id": "orchestrate",
      "name": "Orchestrate",
      "description": "Runs the configured PenguiFlow entrypoint.",
      "tags": ["orchestration"],
      "examples": ["Summarize this text..."]
    }
  ],
  "supportsExtendedAgentCard": false
}
```

### 2) `POST /v1/message:send` (SendMessage)

**Proto aliases**
- `POST /message:send`
- Optional tenant form: `POST /{tenant}/message:send` (proto additional binding)

**Headers**
- `Content-Type: application/a2a+json` (accept `application/json` for compatibility)
- `A2A-Version: <major.minor>` recommended; if present and unsupported → VersionNotSupportedError

**Request body: `SendMessageRequest`**
- `message` (required): `Message`
  - `messageId` (required; client-created)
  - `role` must be `user` for client → server requests
  - `parts` required; at least one Part
  - `contextId` optional:
    - If absent: server MUST generate a new `contextId` for new conversations/tasks
    - If present: server MUST preserve and echo it through Task contextId
  - `taskId` optional:
    - If absent: server MAY create a new Task
    - If present: server MUST treat it as a reference to an existing Task and apply terminal-state rules
- `configuration` (optional): `SendMessageConfiguration`
  - `acceptedOutputModes` (optional): media types accepted by client
  - `pushNotificationConfig` (optional): if push notifications supported, attach config to created task
  - `historyLength` (optional): apply history length semantics to returned Task(s)
  - `blocking` (optional; default false):
    - If true and the result is a Task, server MUST wait until terminal and return final Task state
    - Has no effect if returning a direct Message
- `metadata` (optional): free-form JSON

**Response (HTTP+JSON binding)**

While the abstract operation returns either a `Task` or a `Message`, the HTTP+JSON binding represents this as a wrapper object equivalent to the gRPC `SendMessageResponse` oneof:

- `{ "task": <Task> }`, OR
- `{ "message": <Message> }`

**Example request (single text part)**

```json
{
  "message": {
    "messageId": "msg-uuid",
    "role": "user",
    "parts": [{ "text": "Hello" }]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "blocking": false
  }
}
```

**Example response (Task)**

```json
{
  "task": {
    "id": "task-uuid",
    "contextId": "context-uuid",
    "status": { "state": "working" }
  }
}
```

**Required semantics**
- Must return immediately with either Task or Message.
- If a Task is returned, processing MAY continue asynchronously.
- If message references a task in a terminal state → `UnsupportedOperationError`.
- If message references a task that does not exist/accessible → `TaskNotFoundError`.
- If request parts include an unsupported media type → `ContentTypeNotSupportedError`.

**Blocking semantics (HTTP response shape)**

If `configuration.blocking=true` and the operation returns a Task, then the server MUST wait for task completion and return:

```json
{
  "task": {
    "id": "task-uuid",
    "contextId": "context-uuid",
    "status": { "state": "completed" },
    "artifacts": [
      { "artifactId": "output-0", "parts": [{ "text": "..." }] }
    ]
  }
}
```

### 3) `POST /v1/message:stream` (SendStreamingMessage)

**Proto aliases**
- `POST /message:stream`
- Optional tenant form: `POST /{tenant}/message:stream` (proto additional binding)

**Headers**
- `Content-Type: application/a2a+json` (accept `application/json`)
- Response: `Content-Type: text/event-stream`

**Request body**
- Same `SendMessageRequest` as `/v1/message:send`

**Response body (SSE)**
- Sequence of SSE events; each event MUST contain:
  - `data: <JSON>` where `<JSON>` is a `StreamResponse` object with exactly one of:
    - `task`, `message`, `statusUpdate`, `artifactUpdate`

**Example SSE stream (task lifecycle)**

```text
data: {"task":{"id":"task-uuid","contextId":"context-uuid","status":{"state":"working"}}}

data: {"artifactUpdate":{"taskId":"task-uuid","contextId":"context-uuid","artifact":{"artifactId":"output-0","parts":[{"text":"partial..."}]},"append":false,"lastChunk":false}}

data: {"artifactUpdate":{"taskId":"task-uuid","contextId":"context-uuid","artifact":{"artifactId":"output-0","parts":[{"text":" more"}]},"append":true,"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-uuid","contextId":"context-uuid","status":{"state":"completed"},"final":true}}
```

**Required stream patterns**
- **Message-only stream**:
  - One event with `{"message": ...}` then connection closes
- **Task lifecycle stream**:
  - First event: `{"task": ...}` (initial snapshot)
  - Then zero or more status/artifact updates
  - Stream closes when Task reaches terminal state (completed/failed/cancelled/rejected)

**Required semantics**
- If streaming capability is not declared → `UnsupportedOperationError`.
- Messages sent to terminal tasks → `UnsupportedOperationError`.
- Event ordering MUST match generation ordering (no reordering).

### 4) `GET /v1/tasks/{id}` (GetTask)

**Proto aliases**
- `GET /tasks/{id}` (proto uses resource `name=tasks/{id}`)
- Optional tenant form: `GET /{tenant}/tasks/{id}`

**Query parameters**
- `historyLength` (optional; apply history length semantics)

**Response**
- `Task` object

**Example response (completed)**

```json
{
  "id": "task-uuid",
  "contextId": "context-uuid",
  "status": {
    "state": "completed",
    "timestamp": "2025-10-28T10:30:00.000Z"
  },
  "artifacts": [
    {
      "artifactId": "output-0",
      "name": "result",
      "parts": [{ "text": "Done." }]
    }
  ]
}
```

**Errors**
- `TaskNotFoundError` when task does not exist or is not accessible

### 5) `GET /v1/tasks` (ListTasks)

**Proto aliases**
- `GET /tasks`
- Optional tenant form: `GET /{tenant}/tasks`

**Query parameters (all optional)**
- `contextId`
- `status` (TaskState string value)
- `pageSize` (1–100; default 50)
- `pageToken` (cursor token)
- `historyLength` (apply to each returned Task)
- `lastUpdatedAfter` (milliseconds since epoch)
- `includeArtifacts` (default false)

**Response body: ListTasksResponse**
- `tasks` (array of Task; required)
- `nextPageToken` (string; required; empty string means end)
- `pageSize` (int; required)
- `totalSize` (int; required)

**Example response (includeArtifacts=false; artifacts omitted)**

```json
{
  "tasks": [
    {
      "id": "task-1",
      "contextId": "ctx-1",
      "status": { "state": "working" }
    }
  ],
  "nextPageToken": "",
  "pageSize": 50,
  "totalSize": 1
}
```

**Required semantics**
- MUST be scoped to the authenticated caller’s authorization boundaries.
- MUST be sorted by last update time descending.
- MUST use cursor-based pagination.
- If `includeArtifacts=false`, `artifacts` MUST be omitted entirely from each Task object.

### 6) `POST /v1/tasks/{id}:cancel` (CancelTask)

**Proto aliases**
- `POST /tasks/{id}:cancel` (proto uses resource `name=tasks/{id}`)
- Optional tenant form: `POST /{tenant}/tasks/{id}:cancel`

**Response**
- Updated `Task` with cancelled state when cancellation succeeds

**Example response**

```json
{
  "id": "task-uuid",
  "contextId": "context-uuid",
  "status": { "state": "cancelled" }
}
```

**Errors**
- `TaskNotFoundError` if task does not exist or is not accessible
- `TaskNotCancelableError` if already terminal or cannot be cancelled

**Required semantics**
- Cancel is idempotent.

### 7) `POST /v1/tasks/{id}:subscribe` (SubscribeToTask)

**Proto aliases**
- `GET /tasks/{id}:subscribe` (proto uses GET)
- Optional tenant form: `GET /{tenant}/tasks/{id}:subscribe`

**Response**
- SSE stream where:
  - First event MUST be `{"task": <current Task snapshot>}`
  - Then `statusUpdate`/`artifactUpdate` events
  - Stream closes when task reaches terminal state

**Errors**
- `UnsupportedOperationError` if streaming unsupported or task is terminal
- `TaskNotFoundError` if task does not exist or is not accessible

**Required semantics**
- Multiple concurrent subscribers are allowed; events must be broadcast to all streams in the same order.

### 8) Push notification config endpoints

These endpoints are only valid if `capabilities.pushNotifications` is true; otherwise they MUST return `PushNotificationNotSupportedError`.

#### `POST /v1/tasks/{id}/pushNotificationConfigs` (Set/Create)

Creates or updates a push notification configuration for a task.

**Proto aliases**
- `POST /tasks/{id}/pushNotificationConfigs`
- Optional tenant form: `POST /{tenant}/tasks/{id}/pushNotificationConfigs`

**Important naming detail:** In the canonical model and gRPC binding, the push config is a named sub-resource:
- `TaskPushNotificationConfig.name`: `tasks/{task_id}/pushNotificationConfigs/{config_id}`

**Proto-grounded request shape (`docs/spec/a2a.proto`)**

`SetTaskPushNotificationConfigRequest` fields:
- `parent` (required): `tasks/{task_id}`
- `config_id` (required): becomes query parameter `configId` in REST
- `config` (required): `TaskPushNotificationConfig`

The proto’s HTTP annotation uses `body: "config"`, which means the HTTP request body is the JSON serialization of `TaskPushNotificationConfig`. Fields not in the body/path (notably `configId`) are transmitted via query parameters by google.api.http conventions.

**Request shape (recommended for REST):**
- `configId` is required as a **query parameter**:
  - `POST /v1/tasks/{id}/pushNotificationConfigs?configId=webhook-1`
- Request body is a `TaskPushNotificationConfig` object.

**Response**
- Return the created/updated `TaskPushNotificationConfig` (resource wrapper) as JSON.

**Example request (body: TaskPushNotificationConfig; query: configId=webhook-1)**

HTTP form:

```http
POST /v1/tasks/task-uuid/pushNotificationConfigs?configId=webhook-1
Content-Type: application/a2a+json
```

```json
{
  "name": "tasks/task-uuid/pushNotificationConfigs/webhook-1",
  "pushNotificationConfig": {
    "url": "https://client.example.com/webhooks/a2a",
    "token": "client-provided-token",
    "authentication": {
      "schemes": ["Bearer"],
      "credentials": "optional-shared-secret"
    }
  }
}
```

**Example response (resource wrapper)**

```json
{
  "name": "tasks/task-uuid/pushNotificationConfigs/webhook-1",
  "pushNotificationConfig": {
    "id": "webhook-1",
    "url": "https://client.example.com/webhooks/a2a",
    "token": "client-provided-token",
    "authentication": { "schemes": ["Bearer"] }
  }
}
```

**Server-side normalization rules (required for robustness)**

- The REST endpoint has task id in the path (`{id}`) and `configId` in the query string.
- The server MUST construct the canonical resource name:
  - `tasks/{id}/pushNotificationConfigs/{configId}`
- For interoperability, the server SHOULD accept either:
  - `name` present and matching the canonical resource name, OR
  - `name` omitted (server fills it in), OR
  - `name` present but inconsistent with `{id}`/`configId` (server rejects as validation error)
- The server MUST store the push notification configuration associated with the task until completion or deletion.

#### `GET /v1/tasks/{id}/pushNotificationConfigs/{configId}` (Get)

- Returns config or error if missing

**Proto aliases**
- `GET /tasks/{id}/pushNotificationConfigs/{configId}`
- Optional tenant form: `GET /{tenant}/tasks/{id}/pushNotificationConfigs/{configId}`

**Example response (resource wrapper)**

```json
{
  "name": "tasks/task-uuid/pushNotificationConfigs/webhook-1",
  "pushNotificationConfig": {
    "id": "webhook-1",
    "url": "https://client.example.com/webhooks/a2a",
    "token": "client-provided-token",
    "authentication": { "schemes": ["Bearer"] }
  }
}
```

#### `GET /v1/tasks/{id}/pushNotificationConfigs` (List)

- Returns list (pagination is optional per spec; document if implemented)

**Proto aliases**
- `GET /tasks/{id}/pushNotificationConfigs`
- Optional tenant form: `GET /{tenant}/tasks/{id}/pushNotificationConfigs`

**Example response**

```json
{
  "configs": [
    {
      "name": "tasks/task-uuid/pushNotificationConfigs/webhook-1",
      "pushNotificationConfig": { "id": "webhook-1", "url": "https://client.example.com/webhooks/a2a" }
    }
  ],
  "nextPageToken": ""
}
```

#### `DELETE /v1/tasks/{id}/pushNotificationConfigs/{configId}` (Delete)

- Must be idempotent

**Proto aliases**
- `DELETE /tasks/{id}/pushNotificationConfigs/{configId}`
- Optional tenant form: `DELETE /{tenant}/tasks/{id}/pushNotificationConfigs/{configId}`

**Response**
- Prefer `204 No Content` (or `200 OK` with an empty JSON object) as the deletion confirmation.

**Resource name mapping (Get/List/Delete)**

For REST, the path parameters map directly to the gRPC-style resource fields:

- Get/Delete request `name`:
  - `tasks/{id}/pushNotificationConfigs/{configId}`
- List request `parent`:
  - `tasks/{id}`

#### Webhook delivery (outbound)

- Agent POSTs to configured webhook URL with:
  - `Content-Type: application/a2a+json`
  - Auth headers per config.authentication
  - Body: a `StreamResponse` object (exactly one of task/message/statusUpdate/artifactUpdate)
- Client must respond 2xx to acknowledge
- Agent may retry with backoff; must set timeouts (10–30s recommended)
- Must implement SSRF mitigations (reject private IP ranges, localhost, link-local)

### 9) `GET /v1/extendedAgentCard` (GetExtendedAgentCard)

- Only valid if `supportsExtendedAgentCard` is true.
- Requires authentication per `securitySchemes` / `security`.

**Errors**
- `UnsupportedOperationError` if not supported
- `ExtendedAgentCardNotConfiguredError` if declared but not configured

---

## Gap Analysis (Spec vs PenguiFlow Today)

### 1) Agent discovery / AgentCard

**Spec requires**
- Public discovery: `GET /.well-known/agent-card.json`
- Schema: `AgentCard` with fields such as:
  - `protocolVersion`
  - `supportedInterfaces` (preferred + additional)
  - `capabilities` (`streaming`, `pushNotifications`, `extensions`, `stateTransitionHistory`)
  - `securitySchemes` and `security`
  - `defaultInputModes`, `defaultOutputModes`
  - `skills` with `id`, `name`, `description`, `tags`, `examples`, etc.
  - Optional deprecated fields (url/preferredTransport/additionalInterfaces) for backward compatibility

**Current**
- `GET /agent` returns a legacy object with mismatched schema (`penguiflow_a2a/server.py`)

**Gap**
- Endpoint path mismatch
- Schema mismatch (major)
- Capability representation mismatch

**Fix**
- Implement `AgentCard` model aligned to spec and publish it at `/.well-known/agent-card.json`.
- Optionally keep `GET /agent` as a legacy alias (see Migration section).

---

### 2) Send Message (unary)

**Spec requires**
- HTTP endpoint: `POST /v1/message:send`
- Request body: `SendMessageRequest`:
  - `message` (A2A Message w/ `messageId`, `role`, `parts`)
  - optional `configuration`:
    - `acceptedOutputModes` (media types)
    - `pushNotificationConfig`
    - `historyLength`
    - `blocking` (default false)
  - optional `metadata` (free-form map)
- Response:
  - HTTP+JSON binding returns a wrapper: either `{ "task": <Task> }` or `{ "message": <Message> }`

**Current**
- `POST /message/send` accepts `{payload, headers, meta, traceId, ...}` and returns `{status, taskId, contextId, output}`.

**Gap**
- Different request and response schema; no Parts/Artifacts/TaskStatus.
- No version negotiation or spec error model.
- No `blocking` semantics.

**Fix**
- Implement `SendMessageRequest` parsing, convert to internal execution request.
- Always return `{ "task": <Task> }` initially (compliant), optionally return `{ "message": <Message> }` for trivial flows later.
- Support `blocking`:
  - `blocking=false`: return quickly with `Task` state `submitted` or `working`.
  - `blocking=true`: wait for terminal state and return final `Task` including artifacts (and history per `historyLength` semantics).

---

### 3) Send Streaming Message

**Spec requires**
- HTTP endpoint: `POST /v1/message:stream` returning `text/event-stream`
- Stream semantics:
  - **Message-only stream**: exactly one `Message`, then close
  - **Task lifecycle stream**: first event is `Task`, then zero or more `statusUpdate` / `artifactUpdate`, then close at terminal state
- Payload shape: `StreamResponse` wrapper; each SSE `data:` contains JSON with exactly one of:
  - `{ "task": { ... } }`
  - `{ "message": { ... } }`
  - `{ "statusUpdate": { ... } }`
  - `{ "artifactUpdate": { ... } }`

**Current**
- `POST /message/stream` streams custom SSE event names and payloads (`status/chunk/artifact/done`) and emits PenguiFlow `StreamChunk` frames.

**Gap**
- Different SSE payload schema and event types.
- No `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent`.
- No “first event is Task” requirement.

**Fix**
- Implement `StreamResponse` wire format and send:
  - initial `{task: ...submitted/working...}`
  - then `statusUpdate` events for state transitions
  - then `artifactUpdate` events for streamed artifacts (append/lastChunk semantics)
  - then final `statusUpdate` with `final=true`

---

### 4) Get Task

**Spec requires**
- `GET /v1/tasks/{id}`
- Query parameter: `historyLength` (optional)
- Returns `Task` or `TaskNotFoundError`

**Current**
- Not implemented.

**Gap**
- Requires Task persistence and retrieval.

**Fix**
- Introduce a `TaskStore` abstraction with an in-memory default.
- Persist task status, artifacts, and message history.

---

### 5) List Tasks

**Spec requires**
- `GET /v1/tasks` with query params:
  - `contextId`, `status`, `pageSize`, `pageToken`, `historyLength`, `lastUpdatedAfter`, `includeArtifacts`
- Must:
  - return tasks visible to caller (auth scoping)
  - sort by last update desc
  - use cursor-based pagination
  - always include `nextPageToken` (empty string when done)
  - omit `artifacts` field entirely unless `includeArtifacts=true`

**Current**
- Not implemented.

**Gap**
- Requires Task indexing, sorting, pagination, and artifacts omission rules.

**Fix**
- Implement list query over `TaskStore` with stable cursor tokens and filtering.

---

### 6) Cancel Task

**Spec requires**
- `POST /v1/tasks/{id}:cancel`
- Returns updated `Task` or `TaskNotCancelableError`/`TaskNotFoundError`
- Idempotent behavior

**Current**
- `/tasks/cancel` returns `{taskId, cancelled, traceId}` and does not return `Task`.

**Gap**
- Endpoint mismatch, schema mismatch, error mapping mismatch.

**Fix**
- Implement `POST /v1/tasks/{id}:cancel`, map to PenguiFlow per-trace cancellation.
- Return updated `Task` with state `cancelled` when cancellation succeeds.

---

### 7) Subscribe to Task (stream existing task)

**Spec requires**
- `POST /v1/tasks/{id}:subscribe` returning SSE
- First event MUST be the current `Task`
- Then emit `statusUpdate` / `artifactUpdate`
- Must terminate on terminal state
- Error:
  - UnsupportedOperationError if streaming unsupported or task terminal
  - TaskNotFoundError if not found
- Must support multiple concurrent subscribers; broadcast ordered events to all.

**Current**
- Not implemented.

**Gap**
- Requires per-task event broadcast and subscriber registry.

**Fix**
- Implement per-task pub/sub in `TaskStore` or an `EventBus` layer.
- Ensure event ordering and independent stream lifetimes.

---

### 8) Push Notification Config + Delivery

**Spec requires**
- CRUD endpoints under `/v1/tasks/{id}/pushNotificationConfigs`
- If capability not supported, must return `PushNotificationNotSupportedError`
- On updates, server POSTs `StreamResponse` payloads to client webhook URL with configured auth
- Must implement delivery semantics (at-least-once attempt; retry allowed; timeouts; SSRF mitigations)

**Current**
- Not implemented.

**Gap**
- Requires config storage and outbound webhook sender.

**Fix**
- Implement `TaskPushNotificationConfig` persistence keyed by task id/config id.
- Implement async webhook sender that consumes the same event stream as SSE subscribers.

---

### 9) Get Extended Agent Card

**Spec requires**
- `GET /v1/extendedAgentCard`
- Only if `supportsExtendedAgentCard=true`
- Requires auth per AgentCard `securitySchemes`/`security`
- Must return `UnsupportedOperationError` or `ExtendedAgentCardNotConfiguredError` in appropriate cases

**Current**
- Not implemented.

**Fix**
- Phase in after core operations; requires auth plumbing and potentially multiple card variants.

---

### 10) Version negotiation + service parameter handling

**Spec requires**
- Parse `A2A-Version` per request.
- If unsupported: return `VersionNotSupportedError` (HTTP 400 with proper Problem Details `type`).

**Current**
- Not implemented.

**Fix**
- Implement request middleware/utility parsing and enforce version gating consistently.

---

## Target Architecture

### Design goals

- **Spec correctness first**: shapes, endpoints, error mapping, ordering guarantees.
- **Binding independence**: one core implementation of operations + model; multiple bindings as adapters.
- **Minimal coupling to PenguiFlow internals**: integrate via public APIs where possible; avoid monkey-patching flow internals in the long term.
- **Durable task state**: support in-memory default + pluggable persistence.

### PenguiFlow ↔ A2A mapping (project decisions)

The A2A spec intentionally does not prescribe how an agent maps protocol objects into its internal runtime. To keep implementation consistent and testable, we standardize the mapping rules PenguiFlow will use.

#### Tenant mapping

PenguiFlow `Headers.tenant` is currently required (`penguiflow/types.py`). A2A’s HTTP binding does not require a tenant field, but does allow an optional tenant “path parameter” concept.

**Decision:**
- If the server is deployed in tenant-prefixed mode (`/{tenant}/v1/...`), set `Headers.tenant = <tenant>` from the path.
- Otherwise, derive `Headers.tenant` from authentication context if available (e.g., JWT claim / API key mapping) and document the mapping.
- If neither is present, use a configurable default tenant (e.g., `"default"`). This default MUST be explicit in server config and tests.

#### Message mapping (A2A → PenguiFlow)

We need a deterministic mapping from A2A `Message` (role + parts) into a PenguiFlow `Message(payload, headers, trace_id, meta)`.

**Decision (default mapping):**
- Set `PenguiFlow Message.trace_id` to the A2A `Task.id` we create/attach (so cancellation and binding persistence align naturally).
- Set `PenguiFlow Message.payload` to a JSON-serializable object that preserves the full A2A message:
  - `{ "a2a": { "message": <Message>, "metadata": <SendMessageRequest.metadata?>, "configuration": <SendMessageConfiguration?> } }`
- Also include convenience unwrapped fields in `Message.meta` for routing/observability:
  - `a2a_context_id`, `a2a_task_id`, `a2a_message_id`, `a2a_role`, `a2a_requested_extensions`, `a2a_accepted_output_modes`

This avoids losing information (e.g., multiple parts, files) and keeps the mapping stable across protocol upgrades.

**Optional ergonomic mapping (future):**
- Provide a server option to “unwrap” a single `text` or `data` part into `Message.payload` for simpler flows. This is non-normative and must not affect wire compliance.

#### Result mapping (PenguiFlow → A2A artifacts)

PenguiFlow nodes can return arbitrary JSON-serializable values (and can stream `StreamChunk` text).

**Decision (default):**
- Represent outputs as exactly one primary `Artifact` per task unless the flow explicitly emits multiple artifacts:
  - `artifactId`: stable within task (e.g., `"output-0"` or UUID)
  - If final result is `str`: encode as `Part{text: ...}`
  - Otherwise: encode as `Part{data: {data: <json>}}`
- For streaming text:
  - Emit `TaskArtifactUpdateEvent` with the same `artifactId`
  - First chunk: omit `append` or set `append=false`; subsequent chunks set `append=true`
  - Set `lastChunk=true` on the final chunk

#### Errors and Task terminal states

Differentiate between:

- **Protocol-level errors** (invalid request, unsupported operation/version/content type): return HTTP error (Problem Details) / JSON-RPC error / gRPC status per spec.
- **Task-level failures** (flow execution error): return a `Task` with `status.state = failed` and include an optional `TaskStatus.message` (role=agent) describing the failure. The HTTP status remains `200` because the request was valid and created a Task.

### Proposed module layout (server)

```
penguiflow_a2a/
├── __init__.py
├── config.py                  # Server config (versions, base URLs, tenant mode, defaults)
├── models.py                  # Canonical A2A models (Pydantic) for JSON binding
├── errors.py                  # A2A error types + HTTP Problem Details mapping
├── store.py                   # TaskStore + PushConfigStore protocols + memory impl
├── core.py                    # Binding-independent operation handlers
├── bindings/
│   ├── http.py                # FastAPI routes for HTTP+JSON/REST binding
│   ├── jsonrpc.py             # JSON-RPC binding (optional phase)
│   └── grpc.py                # gRPC service wrapper (optional phase)
├── sse.py                     # SSE encoding helpers (StreamResponse frames)
└── webhooks.py                # Push notification delivery worker
```

### Core responsibility split

- **models.py**: enforce “oneof” constraints (Part, StreamResponse), aliasing, enum string values.
- **core.py**: implement operation semantics:
  - create tasks, update status, append artifacts, manage history, enforce terminal state rules
  - capability validation (streaming/push/ext-card)
  - version validation
- **store.py**: store/query Task state, history, artifacts, push configs, subscribers.
- **bindings/http.py**: translate HTTP requests to core calls and return HTTP responses per spec.
- **webhooks.py**: subscribe to task event stream and POST out `StreamResponse` payloads.

### Mapping PenguiFlow execution to A2A Task semantics

Key mapping requirements:

- A2A Task lifecycle:
  - create Task with `submitted`
  - transition to `working` when execution begins
  - transition to terminal state on completion/failure/cancel/reject
- A2A artifacts:
  - streamed output chunks become `TaskArtifactUpdateEvent` with `append` and `lastChunk`
  - final outputs become a final artifact (or final chunk of same artifact)
- A2A history:
  - store inbound messages (role=user) and agent status messages (role=agent) where applicable

**Important**: Multi-turn tasks are optional (“MAY accept additional messages”), but we should support:
- follow-up messages when `taskId` exists and task is not terminal
- terminal tasks must reject additional messages with `UnsupportedOperationError`

---

## Implementation Plan (Phased Roadmap)

Each phase includes deliverables and acceptance tests. “Phase complete” means required tests exist and pass, and docs/examples are updated.

### Phase 0 — Foundations (models, errors, store, wiring)

Deliverables:
- Vendor/pin the normative proto (done in this repo; keep in sync):
  - Canonical: `docs/spec/a2a.proto`
  - Render-helper copy: `docs/specification/grpc/a2a.proto`
  - Record the source/version (tag + commit SHA) in `docs/spec` (see `docs/spec/README.md`).
- `models.py`: canonical A2A JSON models (Task/Message/Part/Artifact/events/AgentCard)
- `errors.py`: Problem Details helper + error mappings per spec table
- `store.py`: TaskStore protocol + in-memory implementation
- `config.py`: version support list, tenant routing mode, defaults
- Minimal “core” API surface to create/update tasks in the store

Acceptance tests:
- Unit tests for:
  - Part oneof validation
  - StreamResponse oneof validation
  - Enum serialization (kebab-case) and camelCase JSON
  - Problem Details formatting for each A2A error type

### Phase 1 — HTTP+JSON/REST core operations (no push, no ext-card)

Deliverables:
- `/.well-known/agent-card.json`
- `POST /v1/message:send`
- `POST /v1/message:stream` (SSE)
- `GET /v1/tasks/{id}`
- `GET /v1/tasks`
- `POST /v1/tasks/{id}:cancel`
- `POST /v1/tasks/{id}:subscribe` (SSE)
- Proto path aliases (HTTP annotation compatibility):
  - `POST /message:send`, `POST /message:stream`
  - `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}:cancel`
  - `GET /tasks/{id}:subscribe` (SSE)
- Version negotiation middleware (`A2A-Version`)
- Capability validation (streaming on/off)

Acceptance tests:
- SendMessage:
  - returns Task with `contextId` present and correct status
  - supports `blocking=false` and `blocking=true`
  - historyLength semantics (unset vs 0 vs >0)
- Streaming:
  - first SSE frame is `{"task": ...}`
  - emits ordered `statusUpdate`/`artifactUpdate`
  - closes at terminal state; includes final `statusUpdate.final=true`
- SubscribeToTask:
  - first frame is current Task snapshot
  - multiple subscribers receive identical event sequences
- ListTasks:
  - sorted by last update desc
  - cursor pagination and `nextPageToken` always present
  - `includeArtifacts=false` omits artifacts field entirely

### Phase 2 — Push notifications (CRUD + delivery)

Deliverables:
- CRUD endpoints for push configs under `/v1/tasks/{id}/pushNotificationConfigs`
- Webhook sender:
  - sends StreamResponse payloads
  - uses configured authentication
  - implements SSRF mitigations (reject private IP ranges; localhost; link-local)
  - implements timeouts and retry policy
- AgentCard declares `capabilities.pushNotifications=true`

Acceptance tests:
- CRUD correctness and idempotency (delete is idempotent)
- When pushNotifications capability is false, endpoints return `PushNotificationNotSupportedError`
- Webhook delivery:
  - receives statusUpdate and artifactUpdate payloads
  - duplicate deliveries are possible; payload must be idempotent-friendly

### Phase 3 — Extended Agent Card (optional)

Deliverables:
- `GET /v1/extendedAgentCard`
- Auth integration per `securitySchemes` and `security`
- Error behavior:
  - UnsupportedOperationError if not supported
  - ExtendedAgentCardNotConfiguredError if declared but missing

Acceptance tests:
- Auth required; unauthenticated returns proper auth error
- Authenticated returns extended card and clients can replace cached card

### Phase 4 — JSON-RPC binding (optional, but recommended for ecosystem interop)

Deliverables:
- JSON-RPC endpoint (e.g., `/rpc`) implementing method mapping:
  - `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, push config methods, `GetExtendedAgentCard`
- JSON-RPC error mapping for A2A error codes -32001..-32009
- SSE stream format per JSON-RPC binding section

Acceptance tests:
- Same semantics as HTTP binding for equivalent calls (functional equivalence)

### Phase 5 — gRPC binding (optional)

Deliverables:
- gRPC service implementation generated from `a2a.proto`
- ErrorInfo mapping in `google.rpc.Status.details`

Acceptance tests:
- Round-trip tests for SendMessage, GetTask, ListTasks, CancelTask

---

## Acceptance Criteria & Compliance Checklist

This checklist should be used for release readiness.

### Agent Card
- [ ] `GET /.well-known/agent-card.json` returns a valid AgentCard
- [ ] `supportedInterfaces` populated; first entry is preferred
- [ ] Deprecated fields populated only if needed for backward compatibility
- [ ] `capabilities.streaming` and `capabilities.pushNotifications` accurately reflect reality
- [ ] `defaultInputModes`/`defaultOutputModes` are media types
- [ ] `skills[]` uses required fields (`id`, `name`, `description`, `tags`)

### Version negotiation
- [ ] Requests with unsupported `A2A-Version` return `VersionNotSupportedError` (HTTP 400, correct Problem Details `type`)
- [ ] Supported versions list is included in error details (per spec examples)

### SendMessage (HTTP)
- [ ] Accepts `SendMessageRequest` (message + optional configuration/metadata)
- [ ] Returns `{ "task": ... }` or `{ "message": ... }` (we default to `{ "task": ... }`)
- [ ] If `blocking=true`, returns terminal Task with artifacts and status
- [ ] If `blocking=false`, returns immediately with non-terminal Task

### Streaming (HTTP SSE)
- [ ] `/v1/message:stream` uses `text/event-stream`
- [ ] First frame is Task or single Message; conforms to StreamResponse oneof
- [ ] Event ordering preserved
- [ ] Stream closes at terminal state

### Task APIs
- [ ] `GET /v1/tasks/{id}` returns Task or TaskNotFoundError
- [ ] `GET /v1/tasks` supports filters/pagination and omits artifacts unless requested
- [ ] `POST /v1/tasks/{id}:cancel` is idempotent and returns updated Task or proper error
- [ ] `POST /v1/tasks/{id}:subscribe` returns first Task snapshot and streams updates

### Push notifications
- [ ] CRUD endpoints implemented and gated by `capabilities.pushNotifications`
- [ ] Webhook payload is StreamResponse (exactly one of task/message/statusUpdate/artifactUpdate)
- [ ] Delivery uses auth from config; timeouts; retries; SSRF mitigations

### Errors
- [ ] HTTP errors use RFC 9457 Problem Details and correct `type` URIs
- [ ] Error mapping matches spec table for each A2A-specific error
- [ ] Validation errors map to HTTP 400/422 appropriately without leaking sensitive info

---

## Testing Strategy

### Coverage expectations

Per repo policy, keep overall coverage >= 85%. For A2A work:
- Every new operation must have at least one **negative/error-path** test.
- Streaming and push notifications must be tested end-to-end.

### Test types

1. **Model tests**
   - oneof validation
   - JSON aliasing and field naming
   - enum string values
2. **HTTP integration tests**
   - FastAPI app tests (TestClient/async client)
   - SSE stream parsing and ordering checks
3. **Concurrency tests**
   - multiple subscribers
   - concurrent tasks and isolation
4. **Security tests (push notifications)**
   - SSRF URL rejection tests
   - auth header emission tests

### Interop tests (recommended)

Add a lightweight “interop harness” that:
- fetches AgentCard
- runs SendMessage blocking/non-blocking
- streams via SSE and validates payloads against our models

---

## Migration & Backward Compatibility

### Legacy endpoints

The existing endpoints (`/agent`, `/message/send`, `/message/stream`, `/tasks/cancel`) are not spec-compliant but may have downstream/internal usage.

**Plan**
- Keep legacy endpoints during a deprecation window.
- Add new spec-compliant endpoints in parallel under `/v1/...` and `/.well-known/...`.
- Document legacy endpoints as **non-A2A** (explicitly) once v1 endpoints land.

### Data model migration

No direct wire compatibility exists between the legacy payload and A2A canonical Message/Parts model. Migration requires explicit client updates (or a compatibility shim that wraps legacy payload into A2A Message/DataPart, which is optional and non-normative).

---

## Risk Assessment

### Primary risks

1. **Spec drift / ambiguity**
   - Mitigation: keep the vendored proto (`docs/spec/a2a.proto`) pinned to a specific upstream tag+SHA and treat changes as explicit “spec upgrade” work.
2. **Task persistence and pagination correctness**
   - Mitigation: define TaskStore contract early; include pagination tests from Phase 1.
3. **Streaming semantics and multi-subscriber broadcast**
   - Mitigation: build a per-task event bus with deterministic ordering and test concurrency.
4. **Push notification security (SSRF)**
   - Mitigation: strict URL validation, allowlist options, timeouts, bounded retries.
5. **Coupling to PenguiFlow internals**
   - Mitigation: reduce monkey-patching; prefer explicit hooks/callbacks for streaming and status transitions.

### Non-goals (until explicitly planned)

- “Expose PenguiFlow as an MCP server” (separate feature track)
- Implement every possible extension URI semantics (support declaration + opt-in first)
- Agent Card signatures (JWS canonicalization) unless required for a target deployment

---

## Appendix: Canonical Error Mapping (HTTP)

Per spec table (must be implemented exactly):

| A2A Error Type | HTTP Status | Problem `type` URI |
|---|---:|---|
| TaskNotFoundError | 404 | `https://a2a-protocol.org/errors/task-not-found` |
| TaskNotCancelableError | 409 | `https://a2a-protocol.org/errors/task-not-cancelable` |
| PushNotificationNotSupportedError | 400 | `https://a2a-protocol.org/errors/push-notification-not-supported` |
| UnsupportedOperationError | 400 | `https://a2a-protocol.org/errors/unsupported-operation` |
| ContentTypeNotSupportedError | 415 | `https://a2a-protocol.org/errors/content-type-not-supported` |
| InvalidAgentResponseError | 502 | `https://a2a-protocol.org/errors/invalid-agent-response` |
| ExtendedAgentCardNotConfiguredError | 400 | `https://a2a-protocol.org/errors/extended-agent-card-not-configured` |
| ExtensionSupportRequiredError | 400 | `https://a2a-protocol.org/errors/extension-support-required` |
| VersionNotSupportedError | 400 | `https://a2a-protocol.org/errors/version-not-supported` |

## Appendix: Canonical Error Mapping (JSON-RPC)

Per spec table (must be implemented exactly when JSON-RPC binding is enabled):

| A2A Error Type | JSON-RPC code |
|---|---:|
| TaskNotFoundError | -32001 |
| TaskNotCancelableError | -32002 |
| PushNotificationNotSupportedError | -32003 |
| UnsupportedOperationError | -32004 |
| ContentTypeNotSupportedError | -32005 |
| InvalidAgentResponseError | -32006 |
| ExtendedAgentCardNotConfiguredError | -32007 |
| ExtensionSupportRequiredError | -32008 |
| VersionNotSupportedError | -32009 |

## Appendix: Canonical Error Mapping (gRPC)

Per spec table (must be implemented exactly when gRPC binding is enabled):

| A2A Error Type | gRPC status |
|---|---|
| TaskNotFoundError | `NOT_FOUND` |
| TaskNotCancelableError | `FAILED_PRECONDITION` |
| PushNotificationNotSupportedError | `UNIMPLEMENTED` |
| UnsupportedOperationError | `UNIMPLEMENTED` |
| ContentTypeNotSupportedError | `INVALID_ARGUMENT` |
| InvalidAgentResponseError | `INTERNAL` |
| ExtendedAgentCardNotConfiguredError | `FAILED_PRECONDITION` |
| ExtensionSupportRequiredError | `FAILED_PRECONDITION` |
| VersionNotSupportedError | `UNIMPLEMENTED` |
