# Task Groups and Merge Strategies

## Why groups

A single foreground turn may spawn several related background tasks:
- "Research these 3 segments." (3 subagent tasks)
- "Refresh data for these 5 reports." (5 jobs)
- "Compute variants A/B/C." (3 subagent tasks evaluating different approaches)

Without grouping, each task merges independently — the user sees a fragmented stream of partial updates. Groups consolidate the report.

## Group lifecycle

```
spawn(... group="g1")
spawn(... group="g1")
spawn(... group="g1")
    │
    ▼
all tasks complete (or sealed early)
    │
    ▼
group is "sealed" (no more tasks can join)
    │
    ▼
tasks.apply_group(group="g1")
    │
    ▼
single consolidated merge applies to foreground context
    │
    ▼
(if default_group_report=True) user-facing summary generated
```

## Sealing a group

Two ways:
1. **Explicit**: planner calls `tasks.seal_group(group="g1")` when it's done spawning.
2. **Auto**: the group reaches `max_tasks_per_group` (only when that field is set explicitly; unset means no cap), or the turn's open groups are sealed when the foreground yields (`auto_seal_groups_on_foreground_yield=True`).

Once sealed, the group accepts no new tasks. Spawning by `group_id` fails (`group_not_joinable`); spawning by `group="g1"` name in the same turn opens a new group instead. Spawning into an open group that already holds `max_tasks_per_group` tasks fails with `group_task_limit_exceeded` before any task is created.

## Group timeout

When `group_timeout_s` is set explicitly, a watchdog starts once the group is sealed (unset means no timeout). If the group has not completed when it expires, its unfinished tasks are cancelled (reason `group_timeout`, warning log `task_group_timeout`) and the group ends `failed` through the normal path: `wait_for_group_completion` waiters wake, `task_group_failed` telemetry is emitted, and the group report is queued per its report strategy. The watchdog is cancelled when the group completes or the session closes.

## Cancelling a group

`cancel_group` cancels the group's pending tasks (when `propagate=True`) and then finalizes the group as `failed`, signalled like any finished group: waiters wake, telemetry is emitted once, and the group report is queued once.

## Applying a group

```python
tasks.apply_group(group="g1", merge_strategy=None)
```

- `merge_strategy=None` → use `default_group_merge_strategy`.
- `merge_strategy="HUMAN_GATED"` → produce a candidate merge, wait for operator approval.
- `merge_strategy="APPEND"` → append all task results to foreground context.
- `merge_strategy="REPLACE"` → replace a specified slice (planner must supply the slice).

Output: a merge summary dict the planner can render. Includes:
- Task ids.
- Results (or refs to result artifacts).
- Any conflicts (overlapping context patches).
- The applied patch.

## Merge strategies in depth

### `HUMAN_GATED` (default)

Result waits for operator. The host app:
1. Detects completed tasks via `tasks.list(...)` or `tasks.get(...)`.
2. Presents them to the operator.
3. Operator approves → host calls `tasks.apply_group(...)` or per-task apply.

Pros: safety. Cons: requires a merge UI; adds operator latency.

Use for: any task whose result modifies user-facing state (writes, sends, edits).

### `APPEND`

Result is appended to the foreground context automatically. The planner's next step sees it.

Pros: low friction, hands-free. Cons: context can drift unexpectedly; debugging is harder.

Use for: trusted, idempotent enrichment (fact refresh, summary updates).

### `REPLACE`

Result replaces a specified slice using a `ContextPatch`. Requires the spawned task to declare what it replaces.

Pros: cleanest mental model for "refresh this part of context". Cons: requires patch metadata; harder to author.

Use for: scheduled refresh, deterministic regeneration ("re-summarize the conversation from turn 5").

## `ContextPatch`

The merge primitive. Fields:
- `target_path`: dotted path in the context tree.
- `op`: `"append"` | `"replace"` | `"upsert"`.
- `value`: the new content.
- `metadata`: provenance (task id, timestamp, mode).

`APPEND` and `REPLACE` merge strategies internally use `ContextPatch`. For custom merge logic, your `TaskService` can return arbitrary patches and the planner applies them.

## Operator workflows for HUMAN_GATED

A typical UI:
1. **Inbox** — list of completed-but-unmerged tasks/groups.
2. **Preview** — show the candidate patch, the prior context, the new context.
3. **Approve / Reject** — apply or discard.
4. **Conflict resolution** — if multiple patches overlap, operator picks.

For chat agents, this can be inline: a "merge these findings?" prompt with [Approve] [Reject] buttons. Tie into [[penguiflow-rich-output]] `ui_confirm`.

## Group reports

`default_group_report=True` produces a single user-facing summary when a group is applied:
- All results joined.
- LLM summarizer condenses across tasks.
- One coherent paragraph instead of N raw results.

Disable when:
- The host app already renders results elsewhere (and the report would be redundant).
- You need raw results, not LLM synthesis.

## Patterns

### Pattern: parallel research, single report
```python
# planner spawns:
tasks.spawn(mode="subagent", prompt="Research market segment A", group="market_q3")
tasks.spawn(mode="subagent", prompt="Research market segment B", group="market_q3")
tasks.spawn(mode="subagent", prompt="Research market segment C", group="market_q3")
tasks.seal_group(group="market_q3")

# later (or auto-merge with APPEND):
tasks.apply_group(group="market_q3")
# user sees: "Research complete on segments A, B, C. Key findings: ..."
```

### Pattern: variant generation, pick one
```python
# planner spawns three variants, gates with HUMAN_GATED:
tasks.spawn(mode="subagent", prompt="Draft email v1 (formal)", group="email_v")
tasks.spawn(mode="subagent", prompt="Draft email v2 (friendly)", group="email_v")
tasks.spawn(mode="subagent", prompt="Draft email v3 (terse)", group="email_v")
tasks.seal_group(group="email_v")

# operator reviews each draft; picks v2:
tasks.apply_group(group="email_v", merge_strategy="REPLACE")  # planner specifies which slice
```

### Pattern: ETL with sealing gate
```python
# tasks.spawn(mode="job", tool_name="extract", group="etl_run_42")
# tasks.spawn(mode="job", tool_name="transform", group="etl_run_42")
# tasks.spawn(mode="job", tool_name="load", group="etl_run_42")

# seal only after all 3 succeed:
all_done = await wait_for_all_succeeded(group="etl_run_42")
if all_done:
    tasks.seal_group(group="etl_run_42")
    tasks.apply_group(group="etl_run_42")  # writes a "run succeeded" summary
```

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `apply_group` finds nothing to merge | Group wasn't sealed | Call `tasks.seal_group(...)` first |
| Conflicting patches | Two tasks patched the same path | Use distinct paths; or `HUMAN_GATED` and let operator decide |
| Group never auto-seals | `max_tasks_per_group` unset or not reached; no explicit seal | Call `tasks.seal_group(...)` when spawning is done; or set `max_tasks_per_group` explicitly |
| Spawn fails with `group_task_limit_exceeded` / `group_not_joinable` | Group is full or already sealed | Spawn by `group` name to open a new group, or raise `max_tasks_per_group` |
| Group ends `failed` with tasks cancelled (`group_timeout`) | `group_timeout_s` elapsed between seal and completion | Raise `group_timeout_s`, or split the work into smaller groups |
| Reports too verbose | Default summarizer is verbose; multiple grouped reports stack | Set `default_group_report=False`; or customize the summarizer prompt |
| Operator backlog | HUMAN_GATED queue grows | Build the operator UI; consider downgrading some workflows to APPEND |

## Observability

Track:
- Groups created per session.
- Time-to-seal (spawn → seal).
- Time-to-apply (seal → apply).
- Tasks per group (cardinality distribution).
- Operator approval rate (HUMAN_GATED).
- Conflict rate.
