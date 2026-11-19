# Production deployment

## What it is / when to use it

This page is a **production hardening runbook** for PenguiFlow agents, focused on:

- bounded planner runs (budgets, timeouts, concurrency),
- correct multi-tenant scoping,
- durable pause/resume and state,
- the minimum observability you need on call.

## Non-goals / boundaries

- PenguiFlow does not include an official metrics exporter; you derive metrics from `PlannerEvent` and LLM telemetry.
- This page does not cover cloud-specific infrastructure.

## Contract surface (things you must configure deliberately)

### Planner limits

- `max_iters`, token/cost budgets and deadlines on `ReactPlanner` (see **[Configuration](../planner/configuration.md)**).
- Cancellation: pass a `SteeringInbox` as `steering=` and push a `CANCEL` event, or cancel the asyncio task running `planner.run`.

### Tool limits

- Per-source timeouts, retries and `max_concurrency` in `ToolNode` config (see **[Tools concurrency](../tools/concurrency-guide.md)**).

### Multi-tenant boundaries (recommended)

- Put `tenant`, `user_id` and `session_id` in `tool_context` on every run.
- Configure memory isolation so short-term memory is keyed by tenant/user/session (see **[Memory](../planner/memory.md)**).

### Durability / audit (optional, but common)

If you need pause/resume across processes, durable memory, session/task state or planner event replay, configure a `StateStore` (see **[State store](../tools/statestore.md)**).

### Tool execution (planner + ToolNode)

- Keep tool outputs bounded; use artifacts for large payloads.
- Mark side effects (`side_effects="read" | "write" | "external"`) so retries and parallel calls are safe.

## Operational defaults (starting point)

- `configure_logging(structured=True)` and an `event_callback` that forwards planner events.
- A shared `StateStore` in any deployment with more than one replica.
- Explicit budgets; never rely on defaults for paid LLM calls.

## Failure modes & recovery

### Symptom: requests hang

- Check the last `step_complete` / `tool_call_end` in logs for the run.
- Add tool timeouts; confirm the LLM client timeout is set.

### Symptom: high latency / timeouts

- Inspect tool latency histograms and LLM telemetry; reduce parallel fan-out or tighten budgets.

### Symptom: duplicate side effects (double writes)

- Retries or resume re-ran a write. Make the tool idempotent (idempotency keys) and mark it as a write.

### Symptom: cross-tenant leakage

- Memory or state keys aren't tenant-scoped. Pass `tenant`/`session_id` in `tool_context` and verify memory isolation paths.

## Observability (what to log + what to measure)

### Minimum production observability

- Planner `PlannerEvent` capture via `event_callback`.
- LLM telemetry hooks for latency, retries, tokens and cost.
- Task telemetry if you use sessions or background tasks.

See **[Metrics & alerts](../observability/metrics-and-alerts.md)**.

## Security / multi-tenancy notes

- Never log `tool_context` wholesale; it carries credentials and callbacks.
- Authenticate and authorize A2A endpoints in your app before they reach `A2AService`.

## Runnable example: "golden" service skeleton

Serve a planner over A2A HTTP inside a FastAPI app:

```python
from __future__ import annotations

from fastapi import FastAPI

from penguiflow import configure_logging
from penguiflow.planner import ReactPlanner
from penguiflow_a2a import A2AConfig, A2AService, install_a2a_http
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

configure_logging(structured=True)

planner = ReactPlanner(llm_client=..., catalog=..., max_iters=8)  # your client, tools and budgets

card = AgentCard(
    protocol_versions=["0.3"],
    name="Support Agent",
    description="Answers support questions.",
    supported_interfaces=[AgentInterface(url="https://agents.example.com/support", protocol_binding="HTTP+JSON")],
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=True, push_notifications=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[AgentSkill(id="support", name="Support", description="Support answers", tags=["support"])],
)

app = FastAPI()
install_a2a_http(app, A2AService(planner, agent_card=card, config=A2AConfig()))
```

## Troubleshooting checklist

- Need the deployment model: see **[Deployment](overview.md)**.
- Need a queue-worker pattern: see **[Worker integration](worker-integration.md)**.
