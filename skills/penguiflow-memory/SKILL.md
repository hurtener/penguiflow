---
name: penguiflow-memory
description: Add short-term conversation memory to a PenguiFlow ReactPlanner — configure `ShortTermMemoryConfig` with strategy (`none`/`truncation`/`rolling_summary`), `MemoryBudget` token caps + overflow policy, `MemoryIsolation` for multi-tenant fail-closed scoping via `MemoryKey(tenant_id, user_id, session_id)`, optionally persist via `StateStore.save_memory_state`/`load_memory_state`, wire `on_turn_added`/`on_summary_updated`/`on_health_changed` hooks, and tune `summarizer_model`/`include_trajectory_digest`. Use when a user says "add memory", "session memory", "conversation memory", "multi-tenant memory", "rolling summary", or names `ShortTermMemoryConfig`, `MemoryKey`, `MemoryIsolation`.
---

# PenguiFlow Short-Term Memory (Planner STM)

## When to use
- Sessions span multiple `planner.run(...)` calls.
- You want the planner to remember recent turns and/or a rolling summary.
- Multi-tenant: you need explicit isolation and fail-closed defaults.
- Pause/resume cycles where context must survive.

## When NOT to use
- Long-term memory or knowledge bases (vector DBs, retrieval) → not in scope; build it separately.
- Per-trace observability/audit (use [[penguiflow-statestore]] for events/trajectories).
- Cross-session global state (use your app's DB).
- HITL pause state durability → [[penguiflow-hitl-pause-resume]] (memory persistence and pause persistence are distinct StateStore capabilities).

## Hard boundaries
STM is a **bounded prompt-injection layer**. It writes recent turns and an optional rolling summary into `llm_context.conversation_memory`. It is not a vector store, not a knowledge base, not a long-term cache. Anything in STM becomes LLM-visible — never store secrets.

## Workflow

### 1) Decide a strategy
| Strategy | Behavior |
|---|---|
| `none` | No memory injected (default; memory off). |
| `truncation` | Keep the last `full_zone_turns` turns; drop older. |
| `rolling_summary` | Keep recent turns + an LLM-maintained rolling summary; falls back to truncation if the summarizer is unhealthy. |

Start with `truncation` for new agents. Switch to `rolling_summary` only after measuring prompt token usage and confirming you have a reliable summarizer model.

### 2) Build `ShortTermMemoryConfig`
```python
from penguiflow.planner.memory import (
    MemoryBudget, MemoryIsolation, ShortTermMemoryConfig,
)

stm = ShortTermMemoryConfig(
    strategy="rolling_summary",              # default is "none" — must opt in
    budget=MemoryBudget(
        full_zone_turns=5,
        summary_max_tokens=1000,
        total_max_tokens=10000,
        overflow_policy="truncate_oldest",   # truncate_summary | truncate_oldest | error
    ),
    isolation=MemoryIsolation(
        tenant_key="tenant_id",
        user_key="user_id",
        session_key="session_id",
        require_explicit_key=True,           # library default; keep True in multi-tenant
    ),
    summarizer_model="gpt-4.1-mini",          # default None
    include_trajectory_digest=True,           # this is the library default
)
```

Full field reference in `references/config-and-budgets.md`.

### 3) Attach to `ReactPlanner`
```python
planner = ReactPlanner(
    llm="gpt-4o-mini",
    catalog=catalog,
    short_term_memory=stm,
)
```

### 4) Scope every call with a `MemoryKey`
```python
from penguiflow.planner.memory import MemoryKey

key = MemoryKey(tenant_id="t1", user_id="u1", session_id="s1")

await planner.run(
    "Remember my favorite color is teal.",
    memory_key=key,
    tool_context={"session_id": "s1"},
)
```

Two ways to supply the key:
- **Explicit** — pass `memory_key=MemoryKey(...)` on every `run()`/`resume()`. **Recommended for services.**
- **Derived** — configure `MemoryIsolation` dotted paths; the key is read from `tool_context`. Useful for shorthand but explicit is safer.

If `require_explicit_key=True` and no key can be resolved, memory is silently disabled for that call (fail-closed). Check key resolution rate in observability.

### 5) (Optional) Persist memory across processes
For deployments with multiple workers / restarts, attach a `StateStore` implementing `SupportsMemoryState`:
- `save_memory_state(key: str, state: dict) -> None`
- `load_memory_state(key: str) -> dict | None`

Hydration happens on `run()` start; persistence happens after turns are added and, for `rolling_summary`, again when background summarization finishes. See [[penguiflow-statestore]] for backends. The same StateStore methods also back `ctx.kv` under reserved prefix `kv:v1:` — implementations must accept arbitrary composite keys, not only `tenant:user:session`.

### 6) (Optional) Wire hooks for metrics
`ShortTermMemoryConfig` accepts `on_turn_added(turn)`, `on_summary_updated(old, new)`, `on_health_changed(old, new)` — all async, **fire-and-forget**. Exceptions are swallowed. Use for lightweight metrics/logs only; durability goes through `StateStore`. Full signatures in `references/persistence-and-hooks.md`.

### 7) Observe what the LLM sees
When STM is active, `llm_context` gains `conversation_memory` with `recent_turns`, optional `summary`, optional `pending_turns`. Inspect in dev to verify; if missing, key resolution failed.

## Troubleshooting (fast checks)
- **`conversation_memory` absent** — key resolution failed; pass `memory_key=MemoryKey(...)` explicitly or ensure `tool_context` carries the configured keys.
- **Memory bloats prompts** — lower `full_zone_turns` (3-5), set `include_trajectory_digest=False`, move large tool outputs to artifacts.
- **Rolling summary never appears** — confirm `strategy="rolling_summary"` and `summarizer_model` is reachable; watch for `MemoryHealth` transitions to `degraded`.
- **`MemoryBudgetExceeded`** — `overflow_policy="error"`; use `truncate_oldest` for user-facing services.
- **Cross-session leak** — `session_id` reused across users; sessions are **not** tenant boundaries — always set `tenant_id`.
- **`conversation_memory` missing despite `require_explicit_key=False`** — `llm_context` wasn't JSON-serializable; memory injection skips for safety.
- **Hooks not firing** — exceptions are swallowed; add logging inside hook bodies.
- **Memory doesn't survive restart** — attach a `StateStore` with `save_memory_state`/`load_memory_state`.

## Worked examples
- `examples/memory_basic/flow.py`, `examples/memory_callbacks/flow.py`, `examples/memory_truncation/flow.py`, `examples/memory_custom/flow.py`, `examples/memory_persistence/flow.py`, `examples/memory_redis/flow.py`, `examples/react_memory_context/flow.py`.

## References (load only as needed)
- `references/config-and-budgets.md` — `ShortTermMemoryConfig`, `MemoryBudget`, `MemoryIsolation`, `MemoryKey`, every field.
- `references/strategies-and-health.md` — `truncation` vs `rolling_summary`, `MemoryHealth` lifecycle, summarizer reliability, trajectory digests.
- `references/persistence-and-hooks.md` — `StateStore` capability, key composition rules, hook semantics, custom memory backends.
