# Planner troubleshooting

## What it is / when to use it

This page is a symptom-driven checklist for common `ReactPlanner` issues.

Use it when you are debugging:

- schema/JSON failures and repair loops,
- tool selection/visibility problems,
- parallel join behavior,
- memory scoping and pause/resume issues.

## Non-goals / boundaries

- This page is not a replacement for planner contracts; it links to canonical contract pages.
- This page does not cover ToolNode configuration in depth (see tools runbooks).

## Contract surface (things to check first)

Before deep-diving, confirm these knobs and contracts:

- action contract: **[Actions & schema](actions-and-schema.md)**
- tool design: **[Tool design](tool-design.md)**
- tool discovery/integration: **[Tooling](tooling.md)**
- pause/resume contract: **[Pause/resume (HITL)](pause-resume-hitl.md)**
- memory isolation: **[Memory](memory.md)**

Operationally important planner settings:

- `json_schema_mode`, `repair_attempts`, `arg_fill_enabled`, `max_consecutive_arg_failures`
- `max_iters`, `deadline_s`, `hop_budget`
- `absolute_max_parallel` and `planning_hints.max_parallel`
- `llm_timeout_s`, `llm_max_retries`

## Operational defaults (fast path)

If you’re in production and need a safe “baseline”:

- use `temperature=0.0`, `json_schema_mode=True`
- keep tool schemas small and add `@tool(examples=...)`
- enforce tool visibility/policy (reduce catalog surface)
- set timeouts/budgets to avoid infinite waits

## Failure modes & recovery

The sections below are organized by symptom. For each, prefer:

1. confirm the contract (what the planner expects)
2. reduce surface area (visibility, schemas, concurrency)
3. instrument (events + structured logs)

## Observability

If you can’t explain a failure quickly, you usually need an event stream:

- configure `event_callback` and record `PlannerEvent.to_payload()`
- alert on `budget_exhausted`, high repair counts, high tool error rates, and frequent pauses

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Treat `llm_context` as LLM-visible and `tool_context` as privileged.
- Don’t “fix” tool visibility bugs by adding secrets to `llm_context`.
- Treat `resume_token` as an authorization capability.

## Invalid JSON loops

**Symptoms**

- Many repeated “repair” attempts
- Planner hits `budget_exhausted`

**Likely causes**

- model cannot reliably emit strict JSON
- too-large tool catalog or ambiguous tool names

**Fix**

- use a stronger model or enable `json_schema_mode=True`
- reduce tool surface (policy/visibility)
- add tool examples
- keep args schemas small

## “Tool not found” / unknown `next_node`

**Symptoms**

- planner reports invalid node/tool name

**Fix**

- ensure the tool is present in the catalog
- ensure the tool is visible to the model (tool policy / visibility)
- avoid multiple tools with near-identical names

## Arg-fill stuck / repeated invalid args

**Symptoms**

- repeated tool selection with invalid args

**Fix**

- keep args model minimal and well-typed
- add `examples` on the tool spec
- keep `arg_fill_enabled=True`
- adjust `max_consecutive_arg_failures` to force followup instead of looping

## Parallel join skipped

**Symptoms**

- join shows `reason="branch_failures"`

**Fix**

- inspect branch failures
- add retries/timeouts to flaky tools
- lower ToolNode concurrency to reduce rate-limiting

## Memory disabled unexpectedly

**Symptoms**

- memory strategy configured but no memory appears in `llm_context`

**Likely causes**

- `MemoryIsolation.require_explicit_key=True` and no key could be derived

**Fix**

- pass `memory_key=MemoryKey(...)` explicitly, or
- configure isolation paths and ensure `tool_context` contains those keys

## Pause token invalid/expired

**Symptoms**

- `resume(...)` raises KeyError for token

**Fix**

- ensure pause records are persisted (StateStore) in distributed deployments
- ensure tokens are not re-used (tokens are consumed on load in some stores)
- ensure TTL policies match your UX

## Troubleshooting checklist

- **Nothing happens**: check that `catalog` is non-empty and tool visibility isn’t filtering everything out.
- **Planner is slow**: check LLM latency, ToolNode concurrency/rate limits, and whether parallelism is being throttled.
- **Planner is unsafe**: confirm allowlists/HITL for write/external tools and verify secrets never reach `llm_context`.
