# RFC: Task Groups for Coordinated Background Task Reporting

## Status: Draft

## Summary

This RFC proposes extending the background task system with **Task Groups** - a mechanism to coordinate multiple related background tasks and produce cohesive, synthesized reports instead of fragmented individual notifications.

Enhancements to the initial proposal:
- **Atomic reporting**: grouped tasks suppress per-task proactive reports/notifications and instead trigger exactly one group-level report/event (unless explicitly configured otherwise).
- **Safe default for HUMAN_GATED**: grouped results requiring approval must not leak unapproved details in a “synthesis” bubble; approvals should be bundled and narrative synthesis should happen only after approval.
- **Auto-seal for reliability**: groups should seal automatically when the foreground turn yields so the LLM does not have to remember `group_sealed=True`.
- **Strict name collision semantics**: `group` is a display label, not an identifier; reusing a name later creates a new group with a new `group_id` (same display name), never joining an older group due to name reuse and never failing due to collisions.

---

## Table of Contents

1. [Current System](#current-system)
2. [Problem Statement](#problem-statement)
3. [Proposed Solution: Task Groups](#proposed-solution-task-groups)
4. [Design Options](#design-options)
5. [Use Cases](#use-cases)
6. [API Design](#api-design)
7. [Runtime Behavior](#runtime-behavior)
8. [Configuration](#configuration)
9. [Open Questions](#open-questions)

---

## Current System

### Background Task Flow

When the foreground agent spawns a background task:

```
┌─────────────────┐     tasks.spawn()      ┌─────────────────┐
│   Foreground    │ ───────────────────►   │  Background     │
│     Agent       │                        │    Task         │
└─────────────────┘                        └─────────────────┘
        │                                          │
        │  (yields turn to user)                   │  (executes async)
        ▼                                          ▼
   User continues                            Task completes
   conversation                                    │
                                                   ▼
                                          ┌─────────────────┐
                                          │  ContextPatch   │
                                          │  (results)      │
                                          └─────────────────┘
                                                   │
                                    ┌──────────────┼──────────────┐
                                    ▼              ▼              ▼
                                 APPEND        REPLACE      HUMAN_GATED
                              (auto-merge)  (auto-merge)   (needs approval)
```

### Current `tasks.spawn` Parameters

```python
class TasksSpawnArgs(BaseModel):
    query: str | None                    # Task instruction (required for subagent)
    mode: Literal["subagent", "job"]     # Default: "subagent"
    tool_name: str | None                # Required for job mode
    tool_args: dict[str, Any] | None     # Required for job mode
    priority: int = 0                    # Execution priority
    merge_strategy: MergeStrategy        # APPEND | REPLACE | HUMAN_GATED
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"
    notify_on_complete: bool = True
    context_depth: ContextDepth = "full"
    task_id: str | None                  # Optional custom ID
    idempotency_key: str | None          # For deduplication
```

### Merge Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `HUMAN_GATED` | Results queued for user approval before merging | Default. Safe for sensitive operations |
| `APPEND` | Results auto-merged, accumulated with existing context | Research, data gathering |
| `REPLACE` | Results auto-merged, replacing relevant context sections | Updates, corrections |

### Proactive Report-Back (Recently Implemented)

When a background task completes with `APPEND` or `REPLACE` merge strategy:

1. Results are auto-merged into conversation context
2. A `ProactiveReportRequest` is queued
3. When foreground is idle, the agent generates a proactive message
4. User sees a new chat bubble summarizing what the background task discovered

**This works well for single tasks, but has limitations for multiple related tasks.**

---

## Problem Statement

### Scenario: Complex Multi-Task Analysis

User asks: *"Analyze our Q4 performance across sales, marketing, and operations"*

The agent decides to spawn three investigation tasks:

```python
tasks.spawn(query="Analyze Q4 sales data", merge_strategy="APPEND")
tasks.spawn(query="Analyze Q4 marketing metrics", merge_strategy="APPEND")
tasks.spawn(query="Analyze Q4 operations efficiency", merge_strategy="APPEND")
```

### Current Behavior (Fragmented)

```
Task B (marketing) completes first
  → Proactive report: "Marketing analysis shows 15% growth in lead gen..."

Task A (sales) completes
  → Proactive report: "Sales exceeded Q4 targets by 8%..."

Task C (operations) completes
  → Proactive report: "Operations efficiency improved 12%..."
```

**Problems:**
1. Three separate, disjointed messages
2. No cross-functional synthesis
3. No unified recommendations
4. The "wow" effect of autonomous collaboration is lost
5. User has to mentally piece together the full picture

### Desired Behavior (Cohesive)

```
All 3 tasks complete
  → Single proactive report:
    "I've completed the comprehensive Q4 analysis. Here's the synthesis:

    ## Executive Summary
    Strong quarter across all functions with sales +8%, marketing +15%, ops +12%...

    ## Cross-Functional Insights
    - Marketing's lead gen improvements directly contributed to sales growth
    - Ops efficiency gains freed budget for marketing campaigns

    ## Recommendations
    1. Double down on the marketing channels showing highest ROI
    2. ..."
```

### Additional Complexity: Multi-Step Spawning

The agent may not spawn all tasks in a single LLM step:

```
Agent Turn:
  Step 1: "I'll investigate this from three angles"
  Step 2: tasks.spawn("Analyze sales")
  Step 3: Call tool to check data availability
  Step 4: Based on result, tasks.spawn("Analyze marketing")
  Step 5: tasks.spawn("Analyze ops")
  Step 6: "I've dispatched the investigations"
  → Turn ends
```

All three spawns are logically related but spread across multiple planner steps.

### Additional Complexity: Retained Turn with Iteration

```
Agent Turn (retained - doesn't yield to user):
  Step 1: Spawn task_a, task_b, task_c into group
  Step 2: [WAIT for group completion]
  Step 3: Analyze combined results
  Step 4: "Based on findings, I need deeper investigation"
  Step 5: Spawn task_d, task_e into same or new group
  Step 6: [WAIT for completion]
  Step 7: Final synthesis
  → Turn ends with complete, cohesive answer
```

The agent orchestrates complex work without ever yielding to the user until done.

---

## Proposed Solution: Task Groups

### Concept

A **Task Group** is a named collection of related background tasks that:
- Are spawned together (logically, not necessarily in one call)
- Complete independently but report together
- Trigger a single, synthesized proactive report when all complete

Additional group guarantees:
- **Per-task report suppression by default**: tasks in a group do not emit their own proactive report-backs unless `group_report="any"` is explicitly chosen.
- **Stable identity via `group_id`**: `group` is treated as a display name; the runtime assigns a stable `group_id` for storage, UI, and approvals.
- **Turn-scoped name resolution**: `group="name"` only joins an OPEN group created earlier in the same foreground turn; across turns, reuse of `group` creates a new group unless `group_id` is provided explicitly.

### Visual Model

```
┌─────────────────────────────────────────────────────────┐
│  Task Group: "q4_analysis"                              │
│  Status: OPEN (accepting tasks) → SEALED → COMPLETE     │
│  Report Strategy: ALL_COMPLETE                          │
│  Merge Strategy: APPEND                                 │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │
│  │ Task A  │  │ Task B  │  │ Task C  │                 │
│  │ sales   │  │ mktg    │  │ ops     │                 │
│  │ ✓ done  │  │ running │  │ ✓ done  │                 │
│  └─────────┘  └─────────┘  └─────────┘                 │
│                                                         │
│  Sealed: YES (no more tasks will join)                  │
│  Complete: NO (waiting for Task B)                      │
└─────────────────────────────────────────────────────────┘
                         │
                         │ When all complete
                         ▼
              ┌─────────────────────┐
              │  Single Proactive   │
              │  Report with ALL    │
              │  results together   │
              └─────────────────────┘
```

---

## Design Options

### Option 1: Separate Tools for Group Management

```python
# Create group explicitly
group_id = tasks.create_group(
    name="q4_analysis",
    report_strategy="all",
    merge_strategy="APPEND"
)

# Spawn tasks into group
tasks.spawn(query="Analyze sales", group_id=group_id)
tasks.spawn(query="Analyze marketing", group_id=group_id)
tasks.spawn(query="Analyze ops", group_id=group_id)

# Seal when done adding
tasks.seal_group(group_id=group_id)
```

**Pros:**
- Clear separation of concerns
- Explicit lifecycle management

**Cons:**
- Multiple tool calls = higher latency
- LLM must remember to seal the group
- More opportunities for errors

### Option 2: Extend `tasks.spawn` (Recommended) OPTION SELECTED

```python
# First spawn creates group with settings
tasks.spawn(
    query="Analyze sales",
    group="q4_analysis",              # Creates group on first use
    # Group settings (only on creation, from config defaults otherwise)
)

# Subsequent spawns join existing group
tasks.spawn(query="Analyze marketing", group="q4_analysis")

# Final spawn seals the group
tasks.spawn(query="Analyze ops", group="q4_analysis", group_sealed=True)
```

**Pros:**
- Single tool for everything
- Lower latency
- Atomic group creation + first task
- Settings come from config defaults (LLM doesn't repeat them)

**Cons:**
- Slightly larger args model (but optional params with defaults)

**Name collision semantics (strict + long-term friendly):**
- `group="q4_analysis"` resolves to an OPEN group with that display name **created earlier in the same foreground turn**.
- If no such group exists (including “there was a group with that name earlier, but in a prior turn”), the runtime creates a **new group** with a new `group_id` but the same display name.
- Joining across turns must use `group_id` explicitly (so name reuse is always safe by default).

### Option 3: Implicit Grouping by Turn

All tasks spawned in the same foreground turn automatically form a group.

**Pros:**
- Zero cognitive load for LLM
- Automatic

**Cons:**
- Too implicit - agent may spawn unrelated tasks in same turn
- No control over which tasks are grouped
- Multi-step spawning within a turn may not be captured correctly

---

## Use Cases

### Use Case 1: Fire-and-Forget with Grouped Synthesis

Agent spawns related tasks and yields to user. When all complete, user gets one synthesized report.

```python
# Agent's tool calls
tasks.spawn(query="Research competitor A pricing", group="competitor_analysis")
tasks.spawn(query="Research competitor B pricing", group="competitor_analysis")
tasks.spawn(query="Research competitor C pricing", group="competitor_analysis", group_sealed=True)

# Agent yields turn
"I've started analyzing competitor pricing. I'll report back with a comprehensive comparison."
```

**Result:** User continues conversation. Later, receives single message: "I've completed the competitor analysis. Here's how they compare..."

### Use Case 2: Retained Turn with Inline Synthesis

Agent spawns tasks but retains the turn, waiting for results to continue reasoning.

```python
# Agent's tool calls
tasks.spawn(query="Analyze sales", group="q4", retain_turn=True)
tasks.spawn(query="Analyze marketing", group="q4", retain_turn=True)
tasks.spawn(query="Analyze ops", group="q4", group_sealed=True, retain_turn=True)

# Agent DOES NOT yield - runtime waits for group completion
# When complete, agent continues with all results in context
"Based on the comprehensive Q4 analysis I just completed..."
```

**Result:** User never sees intermediate state. Gets one complete, synthesized response.

### Use Case 3: Iterative Retained Turn

Agent spawns tasks, waits, analyzes, spawns more, waits again, then synthesizes.

```python
# Phase 1: Initial investigation
tasks.spawn(query="Get high-level Q4 metrics", group="q4_deep", retain_turn=True, group_sealed=True)

# [Runtime waits, agent resumes with results]

# Phase 2: Based on initial findings, deeper investigation
tasks.spawn(query="Deep dive into sales anomaly in October", group="q4_deep_2", retain_turn=True)
tasks.spawn(query="Investigate marketing campaign that launched in October", group="q4_deep_2", retain_turn=True, group_sealed=True)

# [Runtime waits, agent resumes with results]

# Phase 3: Final synthesis
"After investigating the Q4 metrics and drilling into the October anomaly, I found..."
```

**Result:** Complex multi-phase analysis, user gets single comprehensive answer.

### Use Case 4: Mixed - Some Grouped, Some Individual

Agent spawns some tasks that should report together, others independently.

```python
# These are grouped - report together
tasks.spawn(query="Analyze sales", group="financial")
tasks.spawn(query="Analyze costs", group="financial", group_sealed=True)

# This is independent - reports on its own
tasks.spawn(query="Check server status", merge_strategy="APPEND")  # No group
```

**Result:** Server status reports immediately when done. Financial analysis waits for both tasks.

### Use Case 5: Partial Failure Handling

One task in a group fails. What happens?

```python
tasks.spawn(query="Analyze sales", group="q4")
tasks.spawn(query="Analyze marketing", group="q4")
tasks.spawn(query="Analyze ops", group="q4", group_sealed=True)

# Marketing task fails due to API error
```

**Options:**

- B) Wait for retry (if configured) and if no success Report partial results (sales + ops) with note about failure


---

## API Design

### Extended `TasksSpawnArgs`

```python
class TasksSpawnArgs(BaseModel):
    # === Existing fields (unchanged) ===
    query: str | None = None
    mode: Literal["subagent", "job"] = "subagent"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    priority: int = 0
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"
    notify_on_complete: bool = True
    context_depth: ContextDepth = "full"
    task_id: str | None = None
    idempotency_key: str | None = None

    # === Modified: Only applies to non-grouped tasks ===
    merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED

    # === New: Task Group Support ===
    group: str | None = None
    """
    Group display name. Turn-scoped resolution:
    - Joins an OPEN group with this name created earlier in the same foreground turn
    - Otherwise creates a new group (even if a prior turn used the same name)
    """

    group_id: str | None = None
    """
    Optional stable group identifier. If provided, join that exact group.
    If omitted, `group` acts as a display name and is resolved/created per the turn-scoped name semantics.
    """

    group_sealed: bool = False
    """If True, seal group after this spawn (no more tasks can join)."""

    retain_turn: bool = False
    """
    If True, foreground agent waits for this task/group instead of yielding.

    Constraint: retain_turn requires that results can be injected without user interaction.
    This implies group_merge_strategy in {APPEND, REPLACE} (not HUMAN_GATED), and typically group_report="none"
    (because the foreground answer *is* the report).
    """

    # === New: Group settings (only apply on group creation) ===
    group_merge_strategy: MergeStrategy | None = None
    """Merge strategy for the group. Uses config default if not specified."""

    group_report: Literal["all", "any", "none"] | None = None
    """
    When to generate proactive report:
    - "all": When all tasks in sealed group complete (default for groups)
    - "any": On each task completion (current behavior, default for non-grouped)
    - "none": No proactive report (agent polls manually)
    """
```

### Task Spawn Result (Group-aware)

When a task is spawned into a group, the spawn result should surface the group identity:

```python
class TaskSpawnResult(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    group_id: str | None = None
    group: str | None = None  # display name
```

### New `TaskGroup` Model

```python
class TaskGroup(BaseModel):
    group_id: str
    name: str
    session_id: str
    status: Literal["open", "sealed", "complete", "failed"]
    merge_strategy: MergeStrategy
    report_strategy: Literal["all", "any", "none"]
    task_ids: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    sealed_at: datetime | None = None
    completed_at: datetime | None = None
    retain_turn: bool = False
    patches: list[str] = Field(default_factory=list)
    """Patch IDs produced by tasks in this group (for HUMAN_GATED bundling)."""
```

### New Tool: `tasks.seal_group` (Optional)

For cases where sealing can't be done inline:

```python
class TasksSealGroupArgs(BaseModel):
    group_id: str | None = None
    group: str | None = None  # display name; resolves to current OPEN group

@tool(desc="Seal a task group (no more tasks can join).")
async def tasks_seal_group(args: TasksSealGroupArgs, ctx: ToolContext) -> TasksSealGroupResult:
    ...
```

### New Tool: `tasks.apply_group` (Bundled Approval)

To avoid N approvals for N tasks, groups should support bundled patch application/rejection:

```python
class TasksApplyGroupArgs(BaseModel):
    group_id: str
    action: Literal["apply", "reject"] = "apply"
    strategy: MergeStrategy | None = None

class TasksApplyGroupResult(BaseModel):
    ok: bool
    action: Literal["apply", "reject"]

@tool(desc="Apply or reject all pending patches for a task group.", tags=["tasks", "background"], side_effects="stateful")
async def tasks_apply_group(args: TasksApplyGroupArgs, ctx: ToolContext) -> TasksApplyGroupResult:
    ...
```

### New Tool: `tasks.cancel_group` (Group Cancellation)

```python
class TasksCancelGroupArgs(BaseModel):
    group_id: str
    reason: str | None = None
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"

class TasksCancelGroupResult(BaseModel):
    ok: bool

@tool(desc="Cancel a task group by group_id.", tags=["tasks", "background"], side_effects="stateful")
async def tasks_cancel_group(args: TasksCancelGroupArgs, ctx: ToolContext) -> TasksCancelGroupResult:
    ...
```

---

## Runtime Behavior

### Production-Grade Invariants

The implementation should enforce these invariants (not just “best effort”):
- **Exactly-once group report**: for a given `group_id`, queue at most one group-level proactive report request (dedupe across retries/restarts).
- **No unapproved leakage**: when `group_merge_strategy=HUMAN_GATED`, group-level proactive messages must not include unapproved result content.
- **Deterministic completion**: a group becomes eligible for completion only when it is SEALED and all member tasks have reached a terminal state.
- **Atomic suppression**: for groups with `group_report="all"`, per-task proactive reporting must not occur (unless explicitly configured to `"any"`).

### Name Resolution Rules

When calling `tasks.spawn`:
- If `group_id` is provided, the task joins that exact group (error if group not found or not joinable).
- Else if `group` is provided, the runtime resolves it to an OPEN group with that display name created earlier in the same foreground turn; if none exists, it creates a new group.

Rationale: this keeps “group by name” ergonomic within a single turn, while avoiding accidental cross-turn joins when the same display name is reused long-term.

### Group Lifecycle

```
            tasks.spawn(group="X")
                     │
                     ▼
            ┌─────────────────┐
            │  Group "X"      │
   ┌───────►│  Status: OPEN   │◄────────┐
   │        └─────────────────┘         │
   │                 │                  │
   │   tasks.spawn   │   group_sealed   │  tasks.spawn
   │   (group="X")   │     =True        │  (group="X")
   │                 ▼                  │
   │        ┌─────────────────┐         │
   └────────│  Status: SEALED │─────────┘
            └─────────────────┘
                     │
                     │ All tasks complete
                     ▼
            ┌─────────────────┐
            │ Status: COMPLETE│
            └─────────────────┘
                     │
                     │ group_report="all"
                     ▼
            ┌─────────────────┐
            │ Single Proactive│
            │ Report Generated│
            └─────────────────┘
```

### Retained Turn Flow

```
Agent calls tasks.spawn(retain_turn=True)
                │
                ▼
        ┌───────────────┐
        │ Task spawned  │
        │ Return handle │
        └───────────────┘
                │
                ▼
        ┌───────────────┐
        │ Check: Is     │
        │ group sealed? │
        └───────────────┘
           │         │
           No        Yes
           │         │
           ▼         ▼
        Continue   ┌───────────────┐
        agent      │ Runtime WAITS │
        loop       │ for group     │
                   │ completion    │
                   └───────────────┘
                          │
                          │ Group completes
                          ▼
                   ┌───────────────┐
                   │ Inject results│
                   │ into context  │
                   └───────────────┘
                          │
                          ▼
                   ┌───────────────┐
                   │ Resume agent  │
                   │ loop (same    │
                   │ turn)         │
                   └───────────────┘
```

### Retain Timeout Behavior

If the runtime exceeds `retain_turn_timeout_s` while waiting on a task/group:
- Force-yield the foreground turn back to the user.
- Continue the group in the background with `group_report="all"` (or the configured group report strategy).
- Prompt the agent to tell the user that work is taking longer than expected, has been moved to the background, and it will report back when complete.

#### Background Continuation (After Timeout)

After a retain-timeout force-yield, the system should treat the eventual completion as a proactive report that can optionally continue work:
- The follow-up is **not** a resumption of the original foreground turn; it is a **background continuation run**.
- On group completion, the continuation run produces the proactive report, and may decide to spawn additional background tasks/groups if it determines more investigation is required.
- Continuations must be **bounded** (e.g., max N continuation hops with optional cooldown) to avoid runaway “background thrash”.
- If the user actively steers/cancels, the continuation must respect that (cancel/adjust rather than continuing autonomously).

### Completion Rules

Define terminal task states (implementation-specific but must be stable): `completed`, `failed`, `canceled`.

Group completion logic:
- A group is **COMPLETE** only when:
  - `status == "sealed"`, and
  - every task in `task_ids` is in a terminal state, and
  - the group completion decision has been recorded idempotently (so retries don’t double-trigger side effects).

Failure handling (configurable defaults):
- If `group_partial_on_failure=True`, a group with any failed/canceled tasks still transitions to COMPLETE and reports partial results, with an explicit failure summary.
- If `group_partial_on_failure=False`, any failed task transitions the group to FAILED (and no synthesis is generated unless an explicit “report failures” mode is chosen).

### Proactive Report Generation (Group)

When a group completes with `group_report="all"`:

1. Collect all `ContextPatch` results from group tasks
2. Build combined `ProactiveReportContext`:
   ```python
   ProactiveReportContext(
       task_id=group.group_id,  # Use group ID
       task_description=f"Task group: {group.name}",
       digest=[...combined from all tasks...],
       facts={...merged from all tasks...},
       artifacts=[...all artifacts...],
       sources=[...all sources...],
       execution_time_ms=total_time,
       merge_strategy="APPEND",  # Group's strategy
   )
   ```
3. Queue single `ProactiveReportRequest`
4. When foreground idle, generate synthesized report

**HUMAN_GATED safety rule (grouped):**
- If `group_merge_strategy=HUMAN_GATED`, do not generate a detailed synthesis bubble from unapproved results.
- Instead, generate a minimal “group complete; review/apply results” notification tied to `group_id`, and require a single bundled approval via `tasks.apply_group(group_id=...)`.
- Only after approval should the system generate (or allow the agent to generate) the synthesized narrative report.

### Idempotency and Deduplication

The system should handle duplicate tool calls (LLM retries, transport retries) safely:
- **Task-level**: existing `idempotency_key` should continue to dedupe task spawns within a session.
- **Group-level**: group completion + report emission must be deduped by `group_id` (store a “report_queued” flag/event id).
- **Bundled approval**: `tasks.apply_group` must be idempotent (re-applying should be a no-op once applied/rejected).

### Durability and Cleanup

Minimum production requirements:
- Persist `TaskGroup` state in session state alongside tasks so that restarts do not lose group membership, seal/completion status, or pending patch IDs.
- Add TTL/GC for old groups and group artifacts (e.g., prune COMPLETE groups after N hours/days, configurable).

### Observability

Emit structured lifecycle events (logs/telemetry) including `session_id`, `group_id`, and counts:
- `task_group_created`, `task_group_sealed`, `task_group_completed`, `task_group_failed`
- `task_group_report_queued`, `task_group_approval_requested`, `task_group_patches_applied`

These are critical for diagnosing “missing report”, “double report”, and “stuck open group” issues.

---

## Configuration

### `BackgroundTasksConfig` Extensions

```python
class BackgroundTasksConfig(BaseModel):
    # === Existing fields ===
    enabled: bool = False
    include_prompt_guidance: bool = True
    allow_tool_background: bool = True
    default_mode: str = "subagent"
    default_merge_strategy: str = "HUMAN_GATED"
    # ... etc ...

    # === New: Group defaults ===
    default_group_merge_strategy: MergeStrategy = MergeStrategy.APPEND
    """Default merge strategy for task groups."""

    default_group_report: Literal["all", "any", "none"] = "all"
    """Default report strategy for task groups."""

    group_timeout_s: float = 600.0
    """Timeout for group completion (seal to complete)."""

    group_partial_on_failure: bool = True
    """If True, report partial results when some tasks fail."""

    max_tasks_per_group: int = 10
    """Maximum tasks allowed in a single group."""

    auto_seal_groups_on_foreground_yield: bool = True
    """
    If True, OPEN groups created/touched in the current foreground turn are sealed when the
    foreground yields to the user. This is a reliability feature to avoid forgotten `group_sealed=True`.
    """

    retain_turn_timeout_s: float = 30.0
    """
    Maximum time the runtime will allow a foreground turn to be retained while waiting
    for tasks/groups (dev-configurable).

    If exceeded, the runtime should force-yield the turn and continue the group in the background.
    """

    background_continuation_max_hops: int = 2
    """Maximum number of background continuation cycles after a retain-timeout."""

    background_continuation_cooldown_s: float = 0.0
    """Optional delay between background continuation cycles (helps reduce UI churn)."""
```

### Prompt Guidance Extension

When groups are enabled, inject additional guidance:

```
<task_groups>
You can spawn multiple related tasks as a group for coordinated reporting:

- First spawn creates the group: tasks.spawn(query="...", group="analysis")
- Add more tasks: tasks.spawn(query="...", group="analysis")
- Seal when done adding: tasks.spawn(query="...", group="analysis", group_sealed=True)

When all tasks in a sealed group complete, you'll generate ONE synthesized report
covering all findings together, rather than separate reports for each task.

Groups auto-seal by default when you yield to the user, so you do not have to remember to set
group_sealed=True in every case.

If group_merge_strategy is HUMAN_GATED, do not summarize unapproved results. Instead, prompt for a single
group-level approval (tasks.apply_group) and only then produce a synthesis.

Use groups when:
- Tasks are investigating different aspects of the same question
- Results should be synthesized together for a cohesive answer
- The user would benefit from a unified summary rather than fragmented updates

Use retain_turn=True if you want to wait for results and continue reasoning
without yielding to the user.
</task_groups>
```

---

## Open Questions

### Decisions (v1)

1. **Auto-seal policy:** Seal groups when the foreground turn ends (yields). If the agent forgets to seal explicitly, the group stays OPEN for the duration of the turn and is sealed on yield. Do not rely on an inactivity timer.

2. **Group visibility to user:** Yes. UI should show grouped tasks and progress (e.g., "Group 'q4_analysis' — 2/3 complete").

3. **Cross-session scope:** No. Groups are strictly session-scoped.

4. **Nested groups:** Not supported.

5. **Cancellation:** Support both cancelling a whole group and cancelling tasks individually.

6. **Joining older groups:** Not needed. Joining across turns should be explicit via `group_id` only; reuse of `group` display names remains safe.

7. **Retain turn timeout:** Dev-configurable. On timeout, force-yield and continue the group in the background, instructing the agent to tell the user that work is taking long and will report back when complete.

8. **Group apply UX:** End users should not see raw patches/preview flows. Approval should be presented as a normal chat interaction (single group-level apply/reject), with the patch mechanism remaining internal.

9. **Timeout continuation semantics:** After a retain-timeout force-yield, completion triggers a proactive report that may run a bounded “background continuation” (can synthesize and optionally spawn follow-up background work). This is not a resumption of the original foreground turn.

### Still Open

1. **Continuation cancellation/steering boundary:** If the user sends a new message before the proactive continuation runs, should the continuation be canceled, deferred until idle, or require explicit user confirmation to proceed?

---

## Implementation Plan

### Phase 1: Core Group Infrastructure
- Add `TaskGroup` model
- Extend `TasksSpawnArgs` with group fields
- Implement group creation/join/seal logic in `TaskService`
- Store groups in session state
- Enforce strict group-name resolution rules (display name → current OPEN group or new group)
- Add group lifecycle invariants + idempotent report emission flags in state
- Add `tasks.cancel_group` tool + service support

### Phase 2: Coordinated Reporting
- Modify proactive report queue to handle groups
- Implement combined `ProactiveReportContext` building
- Update prompt guidance
- Add HUMAN_GATED group safety rule + bundled approval (`tasks.apply_group`)
- Add regression tests for: no per-task report, exactly-once group report, no HUMAN_GATED leakage
- Add UI status surface for groups (counts + progress + awaiting approval)

### Phase 3: Retained Turn Support
- Implement runtime wait logic for `retain_turn=True`
- Handle group completion detection
- Resume agent loop with injected results
- Enforce `retain_turn` constraints (no HUMAN_GATED + typically suppress proactive report)
- Add timeout behavior + cancellation propagation tests
- Implement `retain_turn_timeout_s` fallback (force-yield + continue in background + prompt agent to inform user)
- Implement bounded background continuation after timeout (max hops + cooldown + respect user steering/cancel)

### Phase 4: Configuration & Polish
- Add config fields to `BackgroundTasksConfig`
- Update templates
- Add comprehensive tests
- Documentation
- Add observability hooks (structured events/telemetry)

---

## References

- [RFC_BIDIRECTIONAL_PROTOCOL.md](./RFC_BIDIRECTIONAL_PROTOCOL.md) - Steering and background task protocol
- [penguiflow/sessions/task_tools.py](../penguiflow/sessions/task_tools.py) - Current task meta-tools
- [penguiflow/planner/prompts.py](../penguiflow/planner/prompts.py) - Background task prompt guidance
