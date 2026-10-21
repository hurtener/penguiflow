# `BackgroundTasksConfig` Reference

Configuration for planner-driven background tasks. Pass to `ReactPlanner(..., background_tasks=BackgroundTasksConfig(...))`. All defaults are from `penguiflow.planner.models.BackgroundTasksConfig`.

## Enablement

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `False` | Master switch. Tasks are off unless this is True. |
| `include_prompt_guidance` | `True` | Add planner-side hints describing `task.subagent`/`task.tool` opcodes and `tasks.*` tools. |

When `enabled=False`, `tasks.*` tools and `task.*` opcodes are not available; spawn attempts fail explicitly.

## Execution mode and merge

| Field | Default | Purpose |
|---|---|---|
| `allow_tool_background` | `False` | When True, tools with `spec.extra["background"]["enabled"]=True` can spawn async instead of running inline. |
| `default_mode` | `"subagent"` | `"subagent"` (full sub-planner) or `"job"` (single tool). |
| `default_merge_strategy` | `"HUMAN_GATED"` | `"HUMAN_GATED"`, `"APPEND"`, `"REPLACE"`. Default merge for tool-initiated and explicit spawns. |
| `context_depth` | `"full"` | `"full"` / `"summary"` / `"minimal"` snapshot depth handed to the spawned task. |
| `propagate_on_cancel` | `"cascade"` | `"cascade"` (kill children when parent cancels) or `"orphan"`. |
| `spawn_requires_confirmation` | `False` | When True, spawning a task requires explicit user confirmation. |

### Tool-initiated path

A tool call is rerouted to a background task when **all** are true:
1. `background_tasks.allow_tool_background=True`.
2. The tool's `NodeSpec.extra["background"]["enabled"] is True`.
3. `tool_context["task_service"]` is present.
4. `tool_context["session_id"]` is a string.

When that happens, the foreground tool call returns:
```python
{
    "task_id": "...",
    "status": "PENDING",
    "message": "spawned:job"  # or "spawned:subagent"
}
```

The planner records this observation. Foreground continues; the task runs out-of-band and merges per the configured strategy when done.

Tool-spec metadata example:
```python
spec = NodeSpec(
    name="scrape_site",
    desc="...",
    side_effects="external",
    extra={
        "background": {
            "enabled": True,
            "mode": "job",                    # override default_mode
            "merge_strategy": "APPEND",        # override default_merge_strategy
            "notify_on_complete": True,        # platform-specific
        },
    },
    ...
)
```

## Limits

Limits are enforced by the session, not the planner. `StreamingSession.configure_background_tasks(config)` copies each field you **set explicitly** onto a per-session copy of `SessionLimits`. Unset fields keep the `SessionLimits` value (shown in parentheses), so the field default does not apply on its own.

| Field | Default | Session limit (value if unset) | Purpose |
|---|---|---|---|
| `max_concurrent_tasks` | `5` | `max_concurrent_tasks` (`3`) | Cap on tasks running at once in the session (semaphore). Extra tasks queue until a slot frees. |
| `max_tasks_per_session` | `50` | `max_tasks_per_session` (`8`) | Cap on active (non-terminal) tasks per session; completed tasks don't count. Spawn fails with `task_limit_exceeded`. |
| `task_timeout_s` | `3600` (1 hour, **int** seconds) | `max_task_runtime_s` (`900`) | Per-task wall-clock timeout. |
| `max_pending_steering` | `2` | `max_pending_user_messages` (`2`) | Max undrained `USER_MESSAGE` steering events per task; further user messages are rejected. Not the inbox capacity (`steering_queue_size`). |

`SessionLimits.max_background_tasks` (default `4`) is not overridden and still caps background tasks (`background_task_limit_exceeded`).

The config reaches the session via `InProcessTaskService(background_config=...)` (applied on spawn), the AG-UI adapter (which discovers the planner's config), or a direct `session.configure_background_tasks(config)` call. Passing it only to `ReactPlanner(background_tasks=...)` does not apply these limits.

These defaults are conservative; tune downward for latency-sensitive products and upward for long-running research agents.

## Proactive reporting (auto-merge modes)

When `default_merge_strategy` is `APPEND` or `REPLACE`, completed tasks merge automatically. Controls:

| Field | Default | Purpose |
|---|---|---|
| `proactive_report_enabled` | `False` | When True, the planner notifies the user as tasks complete. |
| `proactive_report_strategies` | `["APPEND", "REPLACE"]` | Merge strategies that trigger proactive reports. |
| `proactive_report_max_queued` | `5` | Max queued reports before dropping oldest. |
| `proactive_report_timeout_s` | `30.0` | Timeout for proactive message generation. |
| `proactive_report_max_hops` | `2` | Max recursion hops before disabling background spawning. |
| `proactive_report_fallback_notification` | `True` | Fall back to notification panel if generation fails. |

For chat UIs, `proactive_report_enabled=True` produces "Heads up — research on segment A is in. Want me to incorporate it?" style messages.

## Task groups

| Field | Default | Purpose |
|---|---|---|
| `default_group_merge_strategy` | `"APPEND"` | Strategy applied at `tasks.apply_group`. |
| `default_group_report` | `True` | Produce a single user-facing summary on apply. |
| `max_tasks_per_group` | `10` | Hard cap per group, only when set explicitly (unset: no cap). The group auto-seals once full; spawning into a full open group fails with `group_task_limit_exceeded`. |
| `group_timeout_s` | `600.0` | Seal-to-complete timeout, only when set explicitly (unset: no timeout). On expiry the group's unfinished tasks are cancelled and the group ends `failed`. |

Groups bundle related background work into one merge. Useful when:
- Multi-segment research: each segment is a task, the group is the consolidated report.
- Parallel transformations: each variant is a task, the group decides which to apply.
- Multi-step ETL: each step is a task, the group seals when all succeed.

## Operational defaults

Conservative starting point:
```python
BackgroundTasksConfig(
    enabled=True,
    allow_tool_background=False,            # flip True only after observability is in place
    default_mode="subagent",
    default_merge_strategy="HUMAN_GATED",
    max_concurrent_tasks=4,
    max_tasks_per_session=20,
    task_timeout_s=300.0,
    proactive_report_enabled=False,
    default_group_merge_strategy="HUMAN_GATED",
    default_group_report=True,
    max_tasks_per_group=10,
)
```

Tighter (latency-sensitive product):
- `max_concurrent_tasks=2`
- `task_timeout_s=60.0`
- `default_merge_strategy="APPEND"` (lower friction)

Looser (long-running research agent):
- `max_concurrent_tasks=8`
- `task_timeout_s=1800.0`
- `default_merge_strategy="HUMAN_GATED"` (operator reviews each)

## Prompt guidance

With `include_prompt_guidance=True`, the planner's system prompt gains a section describing:
- How to use `task.subagent` and `task.tool` opcodes.
- When to prefer background over inline (slow work, parallel-able work).
- How task groups consolidate reports.

Disable this if your prompt is already opinionated about task patterns and you don't want the default guidance to conflict.

## Foreground vs subagent visibility

**Critical**: subagents must not be able to spawn more tasks unless you explicitly want recursion.

Two layers of defense:
1. Don't include `tasks.*` in the subagent's catalog.
2. Apply a `ToolVisibilityPolicy` for subagent contexts that filters out `tasks.*`.

The planner doesn't enforce this automatically — it's your responsibility at planner-init time.

## Observability

`PlannerEvent` types emitted by the background subsystem:
- `task_spawned`, `task_started`, `task_completed`, `task_failed`, `task_cancelled`.
- `task_merged` (when context patch applies).
- `task_group_sealed`, `task_group_applied`.

Track:
- Tasks spawned per session.
- Time-to-completion p50/p95/p99 by mode.
- Failure rate by mode.
- Merge backlog (tasks completed but not yet merged, for HUMAN_GATED).
- Time-to-merge (operator workflow signal).

See [[penguiflow-observability]] for the broader event taxonomy.

## Common configuration mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| `enabled=True` but no `task_service` in `tool_context` | All `tasks.*` calls fail | Wire the service |
| No limits set | Session falls back to `SessionLimits` defaults (3 concurrent / 8 active tasks / 900 s), which may not fit the workload | Set `max_concurrent_tasks`, `max_tasks_per_session` explicitly |
| Config passed only to `ReactPlanner` | Limits never reach the session | Pass it to `InProcessTaskService(background_config=...)` or call `session.configure_background_tasks(config)` |
| `default_merge_strategy="APPEND"` from day 1 | Hard to debug context drift | Start `HUMAN_GATED`, promote to APPEND only with observability |
| `tasks.*` visible to subagents | Recursive task explosion | Visibility policy |
| `task_timeout_s=None` for tool-initiated background | Wedged tasks accumulate | Set a real timeout |
