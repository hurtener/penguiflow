# Client and `A2AAgentToolset`

The toolset turns a remote A2A agent into typed planner tools your `ReactPlanner` can call.

## Building a toolset

```python
from penguiflow_a2a import A2AAgentToolset, A2AHttpTransport

transport = A2AHttpTransport(
    # No base_url — each agent_url is passed per request.
    version="0.3",                                       # A2A protocol version sent as A2A-Version header
    headers={"X-Caller": "manager-prod"},                 # global headers
    agent_headers={                                       # per-agent overrides (by agent_url)
        "https://specialist.example.com": {
            "Authorization": "Bearer ${SPECIALIST_TOKEN}",
        },
    },
    timeout_s=30.0,                                       # fallback timeout
    # client=httpx.AsyncClient(...),                       # optional injected client
)

toolset = A2AAgentToolset(
    agent_url="https://specialist.example.com",           # set per toolset
    transport=transport,
    agent_card=card,                                       # optional but recommended
    default_timeout_s=30.0,
    default_metadata={"caller": "manager-prod"},
    include_tool_context_keys=("tenant", "session_id", "task_id", "user_id"),
)
```

`include_tool_context_keys` lists which keys from `tool_context` should be forwarded as A2A `metadata` on each call (useful for tenant scoping and observability). The transport itself is agent-agnostic — `agent_url` lives on the `A2AAgentToolset` (or, when using `A2ARouterToolset`, on each candidate).

## Declaring a tool

```python
node_spec = toolset.tool(
    name="ask_specialist",          # planner tool name
    skill="answer",                  # remote A2A skill name
    args_model=AskArgs,              # Pydantic input model
    out_model=AnswerOut,             # Pydantic output model (plain-text answers fill a single-field model)
    desc="Delegate to the specialist agent",
    tags=("a2a", "remote", "specialist"),
    auth_scopes=(),
    side_effects="external",
    streaming=False,
    timeout_s=None,
    metadata=None,
    metadata_builder=None,           # callable for per-call metadata
    payload_builder=None,            # callable for per-call payload
    cancel_on_cancel=True,           # propagate caller cancel to remote
    chunk_channel="answer",          # which chunk channel to materialize
    execution_mode="auto",           # auto|blocking|stream|task
    use_subscription=True,
    poll_interval_s=0.25,
    max_poll_attempts=120,
    remote_event_sink=None,          # async sink for RemoteTaskEvent
)
```

Add `node_spec` to your planner's catalog (the planner config skill explains catalog wiring — see [[penguiflow-reactplanner-config]]).

## Execution modes

| Mode | When to use | Behavior |
|---|---|---|
| `auto` | Default | Picks `stream` if `streaming=True`, else `blocking`. Falls back from `task` to `blocking` if transport can't do tasks. |
| `blocking` | Short, deterministic calls | Synchronous `message/send`; returns the final result. |
| `stream` | Long answers with progressive content | Subscribes to SSE; emits chunks via the planner's chunk channel. |
| `task` | Long-running work, possible HITL | Submits as a task; polls or subscribes for updates. |

Streaming chunks are routed to `chunk_channel` (default `"answer"`). For UI-facing chunk streams, see [[penguiflow-streaming]].

## Custom payload and metadata builders

```python
def payload_builder(args: AskArgs, ctx: ToolContext) -> Any:
    # Transform args before sending to the remote
    return {"q": args.question, "user": ctx.get("user_id")}

def metadata_builder(args: AskArgs, ctx: ToolContext) -> Mapping[str, Any]:
    return {"trace_origin": "manager", "project": ctx["project_id"]}

node_spec = toolset.tool(
    ...,
    payload_builder=payload_builder,
    metadata_builder=metadata_builder,
)
```

The builders run on every call. Use them when:
- The remote skill expects a different schema than your `args_model`.
- You need per-call metadata derived from `tool_context`.

## Conversation continuity (`RemoteBinding`)

On every call, the toolset:
1. Calls `find_binding(router_session_id, agent_url, skill, tenant, user)` to find the newest non-terminal binding.
2. If found and the remote task is still resumable, reuses `task_id` and `context_id`.
3. Calls `save_remote_binding(...)` after the call with updated state (`is_terminal`, `awaiting_remote_input`, etc.).
4. Calls `mark_binding_terminal(...)` once the task reaches a terminal state.

The toolset reads the `StateStore` from `tool_context["state_store"]` or from the planner's `_state_store` attribute. If neither is present, every call is treated as a fresh conversation (no continuity).

`RemoteBinding` fields the store must persist:
- `router_session_id`
- `agent_url`
- `remote_skill`
- `tenant_id`, `user_id`
- `last_remote_task_id`, `context_id`
- `is_terminal: bool`
- `metadata: Mapping[str, Any]`

See [[penguiflow-statestore]] for durable backends.

## Remote task lifecycle and pauses

When `execution_mode="task"` and the remote returns `INPUT_REQUIRED` or `AUTH_REQUIRED`:
- The toolset pauses the planner via the pause state machine (see [[penguiflow-hitl-pause-resume]]).
- The binding metadata records `awaiting_remote_input` / `awaiting_remote_auth`.
- On resume, the toolset calls the remote with the user's input or refreshed credentials.

`RemoteEventSink` is an async callable receiving `RemoteTaskEvent` for observability:

```python
class MySink:
    async def __call__(self, event: RemoteTaskEvent) -> None:
        await metrics.record(event.kind, event.agent_url, event.task_id)
```

## `A2AHttpTransport` knobs

Concrete `RemoteTransport` for HTTP. Configures:
- `base_url`
- `auth_headers` (dict)
- timeouts, retries, connection pooling

The transport implements `send_message`, `stream_message`, `send_task`, `subscribe_task`, `cancel_task`. For non-HTTP transports (gRPC, in-process), implement the `RemoteTransport` protocol yourself.

## Cancellation propagation

When `cancel_on_cancel=True` (default), cancelling the manager-side tool call (a steering `CANCEL`, or the planner run being cancelled) while a streamed remote task is in flight sends `tasks/cancel` to the remote agent. This is best-effort — the remote may or may not honor it. If the remote ignores cancels, set `cancel_on_cancel=False` and handle cancellation locally only.

## Operational defaults

- `default_timeout_s` on the toolset (don't rely on per-call timeouts).
- `execution_mode="auto"` unless you know better.
- `cancel_on_cancel=True` for user-facing interactive flows.
- Forward `tenant`, `user_id`, `session_id`, `task_id` via `include_tool_context_keys` (the defaults).
- Provide an `agent_card` so the planner can introspect skill metadata.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Calls always start fresh | No `StateStore` visible to the toolset | Pass `state_store` in `tool_context` or attach to planner |
| Remote returns `AUTH_REQUIRED` and planner stalls | Pause/resume not wired | Wire pause state — [[penguiflow-hitl-pause-resume]] |
| Streaming output empty | `streaming=True` but transport doesn't subscribe | Verify transport implements `stream_message` |
| `task` mode silently downgrades to `blocking` | Transport has no `send_task` | Implement `send_task` or use `execution_mode="blocking"` |
| Metadata leaks user PII | `include_tool_context_keys` too broad | Use a `metadata_builder` to redact before send |
| Tool output fails validation with a string `input` | Remote answered with text and `out_model` has several fields | Use a single-field `out_model` (e.g. `answer: str`) — the toolset wraps text into it — or have the remote return structured data |
| Cancel doesn't propagate | `cancel_on_cancel=False` or remote ignores cancels | Flip flag or treat remote as terminal-on-cancel locally |
