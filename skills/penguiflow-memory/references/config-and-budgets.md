# `ShortTermMemoryConfig` and Friends

## `ShortTermMemoryConfig`

Top-level configuration passed to `ReactPlanner(short_term_memory=...)`.

```python
ShortTermMemoryConfig(
    strategy="none" | "truncation" | "rolling_summary",
    budget=MemoryBudget(...),
    isolation=MemoryIsolation(...),
    summarizer_model: str | None = None,
    include_trajectory_digest: bool = False,
    recovery_backlog_limit: int = ...,
    retry_attempts: int = ...,
    retry_backoff_base_s: float = ...,
    degraded_retry_interval_s: float = ...,
    token_estimator: Callable[[str], int] | None = None,
    on_turn_added: Callable[[ConversationTurn], Awaitable[None]] | None = None,
    on_summary_updated: Callable[[str, str], Awaitable[None]] | None = None,
    on_health_changed: Callable[[MemoryHealth, MemoryHealth], Awaitable[None]] | None = None,
)
```

### `strategy`

| Value | Behavior |
|---|---|
| `none` | No memory injected (default; effectively off). |
| `truncation` | Keep the last `full_zone_turns` turns; drop older. Deterministic, no LLM dependency. |
| `rolling_summary` | Keep recent turns + an LLM-generated rolling summary. Falls back to truncation if the summarizer is unhealthy. |

Pick `truncation` first. Switch to `rolling_summary` only after measuring prompt token usage and confirming a reliable summarizer model.

### `summarizer_model`
The LLM used to refresh the rolling summary. Use a cheap, fast model (`gpt-4.1-mini`, `claude-haiku`). Summarizer failures degrade health; persistent failures keep memory in `truncation` mode.

### `include_trajectory_digest`
When `True` (the default), every turn carries a compressed digest of tool usage and observations (`TrajectoryDigest`). Increases prompt size but lets the model reason about prior tool decisions. Set to `False` to trim prompts.

### `recovery_backlog_limit` (default 20), `retry_attempts` (default 3), `retry_backoff_base_s` (default 2.0), `degraded_retry_interval_s` (default 30.0)
Backoff knobs for summarizer recovery. Defaults are reasonable; tune only if you have visibility into summarizer error rates.

### `token_estimator`
Function `(str) -> int` used to estimate token counts for budget enforcement. Default uses a cheap heuristic. Provide a real tokenizer for accurate budgets.

### Hooks
- `on_turn_added(turn)` — called after a turn is appended.
- `on_summary_updated(old, new)` — called when the rolling summary changes.
- `on_health_changed(old, new)` — called when `MemoryHealth` transitions.

All hooks run **fire-and-forget** in background tasks. Exceptions are swallowed. Keep them lightweight (metrics, structured logging) — do not block on I/O. For durability, use a `StateStore`.

## `MemoryBudget`

```python
MemoryBudget(
    full_zone_turns: int,
    summary_max_tokens: int,
    total_max_tokens: int,
    overflow_policy: "truncate_summary" | "truncate_oldest" | "error",
)
```

| Field | Purpose |
|---|---|
| `full_zone_turns` | Number of most-recent turns kept as full messages. 3-8 typical. |
| `summary_max_tokens` | Max tokens for the rolling summary (rolling_summary strategy). |
| `total_max_tokens` | Hard cap on the entire memory payload. |
| `overflow_policy` | What to do when total exceeds the cap. |

### Overflow policies

- `truncate_oldest` (default for services) — Drop oldest turns first.
- `truncate_summary` — Shrink the rolling summary first (keeps its most recent part, below `summary_max_tokens` if needed), then drop the oldest turns. While `degraded` the summary isn't injected, so it is left intact.
- `error` — Raise `MemoryBudgetExceeded`. Useful for hard-bound test environments.

Production recommendation: `truncate_oldest`. `error` is for tests and special cases (e.g., billing-critical paths).

## `MemoryIsolation`

```python
MemoryIsolation(
    tenant_key: str = "tenant_id",
    user_key: str = "user_id",
    session_key: str = "session_id",
    require_explicit_key: bool = True,        # fail-closed by default
)
```

`tenant_key`/`user_key`/`session_key` are paths looked up in `tool_context`. The composite key is `f"{tenant}:{user}:{session}"` (also exposed via `MemoryKey.composite()`).

| `require_explicit_key` | Behavior |
|---|---|
| `True` (default) | Memory only activates if a key is resolvable (explicit or fully derived). Otherwise silently disabled. The library's fail-closed default. |
| `False` | Try to derive from `tool_context`; if missing, use an anonymous key. **Unsafe for multi-tenant** — only flip this for single-tenant prototypes. |

The fail-closed default is intentional. If you can't safely scope memory, don't use memory.

## `MemoryKey`

```python
MemoryKey(tenant_id: str, user_id: str, session_id: str)
```

The explicit form. Pass via `planner.run(..., memory_key=MemoryKey(...))`. This always wins over derived keys.

When to use explicit vs derived:
- **Explicit** — services, multi-tenant, anywhere a bug in `tool_context` could leak across tenants.
- **Derived** — single-tenant prototypes, scripts, demos.

## `ConversationTurn`

The atomic unit STM stores:

```python
ConversationTurn(
    user_message: str,
    assistant_response: str,
    trajectory_digest: TrajectoryDigest | None,
    artifacts_shown: dict[str, Any],
    artifacts_hidden_refs: list[str],
    ts: float,
)
```

`artifacts_shown` keeps inline artifact metadata visible to the LLM; `artifacts_hidden_refs` carries references to artifacts the user has interacted with but aren't included inline in the prompt.

## `TrajectoryDigest`

```python
TrajectoryDigest(
    tools_invoked: list[str],
    observations_summary: str,
    reasoning_summary: str | None,
    artifacts_refs: list[str],
)
```

A compressed record of what happened during a turn. Used only when `include_trajectory_digest=True`. Trade-off: more grounding for the model vs more tokens.

## What lands in `llm_context`

When memory is active, the planner adds:

```json
{
  "conversation_memory": {
    "recent_turns": [
      {
        "user": "user message",
        "assistant": "assistant response",
        "trajectory_digest": {
          "tools_invoked": ["tool.a", "tool.b"],
          "observations_summary": "...",
          "reasoning_summary": "..."
        }
      }
    ],
    "summary": "rolling summary text (rolling_summary, when healthy)",
    "pending_turns": ["raw turns awaiting summarization"]
  }
}
```

Shape varies by strategy and health. Inspect this in dev to verify what the model actually sees.

## Defaults summary

| Setting | Library default | Multi-tenant recommendation | Prototype |
|---|---|---|---|
| `strategy` | `"none"` (off) | `"truncation"` to start, `"rolling_summary"` later | `"truncation"` |
| `full_zone_turns` | `5` | 3-5 | 5-8 |
| `summary_max_tokens` | `1000` | 800-1000 | unchanged |
| `total_max_tokens` | `10000` | 8000 | 16000 |
| `overflow_policy` | `"truncate_oldest"` | `"truncate_oldest"` | `"truncate_oldest"` |
| `require_explicit_key` | `True` (fail-closed) | `True` | `False` if single-tenant |
| `include_trajectory_digest` | `True` | `False` for tight token budgets | unchanged |
| `summarizer_model` | `None` | `"gpt-4o-mini"` or similar | unchanged |
| `memory_key` source | n/a | Explicit `MemoryKey(...)` | Derived from `tool_context` |
