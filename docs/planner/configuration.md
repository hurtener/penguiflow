# ReactPlanner configuration (production patterns)

## What it is / when to use it

This page is a **configuration playbook** for `ReactPlanner` in production services.

Use it when you need:

- a safe “default” configuration you can ship,
- concrete examples for multi-tenant memory + guardrails + steering,
- background task wiring (tasks.* tools and tool-initiated background jobs),
- a clear contract for what goes into `llm_context` vs `tool_context`.

## Non-goals / boundaries

- This page is not a full API reference for every argument on `ReactPlanner`.
- This page does not document ToolNode transports in depth (see **[Tooling](tooling.md)**).
- This page does not define a single “best” architecture; it gives **known-good starting points**.

## Contract surface

### Planner construction vs per-call inputs

**Construction time** (static policy + defaults):

- LLM integration: `llm="..."` / `llm_client=...` / `use_native_llm=True`
- tool catalog: `catalog=[NodeSpec, ...]` (or `nodes=[Node, ...]` + `registry=ModelRegistry`)
- budgets: `max_iters`, `deadline_s`, `hop_budget`, `token_budget`
- safety: `tool_policy`, `guardrail_gateway`, `observation_guardrail`
- memory: `short_term_memory=ShortTermMemoryConfig(...)`
- background tasks: `background_tasks=BackgroundTasksConfig(...)`
- observability: `event_callback`, `stream_final_response`

**Per call** (tenant/session scoped):

- `query`
- `llm_context`: JSON-only, LLM-visible context
- `tool_context`: privileged tool runtime context (clients/secrets/callbacks)
- `memory_key`: explicit memory isolation key (`MemoryKey`)
- `tool_visibility`: dynamic per-call tool filtering (multi-tenant allowlists)
- `steering`: runtime control plane (`SteeringInbox`)

!!! warning
    Treat `llm_context` and `tool_context` as a security boundary:
    secrets and privileged objects belong in `tool_context` only.

### Required `tool_context` keys (common patterns)

There is no single required key, but many subsystems use conventional keys:

- `session_id: str` — enables per-session dispatch (planner concurrency hotfix) and is required by tasks.* tools.
- `tenant_id/user_id: str` — used by memory isolation when deriving keys.
- `task_service: TaskService` — required for tasks.* tools and tool-initiated background spawns.

## Operational defaults (enterprise-safe baseline)

These defaults are intended to be safe in multi-tenant services:

- `temperature=0.0`, `json_schema_mode=True`
- `max_iters=8` (lower if you have strict latency SLOs)
- `deadline_s` set per request (or set on the planner for a global ceiling)
- `llm_timeout_s` aggressively bounded; keep `llm_max_retries` small
- `tool_policy` deny-by-default for write/external tools unless explicitly enabled per tenant
- memory isolation: pass `memory_key=MemoryKey(...)` explicitly per call
- observation clamp: keep `ObservationGuardrailConfig()` enabled (default)

## Configuration recipes

### 1) Minimal “local” planner (no memory, no steering)

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ToolContext


class EchoArgs(BaseModel):
    payload: dict


class EchoOut(BaseModel):
    payload: dict


@tool(desc="Echo input", side_effects="pure")
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(payload=args.payload)


def build_planner() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    return ReactPlanner(llm="gpt-4o-mini", catalog=catalog)
```

### 2) Multi-tenant service baseline (memory + strict budgets)

This pattern:

- uses **explicit memory keys** (fail-closed isolation),
- enforces budgets and timeouts,
- keeps secrets out of `llm_context`,
- emits events for observability.

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import MemoryKey, PlannerEvent, ReactPlanner, ToolContext
from penguiflow.planner.memory import MemoryBudget, MemoryIsolation, ShortTermMemoryConfig
from penguiflow.planner.models import ObservationGuardrailConfig, ToolPolicy


class GetStatusArgs(BaseModel):
    pass


class GetStatusOut(BaseModel):
    ok: bool


@tool(desc="Read-only demo tool", side_effects="read", tags=["read"])
async def get_status(args: GetStatusArgs, ctx: ToolContext) -> GetStatusOut:
    del args, ctx
    return GetStatusOut(ok=True)


def build_planner() -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("get_status", GetStatusArgs, GetStatusOut)
    catalog = build_catalog([Node(get_status, name="get_status")], registry)

    stm = ShortTermMemoryConfig(
        strategy="rolling_summary",
        budget=MemoryBudget(full_zone_turns=5, summary_max_tokens=800, total_max_tokens=8000),
        isolation=MemoryIsolation(require_explicit_key=True),
        summarizer_model="gpt-4.1-mini",
    )

    def on_event(e: PlannerEvent) -> None:
        # Ship this to your structured logger / traces.
        _ = e.to_payload()

    return ReactPlanner(
        llm="gpt-4o-mini",
        catalog=catalog,
        short_term_memory=stm,
        observation_guardrail=ObservationGuardrailConfig(
            max_observation_chars=50_000,
            auto_artifact_threshold=20_000,
        ),
        tool_policy=ToolPolicy(
            allowed_tools={"get_status"},
        ),
        temperature=0.0,
        json_schema_mode=True,
        max_iters=8,
        llm_timeout_s=45.0,
        llm_max_retries=2,
        event_callback=on_event,
    )


async def handle_request(
    planner: ReactPlanner,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    query: str,
    tool_context: Mapping[str, Any],
) -> Any:
    # Secrets/clients live in tool_context; never in llm_context.
    result = await planner.run(
        query,
        tool_context={**dict(tool_context), "session_id": session_id},
        memory_key=MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=session_id),
        llm_context={"ui": {"locale": "en-US"}},
    )
    return result
```

### 3) Native LLM layer + guardrails (recommended for “real” production)

Use this when you want:

- more predictable structured output handling across providers,
- native reasoning streaming (where supported),
- a centralized guardrail policy pack.

This example shows end-to-end wiring for:

- native LLM (`use_native_llm=True`)
- a minimal guardrail gateway (tool allowlist + secret redaction)
- background task tools (tasks.*) in the catalog
- a steering inbox attached per call

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.planner.guardrails import (
    GatewayConfig,
    GuardrailGateway,
    RuleRegistry,
    SecretRedactionRule,
    ToolAllowlistRule,
)
from penguiflow.planner.guardrails.async_eval import AsyncRuleEvaluator
from penguiflow.planner.models import BackgroundTasksConfig
from penguiflow.sessions.task_tools import build_task_tool_specs
from penguiflow.steering import SteeringInbox
from penguiflow.steering.guard_inbox import InMemoryGuardInbox


class PingArgs(BaseModel):
    pass


class PingOut(BaseModel):
    ok: bool


@tool(desc="Health check", side_effects="read", tags=["read"])
async def ping(args: PingArgs, ctx: ToolContext) -> PingOut:
    del args, ctx
    return PingOut(ok=True)


def build_enterprise_planner() -> ReactPlanner:
    # App tools
    registry = ModelRegistry()
    registry.register("ping", PingArgs, PingOut)
    app_catalog = build_catalog([Node(ping, name="ping")], registry)

    # Background task meta-tools (tasks.*)
    task_catalog = build_task_tool_specs()

    # Guardrails (minimal policy pack)
    rules = RuleRegistry()
    rules.register(
        ToolAllowlistRule(
            allowed_tools=frozenset(
                {
                    "ping",
                    # tasks.* tools (if you include them in the catalog)
                    "tasks.spawn",
                    "tasks.list",
                    "tasks.get",
                    "tasks.cancel",
                    "tasks.apply_patch",
                }
            )
        )
    )
    rules.register(SecretRedactionRule())
    inbox = InMemoryGuardInbox(AsyncRuleEvaluator(rules))
    gateway = GuardrailGateway(
        registry=rules,
        guard_inbox=inbox,
        config=GatewayConfig(mode="enforce"),
    )

    return ReactPlanner(
        llm={"model": "openai/gpt-4o-mini"},
        use_native_llm=True,
        catalog=[*task_catalog, *app_catalog],
        background_tasks=BackgroundTasksConfig(enabled=True),
        guardrail_gateway=gateway,
        stream_final_response=True,
    )


async def handle_interactive_request(planner: ReactPlanner) -> None:
    steering = SteeringInbox()
    # Provide a TaskService implementation via tool_context["task_service"] in real deployments.
    await planner.run(
        "Check health; if slow, spawn in background.",
        tool_context={"session_id": "sess_123"},
        steering=steering,
    )
```

See **[Native LLM layer](native-llm.md)**, **[Guardrails](guardrails.md)**, **[Steering](steering.md)**, and **[Background tasks](background-tasks.md)** for operational guidance and failure modes.

## Failure modes & recovery

### “Everything is serialized” (low throughput)

**Symptoms**

- concurrent requests stall behind each other even for different users

**Likely cause**

- no `session_id` is present, so planner falls back to a global lock to protect internal mutable state

**Fix**

- pass `tool_context["session_id"]` (or use `MemoryKey.session_id`) for every call.

### Memory configured but never appears

See **[Memory](memory.md)** (key derivation vs explicit keys, health states, budgets).

### Background tasks don’t spawn

See **[Background tasks](background-tasks.md)** (requires `task_service` in `tool_context` and `BackgroundTasksConfig.enabled=True`).

## Observability

At minimum, record:

- `PlannerEvent.event_type` and `latency_ms` (LLM and tools),
- finish reasons (`answer_complete` / `no_path` / `budget_exhausted`),
- pause/resume and guardrail decisions (redacted).

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Prefer per-tenant `tool_visibility` / `tool_policy` instead of stuffing “available tools” into the prompt.
- Treat `resume_token` and any steering control messages as authorization capabilities.
- Use guardrails to prevent secret leakage in streamed output (see **[Guardrails](guardrails.md)**).

## Runnable examples

- Guardrails examples: `uv run python examples/guardrails/huggingface/flow.py`
- ToolNode integrations: see `examples/` (each example is runnable via `uv run python ...`)

## Troubleshooting checklist

- Is `tool_context["session_id"]` set for every call?
- Are you keeping `llm_context` JSON-only?
- Are memory keys explicit (`memory_key=MemoryKey(...)`) in multi-tenant services?
- Are `event_callback` logs being recorded and searchable?
- If using tasks.* tools, is `tool_context["task_service"]` configured?
