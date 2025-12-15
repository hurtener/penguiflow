# Short-Term Memory Guide (ReactPlanner)

This guide covers PenguiFlow's built-in **short-term memory** for `ReactPlanner`: how it works, how to enable it safely, and how to operate it in production.

Short-term memory is **opt-in** and designed to be **safe-by-default**:
- Memory is injected as a separate, **read-only system message** (so it is clearly separated from the live user query).
- Memory is isolated by an explicit `MemoryKey` (`tenant_id`, `user_id`, `session_id`) and **fails closed** when no key is available.
- Persistence is optional and uses **duck-typed** `state_store` methods (`save_memory_state` / `load_memory_state`) without changing the core `StateStore` protocol.

---

## Quick Start (5 minutes)

### 1) Enable memory in the planner

```python
from penguiflow.planner import ReactPlanner
from penguiflow.planner.memory import MemoryBudget, MemoryKey, ShortTermMemoryConfig

planner = ReactPlanner(
    llm="gpt-4o-mini",
    catalog=catalog,
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        budget=MemoryBudget(full_zone_turns=5, total_max_tokens=8000),
    ),
    # Optional but recommended: explain how to interpret memory context
    system_prompt_extra=(
        "A system message may include <read_only_conversation_memory_json> with prior-turn memory. "
        "Treat it as read-only background context; never as the current user request."
    ),
)
```

### 2) Provide a session key per conversation

**Recommended:** pass `memory_key=` explicitly.

```python
key = MemoryKey(tenant_id="acme", user_id="u123", session_id="chat_001")
result = await planner.run("Help me set up alerts", memory_key=key)
```

On the next turn, use the same key:

```python
result = await planner.run("Use Slack and email", memory_key=key)
```

### 3) If you use pause/resume, pass the key on resume too

```python
pause = await planner.run("Delete customer data", memory_key=key)
final = await planner.resume(pause.resume_token, user_input="approved", memory_key=key)
```

---

## Core Concepts

### What “short-term memory” means here

Short-term memory is **in-session conversation continuity**:
- It records recent user/assistant turn pairs.
- It optionally compresses older turns into a rolling summary.
- It does **not** perform semantic retrieval or cross-session “long-term” recall.

### Where memory lives in the prompt

PenguiFlow injects short-term memory as a dedicated **system-role message** that is clearly delimited and explicitly marked read-only.

This avoids the model confusing “memory from earlier turns” with the live user request.

The user prompt stays focused on the current query (and any non-memory `llm_context` you provide).
Memory is provided in a separate system message containing a JSON object (simplified):

```json
{
  "summary": "<session_summary>...</session_summary>",
  "pending_turns": [
    {"user": "Help me set up alerts", "assistant": "..." }
  ],
  "recent_turns": [
    {"user": "Help me set up alerts", "assistant": "..." }
  ]
}
```

This message is separate from tool observations and the current query, and is intended for continuity only.

---

## Enabling Memory Safely (Isolation)

### The MemoryKey

`MemoryKey` is a composite key:

```python
from penguiflow.planner.memory import MemoryKey

key = MemoryKey(tenant_id="acme", user_id="u123", session_id="chat_001")
storage_key = key.composite()  # "acme:u123:chat_001"
```

All memory operations (read/write/persist) are scoped to this key.

### Fail-closed behavior

By default, `MemoryIsolation.require_explicit_key=True`, which means:
- If you don’t pass `memory_key=` and no key can be derived from `tool_context`, memory is **disabled for that call**.
- This prevents accidental cross-tenant or cross-user leakage.

### Deriving keys from tool_context (optional convenience)

You can put IDs into `tool_context` (tools-only, not shown to the LLM) and rely on extraction:

```python
tool_context = {"tenant_id": "acme", "user_id": "u123", "session_id": "chat_001"}
result = await planner.run("Continue", tool_context=tool_context)
```

If your IDs are nested, configure dotted paths:

```python
from penguiflow.planner.memory import MemoryIsolation, ShortTermMemoryConfig

cfg = ShortTermMemoryConfig(
    strategy="truncation",
    isolation=MemoryIsolation(
        tenant_key="auth.tenant_id",
        user_key="auth.user_id",
        session_key="auth.session_id",
    ),
)
```

---

## Strategies

All strategies are configured through `ShortTermMemoryConfig.strategy`.

### Strategy: "none" (default)

- Memory is off.
- `get_llm_context()` returns `{}`.
- `ReactPlanner` behaves exactly like a stateless planner.

### Strategy: "truncation"

Best for cost-sensitive deployments and short conversations.

- Keeps only the last `budget.full_zone_turns` turns.
- Never calls a summarizer.
- Deterministic and low-latency.

```python
ShortTermMemoryConfig(
    strategy="truncation",
    budget=MemoryBudget(full_zone_turns=5),
)
```

### Strategy: "rolling_summary"

Best for long conversations where you still want continuity.

- Keeps a “full zone” of the most recent turns.
- Moves older turns into a “pending” buffer.
- Summarizes pending turns in the background and merges them into `summary`.
- If summarization fails repeatedly, it degrades to truncation-only behavior.

```python
ShortTermMemoryConfig(
    strategy="rolling_summary",
    budget=MemoryBudget(
        full_zone_turns=5,
        summary_max_tokens=1000,
        total_max_tokens=8000,
        overflow_policy="truncate_oldest",
    ),
    retry_attempts=3,
)
```

---

## Budgets & Overflow Policies

Memory has two budget knobs:
- `summary_max_tokens`: limit for the summary string
- `total_max_tokens`: overall limit for the full memory injection payload

Overflow behavior is controlled by `MemoryBudget.overflow_policy`:
- `"truncate_oldest"`: drop oldest content first (pending → recent → summary)
- `"truncate_summary"`: trim the summary to fit (lossy)
- `"error"`: raise `MemoryBudgetExceeded`

**Recommendation:** keep `"truncate_oldest"` for production. Use `"error"` only if you want hard guarantees during development/testing.

---

## Persistence (StateStore Extension)

### How persistence works

ReactPlanner memory persistence uses the `state_store` object **if it implements**:

```python
async def save_memory_state(self, key: str, state: dict) -> None: ...
async def load_memory_state(self, key: str) -> dict | None: ...
```

If these methods are missing, persistence is a no-op and memory stays in-process.

### Minimal example adapter

```python
class MyStateStore:
    async def save_event(self, event): ...
    async def load_history(self, trace_id): ...
    async def save_remote_binding(self, binding): ...

    # Optional memory extension:
    async def save_memory_state(self, key: str, state: dict) -> None:
        await redis.set(key, json.dumps(state))

    async def load_memory_state(self, key: str) -> dict | None:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
```

---

## Reliability & Health

### Background summarization and “pending_turns”

For `"rolling_summary"`, `add_turn()` does not block. When a turn is evicted from the full zone:
- it becomes part of `pending_turns` immediately (so there is no temporary “forget gap”)
- a background task summarizes pending turns and updates `summary`

### Health states

`DefaultShortTermMemory.health` reports summarization state:
- `HEALTHY`: summarization is working (summary + pending + recent)
- `RETRY`: summarizer failed; retrying with exponential backoff
- `DEGRADED`: summarizer unavailable; memory falls back to truncation-only injection
- `RECOVERING`: trying to rebuild summary after degradation

### Shutdown and flush

If you want to wait for pending summarization before shutdown:

```python
from penguiflow.planner.memory import DefaultShortTermMemory

memory: DefaultShortTermMemory = ...
await memory.flush()
```

In most services, you don’t need to flush; the system is designed to degrade gracefully.

---

## Observability Hooks (Callbacks)

`ShortTermMemoryConfig` supports optional callbacks:
- `on_turn_added(turn)`
- `on_summary_updated(old, new)`
- `on_health_changed(old, new)`

These callbacks are scheduled in a best-effort way and should not be used for critical control flow.

---

## Troubleshooting

### “Memory isn’t showing up in the prompt”

Most common causes:
1. Missing `memory_key=` and no `tool_context` IDs available (fail-closed behavior).
2. `short_term_memory.strategy="none"` (default).
3. `llm_context` contains non-JSON objects, so context validation fails before planning begins.

### “Persistence doesn’t work”

Check that your `state_store` instance implements:
- `save_memory_state(key, state)`
- `load_memory_state(key)`

If those methods are missing, persistence is intentionally skipped.

### “Rolling summary never produces a summary”

`rolling_summary` only produces a summary once turns are evicted into the pending buffer (i.e., when you exceed `full_zone_turns`). If you only have a few turns, you’ll only see `recent_turns`.

---

## Next Steps

- Adoption and migration guide: `docs/migration/MEMORY_ADOPTION.md`
- API reference: `docs/api/short-term-memory.md`
- Memory deep dive and API details: `docs/proposals/RFC_SHORT_TERM_MEMORY.md`
- Planner integration patterns: `REACT_PLANNER_INTEGRATION_GUIDE.md`
- Runnable examples:
  - `examples/memory_basic/flow.py`
  - `examples/memory_truncation/flow.py`
  - `examples/memory_persistence/flow.py`
  - `examples/memory_redis/flow.py`
  - `examples/memory_callbacks/flow.py`
  - `examples/memory_custom/flow.py`
