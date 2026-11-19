# Transports: SSE, Push Notifications, gRPC

## SSE (`message/stream`)

`message/stream` is the streaming counterpart to `message/send`. The router uses FastAPI's `StreamingResponse` to yield A2A `StreamResponse` events as Server-Sent Events.

Encoding: `encode_stream_response(event)` produces the SSE-formatted bytes (`data: <json>\n\n`). The router applies this per event.

What the server emits during a stream:
- Initial task snapshot (state, ids).
- Progressive answer chunks (`artifactUpdate` on `output-0`), mapped from the planner's `llm_stream_chunk` answer-channel events — only when the planner has `stream_final_response=True`.
- The final answer artifact, which replaces the streamed chunks (`append=false`, `lastChunk=true`).
- A final `statusUpdate` (`final: true`) with the terminal state — or `input-required` when the planner paused.

Client side: any SSE-aware HTTP client works. `A2AHttpTransport.stream(request)` returns an async iterator of `RemoteStreamEvent`s; an `append=false` artifact update replaces the text streamed so far.

### When to use SSE vs task mode
- **SSE** — caller stays connected and consumes chunks immediately. Best for chat-style UX.
- **Task mode** — submit work, poll or subscribe later. Best for long-running, resumable, HITL-prone work.

The toolset's `execution_mode` exposes both; the route handler itself supports them all.

## Push Notifications

For long-running tasks where the caller doesn't want to stay connected, A2A defines **push notifications**: the agent posts task updates to a webhook URL the caller provides.

### `HttpPushNotificationSender`

```python
from penguiflow_a2a.push import HttpPushNotificationSender

sender = HttpPushNotificationSender(
    timeout_s=10.0,
    user_agent="penguiflow-a2a-push/1.0",
)
```

Sends task updates via HTTP POST to the configured webhook. Supports the A2A `AuthenticationInfo` shape (bearer, basic, etc.) — `_build_auth_header` constructs the `Authorization` header.

### SSRF defense: `is_safe_webhook_url`

The sender refuses to POST to:
- Loopback (`127.0.0.0/8`, `::1`).
- Link-local (`169.254.0.0/16`, `fe80::/10`).
- Multicast.
- RFC1918 private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
- Unique-local IPv6 (`fc00::/7`).
- Reserved/unspecified ranges.

`is_safe_webhook_url(url)` is the predicate. `_resolve_addresses(host)` performs DNS resolution and checks every address.

This is intentional and not configurable: blocking SSRF is a hard requirement for a multi-tenant A2A agent that accepts arbitrary webhook URLs from callers.

### Common push pitfalls
- "Webhook never arrives" — most often a private/loopback URL blocked by `is_safe_webhook_url`. Use a public URL.
- "Auth header missing" — verify the caller's `AuthenticationInfo` matches what `_build_auth_header` understands (bearer, basic, custom scheme).
- "Duplicate notifications" — the sender retries on transient failures; callers must be idempotent (use task_id as the dedup key).

## gRPC binding

Optional. Install with:

```bash
pip install "penguiflow-lite[a2a-grpc]"
```

### Wiring

```python
import grpc
from penguiflow_a2a import A2AService
from penguiflow_a2a.bindings.grpc import add_a2a_grpc_service

server = grpc.aio.server()
port = server.add_insecure_port("127.0.0.1:50051")
service = A2AService(planner, agent_card=card, config=config)
add_a2a_grpc_service(server, service)

await service.start()
await server.start()
```

### Protobuf surface

The bindings are generated from `penguiflow_a2a/grpc/a2a.proto` (compiled into `a2a_pb2.py` and `a2a_pb2_grpc.py`). Key messages and RPCs:
- `Message`, `Part`, `Role` — A2A message primitives.
- `SendMessageRequest` / `SendMessageResponse` — RPC `SendMessage` (blocking).
- `StreamMessageRequest` / `StreamMessageResponse` (server-streaming) — RPC `StreamMessage`.
- `Task`, `TaskState` — task lifecycle.
- `CancelTaskRequest` / `CancelTaskResponse` — RPC `CancelTask`.

Client side:

```python
from penguiflow_a2a.grpc import a2a_pb2, a2a_pb2_grpc

channel = grpc.aio.insecure_channel("127.0.0.1:50051")
stub = a2a_pb2_grpc.A2AServiceStub(channel)
response = await stub.SendMessage(
    a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id="msg-1",
            role=a2a_pb2.Role.ROLE_USER,
            parts=[a2a_pb2.Part(text="hello grpc")],
        ),
        configuration=a2a_pb2.SendMessageConfiguration(blocking=True),
    ),
    metadata=(("a2a-version", "0.3"),),
)
```

The `a2a-version` metadata header is required to negotiate protocol version on each gRPC call.

### Runnable example

`examples/a2a_grpc_server/main.py` builds a `ReactPlanner` (offline scripted LLM + one tool), serves it over gRPC, sends a `SendMessage` request, and prints the task state and answer. Run with `uv run python examples/a2a_grpc_server/main.py`.

### Optional import guard

`penguiflow_a2a/__init__.py` imports gRPC bindings inside `try/except RuntimeError` — if the extras aren't installed, the rest of the package still works, and `A2AGrpcServicer` / `add_a2a_grpc_service` are simply not exported.

## Choosing a transport

| Need | Pick |
|---|---|
| Browser/UI client, real-time | HTTP + SSE (`message/stream`) |
| Server-to-server, simple | HTTP + JSON-RPC (`/rpc`) or REST (`message/send`) |
| Long-running, caller disconnects | HTTP + push notifications + task mode |
| High-throughput, low-latency, polyglot | gRPC |
| Mixed deployment | Multiple bindings on the same `A2AService` |

A single `A2AService` can be mounted on FastAPI **and** gRPC simultaneously — they share the same task store and lifecycle.
