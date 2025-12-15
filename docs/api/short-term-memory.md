# Short-Term Memory API Reference

This document is a reference for PenguiFlow’s built-in short-term memory (STM) API used by `ReactPlanner`.

If you’re looking for practical usage and examples, start with:
- `docs/MEMORY_GUIDE.md`
- `docs/migration/MEMORY_ADOPTION.md`

---

## Import Paths

- Planner: `from penguiflow.planner import ReactPlanner`
- Memory types: `from penguiflow.planner.memory import ...`

---

## Core Types

### `ConversationTurn`

Atomic unit of STM. It represents a full user → assistant exchange.

Fields:
- `user_message: str`
- `assistant_response: str`
- `trajectory_digest: TrajectoryDigest | None`
- `artifacts_shown: dict[str, Any]`
- `artifacts_hidden_refs: list[str]`
- `ts: float`

### `TrajectoryDigest`

Compressed record of tool usage for a turn (optional). This is intended to preserve “what happened” without storing full tool payloads.

Fields:
- `tools_invoked: list[str]`
- `observations_summary: str`
- `reasoning_summary: str | None`
- `artifacts_refs: list[str]`

---

## Configuration

### `ShortTermMemoryConfig`

Top-level configuration passed into `ReactPlanner(short_term_memory=...)`.

Key fields:
- `strategy`: `"none" | "truncation" | "rolling_summary"`
- `budget`: `MemoryBudget`
- `isolation`: `MemoryIsolation`
- `summarizer_model: str | None`
- `include_trajectory_digest: bool`
- `recovery_backlog_limit: int`
- `retry_attempts: int`
- `retry_backoff_base_s: float`
- `degraded_retry_interval_s: float`
- `token_estimator: Callable[[str], int] | None`

Callbacks (best-effort, non-blocking):
- `on_turn_added(turn)`
- `on_summary_updated(old, new)`
- `on_health_changed(old, new)`

### `MemoryBudget`

Controls how much STM content is retained and what happens under overflow.

- `full_zone_turns`: number of recent turns kept as full messages
- `summary_max_tokens`: max tokens allowed for the rolling summary
- `total_max_tokens`: overall cap for the memory payload
- `overflow_policy`: `"truncate_summary" | "truncate_oldest" | "error"`

### `MemoryIsolation`

Defines how to derive a session key from `tool_context`.

- `tenant_key`, `user_key`, `session_key`: dotted paths looked up in `tool_context`
- `require_explicit_key`: if `True`, STM is fail-closed when no key can be resolved

### `MemoryKey`

Explicit composite key used for STM isolation:

- `tenant_id`
- `user_id`
- `session_id`

`MemoryKey.composite()` returns the storage key format: `"{tenant}:{user}:{session}"`.

---

## Public Protocols

### `ShortTermMemory`

Minimal protocol required by `ReactPlanner`:

- `health -> MemoryHealth`
- `add_turn(turn) -> Awaitable[None]`
- `get_llm_context() -> Awaitable[Mapping[str, Any]]`
- `estimate_tokens() -> int`
- `flush() -> Awaitable[None]`

Optional extensions (duck-typed):

- `persist(store, key)`: called when the planner finishes successfully
- `hydrate(store, key)`: called before injecting memory
- `to_dict()/from_dict(state)`: for custom persistence backends
- `get_artifact(ref)`: for artifact lookups (optional)

---

## Default Implementation

### `DefaultShortTermMemory`

In-memory implementation that supports:

- truncation strategy
- rolling summary strategy (background LLM summarization when wired by `ReactPlanner`)
- failure → retry → degraded health transitions
- optional persistence via `save_memory_state/load_memory_state` on the store

The planner will create a `DefaultShortTermMemory` per `MemoryKey` when configured with `ShortTermMemoryConfig`.

Note:
- If you instantiate `DefaultShortTermMemory` directly with `strategy="rolling_summary"`, you must provide a `summarizer` callable.

---

## What Gets Injected Into `llm_context`

STM injects a JSON-friendly patch into `llm_context`:

```json
{
  "conversation_memory": {
    "recent_turns": [{"user": "...", "assistant": "..."}],
    "pending_turns": [{"user": "...", "assistant": "..."}],
    "summary": "<session_summary>...</session_summary>"
  }
}
```

Notes:
- `pending_turns` and `summary` are only present for `rolling_summary` when healthy.
- In degraded mode, STM falls back to `recent_turns` only.

---

## ReactPlanner Integration Surface

### `ReactPlanner(..., short_term_memory=...)`

Accepts either:
- `ShortTermMemoryConfig` (recommended)
- a custom `ShortTermMemory` implementation

### `ReactPlanner.run(..., memory_key=...)` and `ReactPlanner.resume(..., memory_key=...)`

- If `memory_key` is provided, STM is scoped to it.
- If omitted, the planner may derive it from `tool_context` using `MemoryIsolation`.
- If no key is available and `require_explicit_key=True`, STM behaves as disabled for that call.

---

## Errors

### `MemoryBudgetExceeded`

Raised when budgets overflow and `overflow_policy="error"`.

This is intended for “hard bound” environments where exceeding budgets is an operational error rather than a truncation signal.
