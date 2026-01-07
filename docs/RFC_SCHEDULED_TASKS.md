# RFC: Production-Ready Scheduled Tasks

## Status: Draft

## Summary

This RFC proposes a production-ready scheduled task system for PenguiFlow that enables agents to create recurring background tasks (e.g., "run this analysis every Monday at 9am"). The design prioritizes:

- **Library/framework philosophy**: Opt-in mechanisms, not forced infrastructure
- **StateStore-aligned storage**: Scheduling is an *optional capability* implemented by downstream `StateStore` adapters (Postgres, KV, etc.)
- **Distributed safety**: At-least-once execution with idempotent run records + claim/lease semantics
- **Reliability**: Configurable retry policies with user alerting on failures
- **Playground showcase**: Reference implementation in CLI playground

Key v1 decisions (captured in this RFC):
- **Catch-up policy**: dev-configurable, default `run_once` (do not replay a backlog).
- **Execution semantics**: at-least-once; scheduled payloads must be idempotent (especially for side-effect tools).
- **Approval**: `HUMAN_GATED` is disabled for scheduled tasks; scheduled outputs are auto-merged (`APPEND`) into a delivery session/inbox.
- **Delivery/alerting**: pluggable `DeliverySink`/`AlertSink`; Playground delivers to a notification pane targeting `owner_user_id`, with “open” creating a new session.
- **Distributed safety**: claim creates a run record; reclaim is possible after lease expiry to prevent permanent hangs.

---

## Table of Contents

1. [Motivation](#motivation)
2. [Current State](#current-state)
3. [Design Principles](#design-principles)
4. [Architecture](#architecture)
5. [Data Models](#data-models)
6. [State Store Integration](#state-store-integration)
7. [Distributed Execution Safety](#distributed-execution-safety)
8. [Retry and Failure Handling](#retry-and-failure-handling)
9. [Agent Meta-Tools](#agent-meta-tools)
10. [Configuration](#configuration)
11. [Playground Integration](#playground-integration)
12. [Implementation Plan](#implementation-plan)

---

## Motivation

### User Story

> "Run this sales analysis every Monday morning and notify me of any anomalies."

The agent should be able to:
1. Create a scheduled job that persists across restarts
2. Execute reliably even in distributed deployments
3. Retry on transient failures
4. Alert the user if the job consistently fails
5. Deliver results to the appropriate session/user

### Why This Matters

- **Autonomous agents**: Agents that can set up their own recurring workflows
- **Proactive insights**: Regular analysis without user prompting
- **Enterprise readiness**: Production deployments need reliable scheduling

---

## Current State

### Existing Infrastructure (`penguiflow/sessions/scheduler.py`)

```python
# Models exist
ScheduleConfig(interval_s, next_run_at, timezone)
JobDefinition(job_id, session_id, task_payload, schedule, enabled)
JobRunRecord(job_id, run_id, started_at, completed_at, status, result)

# Interfaces exist
JobStore(Protocol)  # save_job, list_jobs, list_due, record_run
JobScheduler        # tick() polls and spawns
JobSchedulerRunner  # Background loop

# Only in-memory implementation
InMemoryJobStore    # Lost on restart
```

### Gaps

| Gap | Impact |
|-----|--------|
| No persistent store | Jobs lost on restart |
| No agent meta-tool | Agents can't create schedules |
| No cron expressions | Only simple intervals |
| No distributed safety | Double-execution in multi-instance |
| No retry policy | Silent failures |
| No user alerting | Failures go unnoticed |

---

## Design Principles

### 1. Library-First, Not Infrastructure-First

PenguiFlow is a framework. Downstream teams:
- May have their own job schedulers (Airflow, Temporal, Celery Beat)
- May not need scheduling at all
- Should not be forced to run a scheduler process

**Approach**: Opt-in at multiple levels:
- `ScheduledTasksConfig.enabled = False` by default
- Bring-your-own `JobStore` implementation
- Scheduler runner is optional (can use external trigger)

### 2. StateStore-Aligned Storage (Capability-Based)

Don't introduce a new mandatory persistence system. Instead, scheduled tasks require a downstream
store capability that fits the existing PenguiFlow `StateStore` pattern (capabilities detected via
optional protocols).

**Important:** PenguiFlow's `StateStore` protocol is not a generic KV store. This RFC does not
extend `StateStore` with `get/set/list_keys/set_nx` methods.

Approach:
- Define a `SupportsScheduledTasks` protocol (or equivalent) for job persistence + atomic claim/lease.
- Downstream teams can implement it using Postgres (recommended for production) or a KV store (Redis, etc.).

### 3. Idempotency Over Complexity

In distributed environments, prefer simple idempotency patterns:
- Claim-before-execute with TTL
- Idempotency keys for job runs
- At-least-once delivery with deduplication

### 4. Fail Loud, Not Silent

When jobs fail:
- Retry with backoff (configurable)
- After max retries, alert the user
- Never silently drop scheduled work

---

## Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Agent Turn                                    │
│                                                                      │
│  "Schedule this analysis for every Monday at 9am"                   │
│                           │                                          │
│                           ▼                                          │
│                  tasks.schedule(...)                                 │
│                           │                                          │
└───────────────────────────│──────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     JobDefinition                                    │
│                                                                      │
│  job_id: "job_abc123"                                               │
│  query: "Analyze weekly sales data and report anomalies"            │
│  schedule: cron="0 9 * * MON", timezone="America/New_York"          │
│  retry_policy: max_attempts=3, backoff="exponential"                │
│  owner_user_id: "user_xyz"                                          │
│                           │                                          │
└───────────────────────────│──────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      StateStore                                      │
│                                                                      │
│  Key: "penguiflow:jobs:job_abc123"                                  │
│  Value: {JobDefinition JSON}                                        │
│                                                                      │
│  Key: "due_index:1704067200"  (store-specific due index)          │
│  Value: ["job_abc123", "job_def456"]                                │
│                                                                      │
└───────────────────────────│──────────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────────┐                 ┌───────────────────┐
│  Scheduler Tick   │                 │  External Trigger │
│  (built-in loop)  │                 │  (webhook, cron)  │
└───────────────────┘                 └───────────────────┘
        │                                       │
        └───────────────────┬───────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Claim & Execute                                  │
│                                                                      │
│  1. Attempt to start run (run record + lease via store capability)  │
│  2. If claimed, spawn background task                                │
│  3. On completion, record run, update next_run_at                   │
│  4. On failure, apply retry policy or alert user                    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Result Delivery                                  │
│                                                                      │
│  Option A: Queue to owner's session (if active)                     │
│  Option B: Push notification / email                                 │
│  Option C: Webhook to external endpoint                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Provided By |
|-----------|---------------|-------------|
| `JobDefinition` | Data model for scheduled jobs | PenguiFlow (core) |
| `SupportsScheduledTasks` | Storage capability interface | PenguiFlow (protocol) |
| In-memory scheduled store | Reference impl for Playground/tests | PenguiFlow (dev) |
| `JobScheduler` | Poll and trigger logic | PenguiFlow (core) |
| Run lease/claim logic | Claim + reclaim behavior | PenguiFlow (core) |
| `tasks.schedule` | Agent meta-tool | PenguiFlow (core) |
| Scheduler Runner | Background tick loop | Downstream (opt-in) |
| External Trigger | Webhook/cron endpoint | Downstream (opt-in) |

---

## Data Models

### `ScheduleConfig` (Extended)

```python
class ScheduleConfig(BaseModel):
    """When and how often a job runs."""

    # Timing (one of these required)
    cron: str | None = None
    """Cron expression (e.g., "0 9 * * MON" = Monday 9am)."""

    interval_s: int | None = None
    """Simple interval in seconds."""

    # Timezone
    timezone: str = "UTC"
    """IANA timezone for cron interpretation."""

    # Computed
    next_run_at: datetime | None = None
    """Next scheduled execution time (managed by system)."""

    catchup_policy: Literal["run_once", "skip", "run_all"] = "run_once"
    """
    How to handle missed runs after downtime:
    - run_once (default): run at most one overdue execution, then advance next_run_at past now
    - skip: do not run overdue executions; only advance next_run_at past now
    - run_all: run each missed execution up to a cap (dev-configurable)
    """

    @model_validator(mode="after")
    def _validate_schedule(self) -> ScheduleConfig:
        if self.cron is None and self.interval_s is None:
            raise ValueError("Either cron or interval_s must be specified")
        return self

    def compute_next(self, after: datetime) -> datetime:
        """Compute next run time after given datetime."""
        if self.cron:
            from croniter import croniter
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(self.timezone)
            local_after = after.astimezone(tz)
            cron = croniter(self.cron, local_after)
            return cron.get_next(datetime).astimezone(UTC)
        elif self.interval_s:
            return after + timedelta(seconds=self.interval_s)
        raise ValueError("No schedule configured")
```

### Lease / Hang Recovery

Scheduled runs use a lease to prevent permanent stalls and enable safe reclaim:

```python
class LeaseConfig(BaseModel):
    lease_ttl_s: int = 300
    """How long a claim is valid without renewal."""

    reclaim_grace_s: int = 30
    """Extra grace window before a run is considered abandoned and reclaimable."""
```

### `RetryPolicy`

```python
class RetryPolicy(BaseModel):
    """Configurable retry behavior for failed job runs."""

    max_attempts: int = 3
    """Maximum execution attempts before marking as failed."""

    backoff: Literal["none", "linear", "exponential"] = "exponential"
    """Backoff strategy between retries."""

    initial_delay_s: float = 60.0
    """Delay before first retry."""

    max_delay_s: float = 3600.0
    """Maximum delay between retries."""

    retry_on: list[str] = Field(default_factory=lambda: ["transient", "timeout"])
    """Error categories that trigger retry (vs immediate failure)."""

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate delay for given attempt number (1-indexed)."""
        if self.backoff == "none":
            return self.initial_delay_s
        elif self.backoff == "linear":
            return min(self.initial_delay_s * attempt, self.max_delay_s)
        else:  # exponential
            return min(self.initial_delay_s * (2 ** (attempt - 1)), self.max_delay_s)
```

### `JobDefinition` (Extended)

```python
class JobDefinition(BaseModel):
    """A scheduled recurring task."""

    # Identity
    job_id: str = Field(default_factory=lambda: f"job_{secrets.token_hex(8)}")
    idempotency_key: str | None = None
    """Optional key for deduplication during creation."""

    # Ownership
    owner_user_id: str | None = None
    """User who created the job (for result delivery)."""

    session_id: str | None = None
    """Original session (may be stale for long-running jobs)."""

    # What to execute
    query: str | None = None
    """Task instruction (for subagent mode)."""

    mode: Literal["subagent", "job"] = "subagent"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None

    # Schedule
    schedule: ScheduleConfig

    # Execution settings
    merge_strategy: MergeStrategy = MergeStrategy.APPEND
    """
    Scheduled tasks must not require interactive approval. `HUMAN_GATED` is disabled.
    Scheduled outputs are delivered via DeliverySink and auto-merged into that delivery context.
    """
    context_depth: ContextDepth = "minimal"
    """Scheduled jobs typically use minimal context (no conversation history)."""

    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    lease: LeaseConfig = Field(default_factory=LeaseConfig)

    # Delivery
    delivery: DeliveryConfig = Field(default_factory=lambda: DeliveryConfig())

    # Lifecycle
    enabled: bool = True
    max_runs: int | None = None
    """Maximum total runs (None = unlimited)."""

    expires_at: datetime | None = None
    """Job auto-disables after this time."""

    # Metadata
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    run_count: int = 0
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    consecutive_failures: int = 0
```

### `DeliveryConfig`

```python
class DeliveryConfig(BaseModel):
    """How to deliver job results to the user."""

    mode: Literal["inbox", "session", "webhook", "email"] = "inbox"
    """
    - inbox: Add an item to the user's notification/inbox surface (default)
    - session: Queue to owner's active session (or hold until active)
    - webhook: POST to external URL
    - email: Send email summary
    """

    webhook_url: str | None = None
    email_address: str | None = None

    # Session delivery options
    create_session_if_none: bool = False
    """If no active session, create a new one for delivery."""

    hold_until_active: bool = True
    """If session delivery and no active session, hold results until user returns."""
```

### `JobRunRecord` (Extended)

```python
class JobRunRecord(BaseModel):
    """Record of a single job execution."""

    job_id: str
    run_id: str = Field(default_factory=lambda: f"run_{secrets.token_hex(8)}")

    # Timing
    scheduled_at: datetime
    """When the run was supposed to happen."""

    started_at: datetime | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None

    # Execution
    claimed_by: str | None = None
    """Instance ID that claimed this run (for distributed coordination)."""

    lease_expires_at: datetime | None = None
    """When the current lease expires (used for hang recovery)."""

    heartbeat_at: datetime | None = None
    """Optional last heartbeat timestamp (if lease renewal is supported)."""

    attempt: int = 1
    """Which attempt this is (1 = first try, 2+ = retry)."""

    # Result
    status: Literal["pending", "claimed", "running", "success", "failed", "retrying", "abandoned"] = "pending"
    error_category: str | None = None
    """transient, timeout, permanent, etc."""

    error_message: str | None = None
    task_id: str | None = None
    """Background task ID if spawned."""

    result_summary: str | None = None
```

---

## State Store Integration

### Capability: `SupportsScheduledTasks` (StateStore-Aligned)

Scheduled tasks must follow PenguiFlow's “optional capability” pattern rather than extending the base
`StateStore` protocol with KV-like primitives.

Define a store capability that downstream adapters can implement using Postgres or KV stores:

```python
class SupportsScheduledTasks(Protocol):
    async def save_job(self, job: JobDefinition) -> None: ...
    async def get_job(self, job_id: str) -> JobDefinition | None: ...
    async def list_jobs(self, *, owner_user_id: str | None = None) -> list[JobDefinition]: ...

    async def list_due(self, now: datetime, *, limit: int = 200) -> list[tuple[str, datetime]]:
        """
        Return (job_id, scheduled_at) pairs due for execution.
        `scheduled_at` is the effective run time (usually the job's `schedule.next_run_at`).

        v1: Implementations may use naive scans for small scale.
        Future: stores can provide indexed due queries.
        """

    async def try_start_run(
        self,
        *,
        job_id: str,
        scheduled_at: datetime,
        claimer_id: str,
        lease_ttl_s: int,
    ) -> JobRunRecord | None:
        """
        Atomically:
        - create a run record if it doesn't exist for (job_id, scheduled_at, attempt)
        - claim/lease the run for this claimer

        Returns the claimed run record, or None if already started/claimed.
        """

    async def renew_run_lease(self, *, run_id: str, claimer_id: str, lease_ttl_s: int) -> bool: ...
    async def complete_run(self, *, run_id: str, claimer_id: str, status: str, result_summary: str | None) -> None: ...

    async def mark_abandoned_runs(self, *, now: datetime, limit: int = 200) -> int:
        """Mark expired leased runs as abandoned so they can be retried (safety net)."""
```

Backend flexibility:
- Postgres: unique constraints + row-level updates for `try_start_run`.
- KV: compare-and-set / set-if-not-exists primitives to implement run creation + lease.

### KV Store Key Schema (Reference Only)

For teams implementing `SupportsScheduledTasks` on a KV store, a key schema like the following can work,
but it is not mandated by PenguiFlow:

```
penguiflow:jobs:{job_id}                    → JobDefinition JSON
penguiflow:jobs:by_user:{user_id}           → Set of job_ids
penguiflow:runs:{job_id}:{scheduled_at_iso} → JobRunRecord JSON (unique per scheduled run)
penguiflow:leases:{run_id}                  → claimer_id (TTL)
penguiflow:due_index                         → implementation-specific
```

---

## Distributed Execution Safety

### Problem

In a distributed deployment with N instances, all running scheduler ticks:
- Instance A sees job due, spawns task
- Instance B sees same job due, spawns duplicate task

### Solution: Run Record + Lease (Claim-Before-Execute)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Instance A  │     │ Instance B  │     │ Instance C  │
└─────────────┘     └─────────────┘     └─────────────┘
      │                   │                   │
      │ tick()            │ tick()            │ tick()
      ▼                   ▼                   ▼
   list_due()          list_due()          list_due()
      │                   │                   │
      │ job_123 due       │ job_123 due       │ job_123 due
      ▼                   ▼                   ▼
   try_start_run()     try_start_run()     try_start_run()
      │                   │                   │
      │ ✓ CLAIMED         │ ✗ ALREADY         │ ✗ ALREADY
      │   (run created)   │   STARTED         │   STARTED
      ▼                   │                   │
   spawn_task()          skip               skip
      │
      ▼
   update_run(task_id)
   renew_lease_while_running()
   complete_run()
   update_next_run_at()
```

### Hang Recovery Safety Net

If an instance crashes or a run hangs:
- The run lease expires.
- The scheduler marks the run as `abandoned` (or `failed`) and schedules a retry based on `RetryPolicy`.
- A new instance can then `try_start_run` again (with a new attempt) to continue progress.

### Production-Grade Invariants

The implementation should enforce and document these invariants:
- **At-least-once execution**: a given scheduled run may execute more than once (due to lease expiry/reclaim).
- **Idempotent run identity**: `(job_id, scheduled_at, attempt)` is a stable identity for a run record (dedupe retries/tool-call replays).
- **No interactive approval**: scheduled jobs must not block on user input; `HUMAN_GATED` is disabled.
- **Bounded catch-up**: missed runs never cause unbounded replay; default `run_once` and `run_all` must be capped.

### Idempotency Key

Jobs can specify an `idempotency_key` to prevent duplicate creation:

```python
# Agent creates schedule
tasks.schedule(
    query="Weekly sales analysis",
    cron="0 9 * * MON",
    idempotency_key="weekly_sales_v1",  # Agent-chosen
)

# If called again with same key, returns existing job (no duplicate)
```

---

## Retry and Failure Handling

### Retry Flow

```
Job Execution
     │
     ▼
  Success? ──Yes──► Record success, schedule next run
     │
     No
     ▼
  Error category?
     │
     ├── transient ──► Retry with backoff
     │                      │
     │                      ▼
     │               attempts < max?
     │                  │        │
     │                 Yes       No
     │                  │        │
     │                  ▼        ▼
     │             Schedule    Mark failed
     │              retry      Alert user
     │
     ├── timeout ──► Same as transient
     │
     └── permanent ──► Mark failed, alert user immediately
```

### Error Categories

| Category | Examples | Retry? |
|----------|----------|--------|
| `transient` | Network error, rate limit, 503 | Yes |
| `timeout` | Execution exceeded deadline | Yes |
| `permanent` | Invalid query, auth failure, 4xx | No |
| `cancelled` | User/system cancellation | No |

### User Alerting

When a job fails permanently or exhausts retries:

```python
class JobFailureAlert(BaseModel):
    job_id: str
    job_query: str
    failure_reason: str
    attempts_made: int
    last_error: str
    recommended_action: str

# Delivered via configured channel
await alert_service.notify_job_failure(
    user_id=job.owner_user_id,
    alert=JobFailureAlert(
        job_id=job.job_id,
        job_query=job.query,
        failure_reason="Max retries exceeded",
        attempts_made=3,
        last_error="API rate limit (429)",
        recommended_action="Check API quota or increase retry delay",
    ),
)
```

---

## Agent Meta-Tools

### `tasks.schedule`

```python
class TasksScheduleArgs(BaseModel):
    # What to run
    query: str
    """Task instruction for the scheduled job."""

    mode: Literal["subagent", "job"] = "subagent"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None

    # When to run (one required)
    cron: str | None = None
    """Cron expression (e.g., "0 9 * * MON")."""

    interval_s: int | None = None
    """Simple interval in seconds."""

    timezone: str = "UTC"

    # Optional settings
    catchup_policy: Literal["run_once", "skip", "run_all"] | None = None
    """Override schedule catch-up behavior (defaults come from config)."""

    idempotency_key: str | None = None
    max_runs: int | None = None
    expires_at: datetime | None = None

    # Delivery (uses defaults if not specified)
    delivery_mode: Literal["inbox", "session", "webhook", "email"] | None = None

class TasksScheduleResult(BaseModel):
    job_id: str
    created: bool
    """False if idempotency_key matched existing job."""
    next_run_at: datetime

@tool(
    desc="Schedule a recurring background task.",
    tags=["tasks", "scheduling"],
    side_effects="stateful",
)
async def tasks_schedule(args: TasksScheduleArgs, ctx: ToolContext) -> TasksScheduleResult:
    ...
```

Notes:
- `owner_user_id` should be derived from authenticated runtime context (not provided by the LLM).
- Scheduled tasks do not support `HUMAN_GATED`; delivery is automatic via the configured delivery sink.

### `tasks.list_schedules`

```python
class TasksListSchedulesArgs(BaseModel):
    enabled_only: bool = True

class TasksListSchedulesResult(BaseModel):
    jobs: list[JobSummary]

class JobSummary(BaseModel):
    job_id: str
    query: str
    cron: str | None
    interval_s: int | None
    next_run_at: datetime | None
    enabled: bool
    last_run_status: str | None
    run_count: int
```

### `tasks.pause_schedule` / `tasks.resume_schedule`

```python
class TasksPauseScheduleArgs(BaseModel):
    job_id: str

class TasksPauseScheduleResult(BaseModel):
    ok: bool
    was_enabled: bool
```

### `tasks.delete_schedule`

```python
class TasksDeleteScheduleArgs(BaseModel):
    job_id: str

class TasksDeleteScheduleResult(BaseModel):
    ok: bool
```

---

## Configuration

### `ScheduledTasksConfig`

```python
class ScheduledTasksConfig(BaseModel):
    """Configuration for scheduled task functionality."""

    # Master switch
    enabled: bool = False
    """Enable scheduled task meta-tools and scheduler."""

    # Scheduler settings
    tick_interval_s: float = 10.0
    """How often the scheduler checks for due jobs."""

    claim_ttl_s: int = 300
    """How long a run lease is held before expiring (unless renewed)."""

    default_catchup_policy: Literal["run_once", "skip", "run_all"] = "run_once"
    """Default missed-run handling for schedules."""

    max_backlog_runs: int = 5
    """Cap for run_all catch-up."""

    # Limits
    max_jobs_per_user: int = 50
    """Maximum scheduled jobs per user."""

    max_concurrent_scheduled: int = 10
    """Maximum scheduled jobs executing concurrently."""

    # Retry defaults
    default_retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)

    # Delivery defaults
    default_delivery_mode: Literal["inbox", "session", "webhook", "email"] = "inbox"

    # Safety: scheduled tool allowlist
    allowed_scheduled_tools: list[str] = Field(default_factory=list)
    """
    Optional allowlist for job-mode scheduled executions. If empty, downstream policy decides.
    Recommended for production: allow only idempotent, low-risk tools.
    """

    # Prompt guidance
    include_prompt_guidance: bool = True
    """Include scheduling guidance in agent prompts."""
```

### Policy, Delivery, and Alert Hooks

To keep PenguiFlow library-first, scheduling must integrate via lightweight protocols:

```python
class ScheduledTasksPolicy(Protocol):
    async def can_create_schedule(self, *, owner_user_id: str) -> bool: ...
    async def can_run_scheduled_tool(self, *, owner_user_id: str, tool_name: str) -> bool: ...
    async def max_jobs_for_user(self, *, owner_user_id: str) -> int | None: ...
```

```python
class DeliverySink(Protocol):
    async def deliver_job_result(
        self,
        *,
        owner_user_id: str,
        job: JobDefinition,
        run: JobRunRecord,
        summary: str,
    ) -> None: ...
```

```python
class AlertSink(Protocol):
    async def notify_job_failure(
        self,
        *,
        owner_user_id: str,
        job: JobDefinition,
        run: JobRunRecord,
        message: str,
    ) -> None: ...
```

Playground default:
- `DeliverySink` writes to the notification pane (inbox) scoped to `owner_user_id`.
- Opening a notification creates (or navigates to) a new session with the run’s summary appended.

### Environment Variables

```bash
# Enable scheduling
SCHEDULED_TASKS_ENABLED=true

# Scheduler tuning
SCHEDULED_TASKS_TICK_INTERVAL_S=10
SCHEDULED_TASKS_CLAIM_TTL_S=300

# Catch-up policy
SCHEDULED_TASKS_DEFAULT_CATCHUP_POLICY=run_once
SCHEDULED_TASKS_MAX_BACKLOG_RUNS=5

# Limits
SCHEDULED_TASKS_MAX_JOBS_PER_USER=50
SCHEDULED_TASKS_MAX_CONCURRENT=10

# Retry defaults
SCHEDULED_TASKS_DEFAULT_MAX_ATTEMPTS=3
SCHEDULED_TASKS_DEFAULT_BACKOFF=exponential

# Delivery defaults
SCHEDULED_TASKS_DEFAULT_DELIVERY_MODE=inbox

# Safety (optional)
SCHEDULED_TASKS_ALLOWED_SCHEDULED_TOOLS="http.get,openapi.call"
```

---

## Playground Integration

### Showcase Features

The CLI playground should demonstrate:

1. **Schedule creation UI**: Button to create schedule from conversation
2. **Schedule list view**: Table of user's scheduled jobs
3. **Run history**: View past executions and results
4. **Manual trigger**: "Run now" button for testing
5. **Pause/resume/delete**: Management controls

### Implementation

```python
# In playground.py

@app.post("/api/schedules")
async def create_schedule(request: CreateScheduleRequest):
    """Create a scheduled job (mirrors tasks.schedule meta-tool)."""
    ...

@app.get("/api/schedules")
async def list_schedules(user_id: str):
    """List scheduled jobs for user."""
    ...

@app.post("/api/schedules/{job_id}/trigger")
async def trigger_schedule(job_id: str):
    """Manually trigger a scheduled job."""
    ...

@app.patch("/api/schedules/{job_id}")
async def update_schedule(job_id: str, request: UpdateScheduleRequest):
    """Pause, resume, or modify a scheduled job."""
    ...
```

### UI Components

```svelte
<!-- ScheduleList.svelte -->
<script>
  let schedules = [];

  async function loadSchedules() {
    schedules = await api.listSchedules();
  }
</script>

<div class="schedule-list">
  {#each schedules as job}
    <div class="schedule-item">
      <span class="query">{job.query}</span>
      <span class="schedule">{job.cron || `Every ${job.interval_s}s`}</span>
      <span class="next-run">{formatDate(job.next_run_at)}</span>
      <span class="status" class:enabled={job.enabled}>
        {job.enabled ? 'Active' : 'Paused'}
      </span>
      <button on:click={() => togglePause(job)}>
        {job.enabled ? 'Pause' : 'Resume'}
      </button>
      <button on:click={() => triggerNow(job)}>Run Now</button>
    </div>
  {/each}
</div>
```

---

## Implementation Plan

### Phase 0: Storage Capability + Semantics

- Define `SupportsScheduledTasks` protocol (capability-based, StateStore-aligned)
- Update scheduler contracts to use **run records + leases** (at-least-once)
- Add minimal in-memory implementation for Playground/testing
- Add reclaim logic for abandoned/expired runs (safety net)

### Phase 1: Core Models and Scheduling Math

- Extend `ScheduleConfig` with cron support (`croniter` optional dependency)
- Use `zoneinfo` for timezone handling; document DST behavior as “cron library semantics”
- Add `catchup_policy` (default `run_once`) + `max_backlog_runs` cap for `run_all`
- Add `RetryPolicy`, `LeaseConfig`, `DeliveryConfig` models
- Extend `JobDefinition` with new fields and constraints (disable `HUMAN_GATED`)

### Phase 2: Distributed Safety (Backend Implementations)

- Implement `try_start_run` + lease renewal in store backends (Postgres/KV, downstream-provided)
- Ensure completion is idempotent and safe under retries
- Add multi-instance tests: dup suppression + reclaim after crash/hang
- Add idempotency key handling for job creation

### Phase 3: Agent Meta-Tools

- Implement `tasks.schedule`
- Implement `tasks.list_schedules`
- Implement `tasks.pause_schedule`, `tasks.resume_schedule`
- Implement `tasks.delete_schedule`
- Add prompt guidance

### Phase 4: Retry and Alerting

- Implement retry logic in scheduler
- Add error categorization
- Implement user alerting via `AlertSink` protocol
- Implement result delivery via `DeliverySink` protocol (default: inbox → create/open session)

### Phase 5: Playground Integration

- Add schedule management endpoints
- Build schedule list UI component
- Add manual trigger support
- Add run history view

### Phase 6: Documentation and Templates

- Update template `config.py.jinja` files
- Add scheduling section to CLAUDE.md
- Create example scheduled job flows
- Write integration tests

---

## Open Questions

### Decisions (v1)

1. **Catch-up policy after downtime:** Dev-configurable. Default: `run_once`.

2. **Distributed execution safety:** Use `try_start_run` to atomically create a run record and lease it. Add reclaim on lease expiry to prevent permanent hangs.

3. **Execution semantics:** At-least-once. Require idempotent payloads/tools for scheduled work (especially for side effects).

4. **Approval/merge:** Disable `HUMAN_GATED` for scheduled tasks. Scheduled outputs are auto-merged (`APPEND`) into the delivery surface/session.

5. **Result delivery:** Define `DeliverySink`/`AlertSink` protocols. Default delivery is user inbox/notification surface (Playground: notification pane targeting `owner_user_id`, opening creates a new session).

6. **Security/permissions:** Dev-configured via policy hooks (outside core library scope). Downstream controls who can schedule, what tools are allowed, and quotas.

7. **Scaling `list_due`:** v1 frames scanning/naive implementations as acceptable for small scale; future iterations can add indexed due queries.

### Still Open

1. **Cron/DST semantics:** Confirm `croniter` + `zoneinfo` is sufficient, and define expected behavior on DST transitions.

2. **Credentials/tokens:** Define minimal hooks for auth context in scheduled runs (e.g., resolve OAuth tokens by `owner_user_id`) while leaving rotation/revocation to downstream.

3. **Delivery fallback:** If a delivery attempt fails (e.g., webhook down), should the system queue indefinitely, retry with backoff, or mark delivery failed and alert?

4. **Job ownership transfer / teams:** Can jobs be transferred to another user or owned by teams/orgs (not just a user_id)?

---

## References

- [RFC_TASK_GROUPS.md](./RFC_TASK_GROUPS.md) - Related: coordinated background tasks
- [penguiflow/sessions/scheduler.py](../penguiflow/sessions/scheduler.py) - Existing scheduler infrastructure
- [croniter](https://github.com/kiorky/croniter) - Cron expression library
