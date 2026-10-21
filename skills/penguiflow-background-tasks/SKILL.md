---
name: penguiflow-background-tasks
description: Enable concurrent background work on a PenguiFlow ReactPlanner — configure `BackgroundTasksConfig(enabled=True, allow_tool_background=..., default_mode=..., default_merge_strategy=..., max_concurrent_tasks=..., task_timeout_s=...)`, expose the `tasks.*` tool surface via `build_task_tool_specs()`, supply `tool_context["task_service"]` (implementing the `TaskService` protocol) plus `session_id`, choose merge strategies (`HUMAN_GATED`/`APPEND`/`REPLACE`), use task groups for coherent multi-task reports, and let tools opt-into background execution via `spec.extra["background"]`. Use when a user says "background task", "spawn a subagent", "async tool", "task groups", or names `BackgroundTasksConfig`, `tasks.spawn`, `task.subagent`.
---

# PenguiFlow Background Tasks

## When to use
- The foreground agent needs to stay responsive while a long task runs.
- You need parallelism beyond a single `parallel` planner step.
- An "always slow" tool (scrape, ETL) should run async with a delayed merge.
- You want a multi-task report grouped for the user.

## When NOT to use
- The work is fast (<2s) — don't bother; foreground tools are simpler.
- You need a durable task queue across processes — this is in-process by default; bring your own `TaskService` for cross-process work.
- The work has user-visible interim output → use [[penguiflow-streaming]] instead.
- A single LLM-driven parallel step suffices → use the planner's parallel-and-joins (see [[penguiflow-reactplanner-config]]).

## Hard boundaries
This skill covers **planner-driven** background tasks: spawn, observe, merge. The `TaskService` implementation (executor, persistence) is something you (or your host platform) provide. The pause/resume mechanics for HITL on merged context belong to [[penguiflow-hitl-pause-resume]].

## Workflow

### 1) Configure `BackgroundTasksConfig`
```python
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import BackgroundTasksConfig

planner = ReactPlanner(
    ...,
    background_tasks=BackgroundTasksConfig(
        enabled=True,                        # default False; must opt in
        include_prompt_guidance=True,        # default
        allow_tool_background=False,         # default
        default_mode="subagent",             # default — "subagent" | "job"
        default_merge_strategy="HUMAN_GATED",# default
        max_concurrent_tasks=5,              # field default; session uses 3 if unset
        max_tasks_per_session=50,            # ACTIVE tasks; session uses 8 if unset
        task_timeout_s=3600,                 # INT seconds; session uses 900 if unset
        proactive_report_enabled=False,      # default
        default_group_merge_strategy="APPEND",  # library default; flip to HUMAN_GATED for safety
        default_group_report=True,           # default
        max_tasks_per_group=10,              # cap only if set; group auto-seals when full
    ),
)
```
Limits are enforced by the session, not the planner: `StreamingSession.configure_background_tasks(config)` applies only the fields you **set explicitly** to that session's `SessionLimits` (`max_concurrent_tasks`, `max_tasks_per_session`, `task_timeout_s` → `max_task_runtime_s`, `max_pending_steering` → `max_pending_user_messages`). Unset fields keep the `SessionLimits` values (3 concurrent / 8 active tasks / 900 s / 2 pending user messages by default), and `SessionLimits.max_background_tasks` (4) still applies. The config reaches the session via `InProcessTaskService(background_config=...)`, the AG-UI adapter, or a direct `configure_background_tasks` call — passing it only to `ReactPlanner` is not enough.

Full field table in `references/config-and-policies.md`.

### 2) Implement (or import) a `TaskService`
The `tasks.*` tools call into a `TaskService` you supply. PenguiFlow ships an in-process implementation suitable for development. Production deployments typically wrap it with persistence (StateStore) and a real executor (asyncio task group, worker pool, distributed queue).

The host app:
1. Constructs a `TaskService` instance.
2. Passes it via `tool_context["task_service"]` for every foreground `planner.run(...)` call.
3. Ensures `tool_context["session_id"]` is set (required for scoping).

See `references/task-service-protocol.md` for the protocol and a reference implementation.

### 3) Expose `tasks.*` tools
```python
from penguiflow.sessions.task_tools import build_task_tool_specs

task_tool_specs = build_task_tool_specs()
catalog = build_catalog(my_nodes + task_tool_specs, registry)
```

Tools exposed:
- `tasks.spawn`, `tasks.list`, `tasks.get`, `tasks.cancel`, `tasks.prioritize`
- `tasks.seal_group`, `tasks.list_groups`, `tasks.apply_group`

**Hard rule**: expose `tasks.*` only to the **foreground** agent. Subagents must not spawn further tasks unless you explicitly want recursive agents — apply a `ToolVisibilityPolicy` that hides `tasks.*` from subagent traces.

### 4) Pick a mode per task: `subagent` vs `job`
| Mode | What runs | When to use |
|---|---|---|
| `subagent` | A full ReactPlanner sub-run with its own catalog and prompt | Multi-step reasoning, parallel research, anything needing the LLM in the loop. |
| `job` | A single tool call (no further LLM) | One slow tool you want async (scrape, ETL, batch download). No pause/resume inside the job. |

Set per-call: `tasks.spawn(mode="subagent", ...)`. The planner's `task.subagent` and `task.tool` action opcodes normalize into `tasks.spawn` with the right mode.

### 5) Pick a merge strategy
| Strategy | Behavior |
|---|---|
| `HUMAN_GATED` (default) | Result waits in a queue; user/operator explicitly merges via `tasks.apply_group` or equivalent. |
| `APPEND` | Result is appended to the foreground context automatically when the task completes. |
| `REPLACE` | Result replaces a specified slice of context (uses `ContextPatch`). |

Start with `HUMAN_GATED` until you trust the workflow. `APPEND`/`REPLACE` shine for trusted, idempotent tasks (e.g., scheduled fact refreshes).

### 6) Use task groups for coherent reports
Spawn related tasks with `group="..."` (e.g., per research segment), then `tasks.seal_group(group=...)` and `tasks.apply_group(group=...)` to merge as one. With `default_group_report=True`, the user gets a single summary instead of N partial updates. Full pattern in `references/groups-and-merge.md`.

### 7) (Advanced) Tool-initiated background spawns
A tool runs async (not inline) when **all** are true: `allow_tool_background=True`, `spec.extra["background"]["enabled"]=True`, and `tool_context` has `task_service` + `session_id`. Returns `{"task_id": ..., "status": "PENDING", "message": "spawned:job|subagent"}`. Useful for "always slow but safe" integrations.

### 8) Limits, alerts, observability
Enforce `max_concurrent_tasks`, `max_tasks_per_session` (active tasks) — alert on hit (`task_limit_exceeded`, `background_task_limit_exceeded`). Track tasks created/completed/failed/cancelled, time-to-merge (HUMAN_GATED backlog), `PlannerEvent` lifecycle. See [[penguiflow-observability]].

## Troubleshooting (fast checks)
- **`task_service_unavailable`** — `tool_context["task_service"]` not set; wire your `TaskService` for every foreground call.
- **`session_id_missing`** — pass `tool_context["session_id"]` on every `run()`/`resume()`.
- **`tasks.*` calls hang** — `TaskService` implementation is buggy or its executor stalled; inspect the service's internals.
- **Tasks pile up unmerged** — `default_merge_strategy="HUMAN_GATED"` and no one is merging; either auto-merge with `APPEND`/`REPLACE` or build a merge UI.
- **Subagent spawns tasks recursively** — `tasks.*` visible to subagent context; hide via `ToolVisibilityPolicy`.
- **Task timeout fires** — `task_timeout_s` too low for the workload (if unset, the session's 900 s `max_task_runtime_s` applies), or the task is wedged. Raise the timeout or fix the workload.
- **Tool's background opt-in ignored** — confirm `allow_tool_background=True` planner-wide and `spec.extra["background"]["enabled"]=True` on the tool.
- **Group apply merges nothing** — `tasks.seal_group(group=...)` must run before `tasks.apply_group(...)`.

## Worked example
- Template wiring: `penguiflow new <template> --with-background-tasks --dry-run` shows the generated `task_service.py` skeleton and planner hook-up.
- Integration tests under `tests/test_background_tasks*.py` exercise the `tasks.*` surface end-to-end.

## References (load only as needed)
- `references/config-and-policies.md` — every `BackgroundTasksConfig` field, tool-initiated spawn rules, prompt guidance, limits, proactive reporting.
- `references/task-service-protocol.md` — `TaskService` protocol contract, in-process implementation, persistence patterns, recovery.
- `references/groups-and-merge.md` — task groups, merge strategies (`HUMAN_GATED`/`APPEND`/`REPLACE`), `ContextPatch`, operator workflows.
