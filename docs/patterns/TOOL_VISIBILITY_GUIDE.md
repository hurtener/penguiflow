# Tool Visibility & Tool Policy Guide

This guide explains how to control which tools the planner can execute and which tools the LLM can *see*.

It also calls out the thread-safety constraints around per-run tool visibility so downstream teams can use it safely.

## Concepts (two layers)

PenguiFlow has two complementary mechanisms:

1) **Planner tool policy (init-time filtering)**
- A `ToolPolicy` can be applied when constructing a `ReactPlanner` (or when creating a derived planner via `fork()`).
- The policy filters the planner’s catalog during initialization, so removed tools:
  - are not present in `planner._specs`
  - are not in `planner._spec_by_name`
  - are not included in the system prompt tool catalog

2) **Per-run tool visibility (run-time filtering)**
- A `ToolVisibilityPolicy` can be passed to `ReactPlanner.run(..., tool_visibility=...)` and `ReactPlanner.resume(..., tool_visibility=...)`.
- This is intended for per-tenant / per-user / per-request visibility (RBAC, feature flags, entitlements).
- It affects the *LLM-visible* tool catalog for that single run, and it also restricts tool resolution so hidden tools are not executable during that run.

## Thread-safety (crystal clear)

### `tool_visibility` is **not thread-safe** on a shared planner instance

`tool_visibility` works by temporarily mutating planner internals while the run is executing (for that planner instance):
- `planner._specs`
- `planner._spec_by_name`, `planner._catalog_records`, `planner._tool_aliases`
- `planner._system_prompt`
- guardrail context `available_tools` (when guardrails are enabled)

These fields are used throughout the run loop (prompt building, tool routing, guardrail context).

If two runs overlap on the *same* `ReactPlanner` object, one run can observe the other run’s temporary catalog/prompt state.
That can cause incorrect tool availability, incorrect prompts, and nondeterministic behavior.

### Safe rule

Only use `tool_visibility` when you can guarantee:
- **No concurrent `run()` / `resume()` calls on the same planner instance.**

### Recommended concurrency pattern (safe)

If you need concurrency (background tasks, multi-tenant servers, parallel HTTP requests):
- Create a **fresh planner instance per request/task**, typically via `planner.fork(...)`.
- Then use `tool_visibility` on that per-request/per-task planner instance.

This is safe because the temporary mutations occur on an instance that is not shared across concurrent runs.

## Recommended usage patterns

### Pattern A: “Safe by default” for servers (fork per request)

Use this when your host app handles multiple requests concurrently.

1. Keep a base planner configuration (nodes/catalog/registry, LLM config, etc.).
2. For each request:
   - create an isolated planner instance via `fork()`
   - apply coarse constraints via `tool_policy` at fork-time (optional)
   - apply fine-grained per-request visibility via `tool_visibility` at run-time (optional)

```python
from penguiflow.planner import ReactPlanner, ToolPolicy

def handle_request(base: ReactPlanner, *, user_id: str, role: str) -> str:
    request_planner = base.fork(
        # Optional: coarse constraints (tenant/app-level)
        tool_policy=ToolPolicy(denied_tools={"payments.charge"}),
    )

    result = await request_planner.run(
        query="...",
        tool_context={"user_id": user_id, "role": role},
        tool_visibility=RoleBasedVisibility(...),  # optional: per-request
    )
    return result.payload
```

### Pattern B: “Single-threaded” sequential usage (no fork, but no overlap)

Use this only if you control execution so that the same planner instance is never used concurrently.
For example: a local CLI where requests are processed sequentially.

```python
result = await planner.run(
    query="...",
    tool_context={"role": "viewer"},
    tool_visibility=RoleBasedVisibility(...),
)
```

## `fork()` details (tool policy override + merging)

`ReactPlanner.fork()` supports:
- `tool_policy=...` to override or tighten tool policy on the derived planner.
- `inherit_policy=True` (default) to merge with an existing base policy.

Merge semantics:
- `denied_tools`: union (base ∪ override)
- `require_tags`: union
- `allowed_tools`:
  - if both specify allowlists: intersection
  - if only one specifies: that allowlist is used
  - if neither specifies: no allowlist

This makes it easy to set a “global baseline” policy and then tighten per-subagent/per-task.

## `tool_visibility` details (what changes during a run)

When you pass `tool_visibility`, the planner will:
- compute a visible subset from the current planner’s `specs` and the provided `tool_context`
- rebuild:
  - tool alias mapping (`_tool_aliases`)
  - tool lookup (`_spec_by_name`)
  - prompt catalog records (`_catalog_records`)
  - the system prompt (tool catalog section)
- run the full ReAct loop using that temporary catalog
- restore the original catalog/prompt state after the run finishes

### Important: `resume()` should use the same visibility policy

If a run pauses and you later call `resume()`:
- pass the same `tool_visibility` policy to `resume()` (or one that is compatible with the original visible set)
- otherwise the resumed run might see a different tool catalog than the original paused run

## Practical guidance for downstream teams

### Use tool policy for “hard” constraints

If a tool must never be available in a given environment (e.g., prod safety):
- prefer `ToolPolicy` at planner init or on `fork()`.

### Use tool visibility for “soft” per-request shaping

If tool access depends on user/tenant/role/session:
- prefer `tool_visibility` so the LLM doesn’t waste tokens trying forbidden tools.

### Defense in depth

Even with visibility, you may still want enforcement at execution time (guardrail-based tool gates).
This is tracked for a future RFC (see `docs/RFC/ToDo/RFC_IDEAS_BACKLOG_2026_01.md`).

## Limitations (current)

- No caching of the rebuilt system prompt tool catalog yet.
  - This is intentionally deferred; the first implementation prioritizes correctness and opt-in safety.
- `tool_visibility` is not a replacement for `fork()` in concurrent environments.

