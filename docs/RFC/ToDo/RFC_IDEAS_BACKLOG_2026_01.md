# Ideas Backlog (To Review) — 2026-01

This document captures ideas discussed during development/review so they don’t get lost.
It is intentionally not a committed roadmap; items should be evaluated and turned into
their own RFCs (or incorporated into existing RFCs) before implementation.

## Background Tasks v2.11 (RFC_BACKGROUNDTASKS_V2.11.MD)

Implementation is now phased in `docs/RFC/ToDo/RFC_BACKGROUNDTASKS_V2.11.MD`.

Open follow-ups / related ideas:
- Ensure `retain_turn` is correct and config-driven (`retain_turn_timeout_s`, de-dupe proactive emissions).
- Ensure background results never bloat user prompts (extract/remove pattern in planner message building).

## LLM Reliability + Routing (Bifrost-style)

### 1) Provider failover + model fallback chains

Goal: automatic fallbacks when a provider/model fails (rate limits, outages, model unavailable, timeouts, parse/validation errors).

PenguiFlow mapping:
- Add a routing policy on the LLM client/node: `primary -> fallback1 -> fallback2`.
- Classify failures into stable categories (examples):
  - HTTP 429 / rate limit
  - 5xx / provider outage
  - timeouts
  - “model not found” / invalid model
  - structured-output parse/validation failures
- Per-category policy:
  - retry same route (with backoff)
  - failover to next route
  - fail closed / surface error immediately
- Explicit semantics: a failover attempt is treated as a new request (plugins/hooks re-run).

Implementation notes:
- The repo already has an LLM error taxonomy (`penguiflow/llm/errors.py`) and retry logic in the native adapter layer.
- Candidate shape: `FailoverLLMClient(routes=[...], policy=...)` or `RouterPolicy` integrated into the provider selection layer.

### 2) Key-pool management + weighted load balancing

Goal: distribute traffic across multiple API keys (and optionally multiple providers/models) with weighting and health tracking.

PenguiFlow mapping:
- `KeyPool` per provider:
  - multiple keys, weights, health, cooldowns
  - compatibility filtering (key supports model family)
- Selection strategy:
  - weighted random
  - least-latency / least-errors
  - circuit breaker quarantine after N failures

### 3) Adaptive routing based on live performance

Goal: automatically down-weight “bad” routes using real-time metrics (latency, error rate, throughput).

MVP:
- Maintain rolling p95 latency + error rate per route.
- Down-weight routes for a cooldown window when degraded.
- Guard against oscillation (hysteresis + minimum traffic).

## Caching

### 4) Semantic caching (cost + latency win)

Goal: cache by similarity (embeddings), not exact prompt string.

PenguiFlow mapping:
- Store `(embedding(prompt), response, metadata)` in a durable store.
- Configurable threshold, TTL, and “do not cache” flags (PII/high-risk).
- Tool-call aware:
  - cache only “final answer” class of runs by default
  - avoid caching tool executions unless explicitly allowed

Risks:
- correctness regressions from approximate matches
- privacy policy and tenancy isolation requirements
- invalidation strategy

## Tool Search + Deferred Loading

There is a dedicated RFC with substantial detail: `docs/RFC_TOOL_SEARCH_AND_EXAMPLES.md`.

Idea summary:
- Reduce tool context bloat by deferring tool schema injection.
- Provide a meta “search tools” tool that returns references, allowing the agent to discover and then call tools.
- Start with fast local search (BM25 / SQLite FTS5); optionally add semantic search later.

Suggested evaluation dimensions:
- token savings vs. retrieval accuracy
- latency budget (target sub-20ms for ~500 tools)
- provider-agnostic tool format adapters
- caching strategy (query result TTL + schema cache)

