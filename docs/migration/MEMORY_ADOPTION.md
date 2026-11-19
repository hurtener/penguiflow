# Memory Adoption Guide (Short-Term Memory)

This guide helps you adopt PenguiFlow’s built-in short-term memory (STM) for `ReactPlanner`, and (optionally) decide how it coexists with an external memory service.

STM is **opt-in**, **async-first**, and **safe-by-default** for multi-tenant systems.

For the full STM feature guide, see `docs/MEMORY_GUIDE.md`.

---

## Who This Is For

- You have an agent built with `ReactPlanner` and you want **conversation continuity** across turns.
- You want a memory solution that does **not** require a separate service.
- You want to keep tenant/user/session isolation explicit and avoid “memory leakage” between sessions.

If you want retrieval-augmented “long-term” memory (vector search / knowledge base), STM is not a replacement; STM stores **recent conversation context** for a session.

---

## Mental Model

PenguiFlow distinguishes:

- `llm_context`: **LLM-visible** context (should not contain tenant/user identifiers).
- `tool_context`: **runtime-only** context (safe place for `tenant_id`, `user_id`, `session_id`, DB handles, etc.).

STM integrates by:

1. Building a `MemoryKey(tenant_id, user_id, session_id)` from `memory_key=` or from `tool_context`.
2. Injecting a `conversation_memory` block into `llm_context` for the next run.
3. Persisting/hydrating memory state via optional duck-typed `state_store` methods.

---

## Quick Start (Existing Codebase)

### 1) Pick a strategy

- `rolling_summary`: best default for longer sessions (keeps recent turns + a summary).
- `truncation`: cheapest (keeps last N turns only).
- `none`: disables STM.

### 2) Enable STM on the planner

In your planner construction:

- Create a `ShortTermMemoryConfig`
- Pass it as `short_term_memory=...` when creating `ReactPlanner`

### 3) Pass a session key safely (required by default)

STM is **fail-closed** by default: if no key can be resolved, STM behaves as disabled for that call.

Recommended: pass identifiers via `tool_context`:

- `tenant_id`
- `user_id`
- `session_id`

---

## Step-by-Step Migration: Stateless → STM

This path keeps behavior changes controlled and debuggable.

### Step A: Keep default “stateless” behavior in production

- Deploy STM code paths but keep `strategy="none"` (or omit STM config).
- Validate nothing changes.

### Step B: Enable truncation with small limits

- Start with `strategy="truncation"`
- Set `full_zone_turns=2..5`
- Keep `total_max_tokens` conservative

Goal: confirm keying and isolation correctness with minimal cost.

### Step C: Switch to rolling summary

- Change to `strategy="rolling_summary"`
- Confirm summary updates don’t add latency (summarization runs in the background).

### Step D (optional): Add persistence

If you run multiple workers (or you want session continuity across restarts), add a store:

- Provide `state_store` to the planner
- Implement optional duck-typed methods:
  - `save_memory_state(key: str, state: dict) -> Awaitable[None]`
  - `load_memory_state(key: str) -> Awaitable[dict | None]`

The planner will call memory’s duck-typed `persist/hydrate` if present.

---

## STM and an External Memory Service

If your app wires a client for an external memory service but you do **not** run that service:

### Option 1 (recommended): Keep retrieval memory off, use only STM

1. Remove the external memory client wiring in code.
2. Remove prompt instructions that are tied to the external service.
3. Enable built-in STM via `short_term_memory=...` on `ReactPlanner`.

This yields a clean separation:

- STM: short-term conversation continuity
- External retrieval: disabled

### Option 2: Keep both (STM + external retrieval)

This is valid if you want:

- External memory service for long-term retrieval/snippets, AND
- Built-in STM for session continuity and summarization

In this mode:

- External retrieval snippets can remain in `llm_context` under `external_memory` (developer-owned shape)
- STM injects its own `conversation_memory` block

The two can coexist as long as you keep identifiers in `tool_context`.

---

## Common Pitfalls

### “STM does nothing”

Most common cause: no session key available.

Fix:

- Ensure `tool_context` includes `session_id` (and optionally `tenant_id`, `user_id`).
- Or pass `memory_key=` explicitly to `planner.run()`/`planner.resume()`.

### “Context leaked between users”

Cause: keying is shared or `require_explicit_key=false` in a multi-tenant setup.

Fix:

- Keep `require_explicit_key=true` unless you have a single-tenant demo environment.
- Ensure per-request `tool_context` carries correct identifiers.

### “Token usage grew too much”

Fix:

- Use smaller `full_zone_turns`
- Lower `total_max_tokens`
- Consider `truncation` strategy
- Keep `include_trajectory_digest=false` if tool digests are too verbose for your domain

---

## Performance Tuning Checklist

- Prefer `rolling_summary` when sessions can exceed ~5 turns.
- Use a cheaper summarizer model (when configured) for summary updates.
- Start budgets conservative and widen only after verifying quality.
- Keep `overflow_policy="truncate_oldest"` for predictable bounded growth.

---

## Validation Checklist

Before enabling STM broadly:

- Verify session continuity: the agent references earlier user turns correctly.
- Verify isolation: two different sessions do not influence each other.
- Verify failure behavior: summarizer failure does not block responses.
- Verify persistence: restart the process and confirm STM resumes if you added a store.
