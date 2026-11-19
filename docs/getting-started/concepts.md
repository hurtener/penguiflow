# Concepts

## What it is / when to use it

This page defines the *major moving parts* of PenguiFlow and how they fit together.

Use it when you want to:

- understand how tools, the planner and its LLM client relate,
- decide which production surface (sessions, state store, A2A) you need,
- know where reliability, pause/resume and observability come from.

If you want runnable code first, start with **[Quickstart](quickstart.md)**.

## Non-goals / boundaries

- This page is not a full API reference. It focuses on the mental model and the contracts that matter.
- It does not cover external tool configuration and auth (see **[Tooling](../planner/tooling.md)** and **[Tools & integrations](../tools/configuration.md)**).

## Contract surface (the pieces you compose)

### Tool (typed async function)

A **tool** is an async function with typed input and output:

- signature: `async def fn(args: InModel, ctx: ToolContext) -> OutModel`
- `@tool(desc=..., side_effects=..., tags=...)` adds the metadata the planner shows the LLM
- `Node(fn, name=...)` wraps it, and `ModelRegistry.register(name, InModel, OutModel)` declares its contract
- `build_catalog(nodes, registry)` turns them into the planner's tool catalog

External tools (MCP, UTCP, HTTP) plug in the same way through `ToolNode`; see **[Tooling](../planner/tooling.md)**.

### ToolContext (in-tool capabilities)

Every tool receives a `ctx` that exposes:

- `ctx.llm_context` (visible to the LLM) and `ctx.tool_context` (tool-only: tenant, session, callbacks),
- `await ctx.pause(reason, payload)` for human-in-the-loop checkpoints,
- `await ctx.emit_chunk(...)` / artifact helpers for streaming partial output,
- artifact and session KV stores when configured.

### Planner (ReactPlanner)

The **planner** is an LLM-driven loop that selects tools and orchestrates their execution with:

- a typed action schema and repair attempts,
- parallel tool calls and joins,
- pause/resume (HITL) and steering (cancel, inject guidance),
- optional short-term memory, guardrails, reflection, skills and rich output,
- `PlannerEvent` callbacks for observability and streaming.

`await planner.run(query, tool_context=...)` returns a `PlannerFinish` (answer, or no path / budget exhausted) or a `PlannerPause` (resume with `planner.resume(token, user_input=...)`).

Canonical: **[ReactPlanner overview](../planner/overview.md)**.

### Production surfaces

- **Sessions** (`StreamingSession`, `SessionManager`, `PlannerTaskPipeline`): multi-turn sessions with foreground/background tasks and live updates. See **[Background tasks](../planner/background-tasks.md)**.
- **State store** (`StateStore`): durable pause records, memory, task state and remote bindings. See **[State store](../tools/statestore.md)**.
- **A2A** (`penguiflow_a2a`): serve a planner as an A2A agent with `A2AService(planner, ...)`, and call remote agents as tools with `A2AAgentToolset`.

## Operational defaults (safe starting points)

- Give every tool an explicit input/output model; validation errors become planner observations the LLM can repair.
- Pass `tenant` and `session_id` in `tool_context` so memory, pause records and state are scoped correctly.
- Set budgets (`max_iters`, token budgets, deadlines) before production rollout.
- Attach an `event_callback` early and decide where planner events go (logs, metrics, UI).

## Failure modes & recovery

- **The planner loops or stalls**: tighten tool descriptions and budgets; see **[Troubleshooting](../planner/troubleshooting.md)**.
- **Tool output fails validation**: the tool's return value doesn't match its registered output model.
- **Pause tokens are lost across processes**: configure a `StateStore` so pause records survive restarts.
- **Retries amplify side effects**: mark tools with the right `side_effects` and make them idempotent.

## Observability

The planner emits `PlannerEvent`s (step start/end, tool calls, LLM stream chunks, finish, errors) to `event_callback`. Use `configure_logging(structured=True)` for JSON logs. See **[Planner observability](../planner/observability.md)** and **[Logging](../observability/logging.md)**.

## Security / multi-tenancy notes

- Keep secrets in `tool_context` (never in `llm_context`), and never echo them into tool outputs.
- Scope memory and state by tenant/user/session.
- Treat resume tokens and session ids as sensitive control surfaces in applications.

## Runnable examples

Start with the minimal planner example (stubbed LLM, no API key needed):

```bash
uv run python examples/react_minimal/main.py
```

More: `examples/react_parallel/`, `examples/react_pause_resume/`, `examples/streaming_llm/stream_answer.py`, `examples/a2a_grpc_server/main.py`.

## Troubleshooting checklist

- **Need parallel tool calls + join**: see **[Parallel & joins](../planner/parallel-and-joins.md)**.
- **Need pause/resume / HITL**: see **[Pause/resume](../planner/pause-resume-hitl.md)**.
- **Need tool integrations**: use ToolNode and configure auth (see **[Tools configuration](../tools/configuration.md)**).
- **Need to expose the agent to other agents**: serve it with `penguiflow_a2a.A2AService`.
