# Memory (short-term memory)

## What it is / when to use it

PenguiFlow includes an opt-in short-term memory (STM) layer used by `ReactPlanner` to preserve session continuity across runs and resumes.

Use STM when:

- a “session” spans multiple planner runs (or pause/resume cycles),
- you want the planner to remember recent turns and/or a rolling summary,
- you need explicit multi-tenant isolation and safe defaults.

STM is intentionally designed to be:

- **explicitly scoped** (via `MemoryKey` / `MemoryIsolation`)
- **fail-closed** (memory becomes effectively disabled when it cannot be scoped safely)
- **bounded** (token budgets with overflow policies)

!!! note
    STM is **off by default** and designed to be safe-by-default for multi-tenant environments.

## Non-goals / boundaries

- STM is not long-term memory or a knowledge base (no vector search, no durability guarantees unless you persist it).
- STM is not cross-tenant. If you do not provide a proper key, it should not “guess”.
- STM does not guarantee perfect recall; it is a bounded context aid.

## Contract surface

### Core types

STM configuration lives in `penguiflow.planner.memory`:

- `ShortTermMemoryConfig(strategy=..., budget=..., isolation=...)`
- `MemoryBudget(full_zone_turns, summary_max_tokens, total_max_tokens, overflow_policy)`
- `MemoryIsolation(tenant_key, user_key, session_key, require_explicit_key)`
- `MemoryKey(tenant_id, user_id, session_id)`

Planner integration points:

- `ReactPlanner(..., short_term_memory=ShortTermMemoryConfig(...))`
- `ReactPlanner.run(..., memory_key=MemoryKey(...))` (recommended for services)
- `ReactPlanner.run(..., tool_context=...)` (key can be derived if configured)

### LLM-visible shape

When memory is enabled, it patches `llm_context` with a JSON object under:

- `conversation_memory`

Typical shape (varies by strategy/health):

- `conversation_memory.recent_turns`: list of `{user, assistant, trajectory_digest?}`
- `conversation_memory.summary`: rolling summary string (rolling_summary only, when healthy)
- `conversation_memory.pending_turns`: buffered turns awaiting summarization (rolling_summary only)

### Optional persistence (StateStore capability)

If you use `DefaultShortTermMemory` (the built-in implementation), it can persist/hydrate via a `StateStore` that provides:

- `save_memory_state(key: str, state: dict)`
- `load_memory_state(key: str) -> dict | None`

See `penguiflow.state.protocol.SupportsMemoryState`.

!!! note
    These same methods are also used by the durable tool KV facade (`ctx.kv`) under a reserved keyspace prefix `kv:v1:`. If your StateStore implements STM persistence, it should accept arbitrary composite keys (not just `tenant:user:session`).

## What STM stores

At a high level STM maintains:

- recent turns (user → assistant),
- an optional rolling summary,
- an optional “trajectory digest” (compressed tool usage/observations) per turn.

## Operational defaults (enterprise-safe)

STM is designed to be **fail-closed** for multi-tenant environments. You can scope memory by an explicit `MemoryKey` or derive it from `tool_context` using configured `MemoryIsolation`.

Typical approaches:

- **Explicit key (recommended for services):** pass `memory_key=MemoryKey(...)` to planner calls.
- **Derived key:** configure `MemoryIsolation` to read keys out of `tool_context` (e.g. `tenant_id`, `user_id`, `session_id`).

Recommended defaults:

- `MemoryIsolation.require_explicit_key=True` for multi-tenant services
- small `full_zone_turns` (3–8) and bounded budgets
- `overflow_policy="truncate_oldest"` unless you need hard-fail behavior
- `include_trajectory_digest=True` only if you actually use it (it increases prompt size)

## Configuration

STM is enabled by passing a `ShortTermMemoryConfig` into `ReactPlanner(short_term_memory=...)`.

Example:

```python
from penguiflow.planner.memory import MemoryBudget, MemoryIsolation, ShortTermMemoryConfig

stm = ShortTermMemoryConfig(
    strategy="rolling_summary",
    budget=MemoryBudget(
        full_zone_turns=5,
        summary_max_tokens=1000,
        total_max_tokens=10000,
        overflow_policy="truncate_oldest",
    ),
    isolation=MemoryIsolation(
        tenant_key="tenant_id",
        user_key="user_id",
        session_key="session_id",
        require_explicit_key=True,
    ),
    summarizer_model="gpt-4.1-mini",
    include_trajectory_digest=True,
)
```

### Strategies

- `none`: no memory injected.
- `truncation`: keep the last `full_zone_turns` and drop older content.
- `rolling_summary`: keep recent turns + an LLM-maintained summary when healthy.

### Memory hooks (operational callbacks)

`ShortTermMemoryConfig` exposes optional async hooks you can use for metrics, audits, or external persistence coordination:

- `on_turn_added(turn: ConversationTurn) -> Awaitable[None]`
- `on_summary_updated(old: str, new: str) -> Awaitable[None]`
- `on_health_changed(old: MemoryHealth, new: MemoryHealth) -> Awaitable[None]`

Important semantics (production-critical):

- Hooks are executed **fire-and-forget** in background tasks (they do not block the planner run).
- Exceptions in hooks are swallowed intentionally. Treat hooks as best-effort.
- Hooks may run concurrently; they must be thread-safe/async-safe for your environment.

Example: emit metrics on memory health transitions

```python
from __future__ import annotations

from penguiflow.planner.memory import MemoryHealth


async def on_health_changed(old: MemoryHealth, new: MemoryHealth) -> None:
    # Replace with your metrics sink.
    print(f"stm_health: {old.value} -> {new.value}")
```

### Budgets and overflow

`MemoryBudget` enforces token caps. When exceeded:

- `truncate_oldest` removes older turns first (default).
- `truncate_summary` shrinks the rolling summary first.
- `error` raises `MemoryBudgetExceeded` (useful for hard-bound environments).

!!! warning
    Do not do long blocking I/O in hooks (e.g., synchronous DB writes). If you need durability, prefer a `StateStore`
    that supports memory hydration/persistence and keep hooks for lightweight side effects.

## Failure modes & recovery

### Memory “disabled unexpectedly”

**Symptoms**

- `ShortTermMemoryConfig` is configured, but `conversation_memory` is absent in `llm_context`

**Likely causes**

- `MemoryIsolation.require_explicit_key=True` and:
  - no `memory_key=...` was passed, and
  - key could not be derived from `tool_context`

**Fix**

- pass `memory_key=MemoryKey(...)` explicitly, or
- ensure `tool_context` contains the configured key paths (default: `tenant_id`, `user_id`, `session_id`)

### Rolling summary degrades

Rolling summaries depend on an LLM-backed summarizer. When it repeatedly fails, memory can enter degraded behavior (keeping only the recent “full zone” turns).

**Fix**

- set `ShortTermMemoryConfig.summarizer_model` to a more reliable/cheaper model
- reduce budget pressure and tool output sizes
- monitor `MemoryHealth` transitions (see Observability)

### Context becomes non-JSON-serializable

If the merged `llm_context` cannot be JSON-serialized, memory injection is skipped for safety.

**Fix**

- keep `llm_context` strictly JSON-friendly (no objects/functions)
- store opaque objects in `tool_context` instead

### Budget hard-fail (`MemoryBudgetExceeded`)

If you set `overflow_policy="error"`, adding turns can raise.

**Fix**

- prefer truncation overflow policies in user-facing services
- lower `include_trajectory_digest` or reduce tool output sizes

## Observability

STM exposes hooks in `ShortTermMemoryConfig` (async callbacks):

- `on_turn_added(turn)`
- `on_summary_updated(previous, new)`
- `on_health_changed(old, new)`

In addition, planner logs warnings when memory hydration/persistence or serialization fails.
In production, record at least:

- memory key presence rate (how often key resolution fails)
- summarizer error rate and health transitions
- estimated token size (`ShortTermMemory.estimate_tokens()`) by session

## Security / multi-tenancy notes

- Treat the memory key as the isolation boundary. Never share a `session_id` across tenants.
- Keep `require_explicit_key=True` for multi-tenant services unless you intentionally accept an ephemeral “anonymous” key.
- Do not store secrets in memory: anything in memory becomes LLM-visible as part of `llm_context`.

## Runnable example: explicit memory key

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.planner.memory import MemoryKey, ShortTermMemoryConfig


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    response: str


@tool(desc="Echo input", side_effects="pure")
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(response=args.text)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    planner = ReactPlanner(
        llm="gpt-4o-mini",
        catalog=catalog,
        short_term_memory=ShortTermMemoryConfig(strategy="truncation"),
    )

    key = MemoryKey(tenant_id="t1", user_id="u1", session_id="s1")
    await planner.run("Remember that my favorite color is teal.", memory_key=key, tool_context={"session_id": "s1"})
    result = await planner.run("What is my favorite color?", memory_key=key, tool_context={"session_id": "s1"})
    print(getattr(result, "payload", None))


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **Memory missing**: check key resolution; pass explicit `memory_key`.
- **Summary never appears**: confirm `strategy="rolling_summary"` and that the summarizer model is reachable.
- **Memory bloats prompts**: reduce `full_zone_turns` and disable trajectory digest, and move large tool outputs into artifacts.

## API reference

See **[Short-term memory API](../reference/short-term-memory-api.md)**.
