# Strategies and Memory Health

## `truncation` strategy

Deterministic. No LLM dependency.

Behavior:
- Keeps the most recent `full_zone_turns` turns.
- Drops older turns when over budget.
- Health is always `healthy`.

Use when:
- Sessions are short.
- You can't afford LLM cost / latency for memory maintenance.
- You don't need long-context awareness beyond the last few turns.

What the LLM sees:
```json
"conversation_memory": {
  "recent_turns": [{"user": "...", "assistant": "..."}, ...]
}
```

No `summary`, no `pending_turns`.

## `rolling_summary` strategy

LLM-maintained summary + recent turns.

Behavior:
- New turns enter `pending_turns`.
- When `pending_turns` is large enough, the summarizer model rolls them into the `summary`.
- The summary is bounded by `summary_max_tokens`.
- If the summarizer fails repeatedly, memory enters `degraded` and silently falls back to truncation behavior.

What the LLM sees (healthy):
```json
"conversation_memory": {
  "recent_turns": [{"user": "...", "assistant": "..."}, ...],
  "summary": "User's name is Alice. Prefers terse responses. Asked about ...",
  "pending_turns": []
}
```

What the LLM sees (degraded):
```json
"conversation_memory": {
  "recent_turns": [...]
}
```

(Summary may be stale and pending_turns absent — fallback to truncation-style.)

## `MemoryHealth` lifecycle

States (the `MemoryHealth` enum):
- `HEALTHY` (`"healthy"`) — Summarizer works; rolling summary is fresh.
- `RETRY` (`"retry"`) — Last summarizer call failed; another retry is pending under `retry_attempts`/`retry_backoff_base_s`.
- `DEGRADED` (`"degraded"`) — Retry budget exhausted; memory falls back to truncation behavior while a slower recovery loop continues (`degraded_retry_interval_s`).
- `RECOVERING` (`"recovering"`) — A recovery attempt (summarizing the backlog) is running after degradation; success returns to `HEALTHY`, failure back to `DEGRADED`.

Transitions emit `on_health_changed(old, new)`. Wire this to your metrics sink to alert on summarizer reliability.

### Recovery loop

1. A failed summarizer call moves health to `retry`; the background task retries itself with exponential backoff (`retry_backoff_base_s * 2**(attempt-1)`), up to `retry_attempts` times. A success returns to `healthy`.
2. Retries exhausted: health becomes `degraded` and pending turns move to a backlog.
3. While degraded, a new turn triggers a recovery attempt at most once per `degraded_retry_interval_s` (health `recovering` while it runs).
4. Recovery success → `healthy`. Recovery failure → back to `degraded` with the backlog kept.

`recovery_backlog_limit` caps the backlog kept for recovery (oldest turns are dropped). A restored state normalises in-progress health: `recovering` becomes `degraded` if a backlog remains (else `healthy`), and `retry` becomes `healthy`.

## Trajectory digests

When `include_trajectory_digest=True`, each turn carries a `TrajectoryDigest`:

```python
TrajectoryDigest(
    tools_invoked=["search.web", "search.docs"],
    observations_summary="Found 5 results. Top: ...",
    reasoning_summary="Decided to combine both search backends because...",
    artifacts_refs=["artifact://abc123"],
)
```

The planner builds the digest from the turn's trajectory after the run completes.

Trade-off:
- **On** — the model sees what tools were used and why; better continuity for multi-turn debugging.
- **Off** — smaller prompts; cheaper. Default.

Turn it on for high-value sessions (support escalations, debugging). Leave it off for high-throughput chat.

## Choosing a summarizer model

Production criteria:
- **Cheap** — summarizer runs once per turn. Cost compounds.
- **Fast** — affects perceived latency of multi-turn sessions.
- **Reliable** — failures cause health degradation; uptime matters.
- **Good at compression** — small models that summarize crisply.

Reasonable choices:
- `gpt-4.1-mini`, `gpt-4o-mini`
- `claude-haiku` (latest)
- `gemini-flash` (latest)

Avoid the same model as your main planner unless prompt cost is negligible.

## Token estimator

Default heuristic counts characters / 4 (rough English approximation). For production:

```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4")
stm = ShortTermMemoryConfig(..., token_estimator=lambda s: len(enc.encode(s)))
```

Real tokenizers give accurate budget enforcement. Worth the cost if you're optimizing prompts tightly.

## Operational defaults by workload

### Chat agent, multi-tenant SaaS
- `strategy="rolling_summary"`
- `full_zone_turns=5`
- `summary_max_tokens=800`
- `total_max_tokens=8000`
- `overflow_policy="truncate_oldest"`
- `include_trajectory_digest=False`
- `summarizer_model="gpt-4o-mini"`
- `require_explicit_key=True`

### Single-user assistant
- `strategy="truncation"`
- `full_zone_turns=10`
- `total_max_tokens=16000`
- `require_explicit_key=False`

### Debugging / support tool
- `strategy="rolling_summary"`
- `full_zone_turns=8`
- `include_trajectory_digest=True`   # keep tool history visible
- `summarizer_model="gpt-4o-mini"`

### Hard-budget compliance flow
- `strategy="truncation"`
- `total_max_tokens=4000`
- `overflow_policy="error"`           # fail loudly if over budget

## Anti-patterns

- **Long-term memory** — STM is bounded; don't use it as a knowledge base. Build retrieval separately.
- **Storing secrets** — anything in STM enters `llm_context`. Treat it as LLM-visible.
- **Anonymous keys in production** — `require_explicit_key=False` + no `tool_context` keys = single global session shared across users. Catastrophic in multi-tenant.
- **Tight budgets with `error` overflow** — user-facing flows will randomly raise. Use truncation policies for UX.
- **Heavy hooks** — hooks are fire-and-forget but they still consume the event loop. Don't do DB writes there; use a `StateStore`.
