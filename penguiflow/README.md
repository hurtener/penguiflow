# PenguiFlow package overview

PenguiFlow is an async-first library for LLM tool-using agents built on `ReactPlanner`.
This document focuses on the internals that live inside the `penguiflow/` package so
contributors understand how the pieces fit together.

## Module tour

| Module | Purpose |
| --- | --- |
| `planner/` | `ReactPlanner` loop: prompts, action schema and repair, parallel calls and joins, pause/resume, steering hooks, memory, guardrails, reflection, streaming events. |
| `llm/` | Native LLM layer: client, provider adapters, structured output, routing, retries, pricing, telemetry. |
| `node.py` | `Node` wrapper (an `async def fn(args, ctx)` tool plus its name) and `NodePolicy` metadata. |
| `registry.py` | `ModelRegistry` that caches `TypeAdapter`s for each tool's input/output models. |
| `catalog.py` | `NodeSpec` dataclass, `@tool` decorator, and `build_catalog` feeding the planner. |
| `tools/` | `ToolNode` for MCP/UTCP/HTTP tool sources, OAuth, presets, MCP resources and apps. |
| `common_tools/` | Ready-made planner tools (web fetch/search). |
| `skills/` | Skill packs, local skill store, and the planner's skill tools. |
| `rich_output/` | Rich UI component registry, validation, and planner tools for rendering components. |
| `steering/` | `SteeringInbox` and guard inbox for cancel/pause/inject events during runs. |
| `sessions/` | `StreamingSession`, `SessionManager`, `PlannerTaskPipeline`, background tasks, session KV, job scheduler. |
| `state/` | StateStore protocol, models, adapters, and the in-memory reference implementation. |
| `artifacts.py` | Artifact stores for large or binary tool outputs. |
| `remote.py` | `RemoteTransport` protocol and remote task models used by the A2A client toolsets. |
| `types.py` | `Headers` and `Message` envelope used when forwarding work to remote agents. |
| `logging.py` | `configure_logging` and structured/extra-aware formatters. |
| `cli/` | `penguiflow tools list|connect` CLI. |
| `admin.py` | Developer CLI helpers (`penguiflow-admin`) for inspecting StateStore history. |
| `__init__.py` | Public surface that re-exports the main primitives for consumers. |

### Optional extras

The `penguiflow_a2a` package ships alongside and contains the A2A HTTP+JSON/JSON-RPC and
gRPC bindings:

* **Serving a planner**: `A2AService(planner, agent_card=...)` runs one planner task per A2A
  task and is mounted with `create_a2a_http_router` / `install_a2a_http` /
  `create_a2a_http_app` (`a2a-server` extra) or the gRPC helpers (`a2a-grpc` extra).
* **Calling remote agents**: `A2AHttpTransport` (`a2a-client` extra) plus
  `A2AAgentToolset` / `A2ARouterToolset` wrap remote A2A agents as planner tools
  (`NodeSpec`s) invoked through `ToolContext`.

Install the `planner` extra for the LiteLLM-backed client and tool integrations; the
native LLM layer needs the `llm` extra for provider SDKs. You can always inject a
deterministic stub through the `llm_client` parameter.

## Key runtime behaviors

* **Typed tools**: the planner validates tool arguments against the registered input model
  before calling `node.func(args, ctx)`, and validates the result against the output model.
  Validation failures become observations the LLM can repair.
* **Session dispatch**: runs that carry a `session_id` in `tool_context` execute on a
  per-session planner runtime, so different sessions run concurrently and one session is
  serialized.
* **Pause/resume**: tools call `await ctx.pause(reason, payload)`; `run()` returns a
  `PlannerPause` whose `resume_token` resumes the run via `resume(token, user_input=...)`.
  Pause records persist through the configured `StateStore` when present.
* **Steering**: a `SteeringInbox` passed as `steering=` lets callers cancel, pause, or inject
  guidance; the planner checks it between steps and while tools run.
* **Events**: every step, tool call, repair, stream chunk and finish is emitted as a
  `PlannerEvent` to the `penguiflow.planner` logger and to `event_callback`.

## Testing & examples

* Unit tests live under `tests/` and exercise the planner, LLM layer, tools, sessions,
  state stores and A2A bindings.
* Runnable examples live under `examples/`. Use `uv run python <path>` to execute them
  locally, starting with `examples/react_minimal/main.py`.

For a conceptual overview and getting-started guide, see `docs/getting-started/`.
