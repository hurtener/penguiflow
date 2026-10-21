# The `TaskService` Protocol

`tasks.*` tools delegate to whatever object is in `tool_context["task_service"]`. The library defines the protocol; you (or your platform) supply the implementation.

## What `TaskService` must do

At a high level:
- Spawn a background task (subagent or job mode).
- Track task lifecycle: PENDING → RUNNING → COMPLETED / FAILED / CANCELLED.
- Stream observations / context patches back when tasks complete.
- Support cancellation and prioritization.
- Group tasks; seal and apply groups.
- Enforce limits configured in `BackgroundTasksConfig`.

## Method surface (signatures)

```python
class TaskService(Protocol):
    async def spawn(
        self,
        *,
        session_id: str,
        mode: str,                   # "subagent" | "job"
        prompt: str | None = None,       # for subagent mode
        tool_name: str | None = None,    # for job mode
        tool_args: dict | None = None,   # for job mode
        merge_strategy: str = "HUMAN_GATED",
        group: str | None = None,
        timeout_s: float | None = None,
        priority: int = 0,
        metadata: dict | None = None,
    ) -> str: ...                        # returns task_id

    async def list(self, *, session_id: str, status: str | None = None) -> list[dict]: ...

    async def get(self, task_id: str) -> dict | None: ...

    async def cancel(self, task_id: str) -> bool: ...

    async def prioritize(self, task_id: str, priority: int) -> None: ...

    async def seal_group(self, *, session_id: str, group: str) -> None: ...

    async def list_groups(self, *, session_id: str) -> list[dict]: ...

    async def apply_group(
        self, *, session_id: str, group: str, merge_strategy: str | None = None,
    ) -> dict: ...                       # returns merge summary
```

Exact attribute names and types come from `penguiflow.sessions.task_service`. The protocol is duck-typed — implement these methods on any object.

## In-process reference implementation

PenguiFlow ships an in-process `TaskService` suitable for development:
- Tasks run as `asyncio.Task`s within the same process.
- Lifecycle is tracked in-memory.
- No persistence across restarts.

Wire it like:
```python
from penguiflow.sessions.task_service import InProcessTaskService

task_service = InProcessTaskService(
    planner=foreground_planner,
    max_concurrent=4,
    max_per_session=20,
)

# pass on every planner.run(...)
result = await foreground_planner.run(
    user_message,
    tool_context={"session_id": "...", "task_service": task_service},
)
```

This works for: dev playground, single-process production, low-volume agents. It doesn't work for: multi-worker deployments, anything needing task recovery after restart.

## Production patterns

### Pattern 1: persistent in-process

In-process `TaskService` + `StateStore` for durability. The service:
- Writes task state to the store on every transition.
- Hydrates from the store on startup (recovers PENDING/RUNNING tasks).
- Continues to execute tasks in `asyncio`.

Survives process restart. Doesn't scale across workers.

### Pattern 2: distributed queue

Wrap a real queue (Redis, RabbitMQ, SQS) and a worker pool:
- `TaskService.spawn` enqueues a task message.
- A worker process subscribes, runs subagent/job, posts result back.
- The foreground planner polls (or subscribes to) task state via `TaskService.list` / `get`.

Scales horizontally. Adds infrastructure dependency. State must live in the queue + a metadata store.

### Pattern 3: subprocess executor

Spawn subprocesses for jobs (e.g., long Python scripts, sandboxed code). Useful for:
- Memory isolation.
- CPU-bound work that would block `asyncio`.
- Sandboxing untrusted code.

`TaskService.spawn` launches the subprocess and tracks its handle. Cancellation kills the process.

## Spawn flow walkthrough

```
foreground tool calls tasks.spawn(mode="subagent", prompt="...", group="g1")
    │
    ▼
TaskService.spawn(...)
    1. Validate limits (max_concurrent, max_per_session)
    2. Create task record (PENDING)
    3. Persist (if durable backend)
    4. Schedule execution
    5. Return task_id
    │
    ▼
foreground continues
    │
    ▼
(background) execution
    1. Mark RUNNING, persist
    2. Run subagent or tool
    3. Capture result
    4. Mark COMPLETED/FAILED, persist
    5. If APPEND/REPLACE: apply ContextPatch to foreground context
    6. If HUMAN_GATED: queue for later apply
    7. If grouped: increment group count; maybe auto-seal
```

## Cancellation

`tasks.cancel(task_id)`:
- PENDING → mark CANCELLED, don't execute.
- RUNNING → cancel the in-flight `asyncio.Task` (cooperative; same caveats as [[penguiflow-core-flows]] cancellation).
- COMPLETED/FAILED — no-op.

For subprocess executors, cancellation sends SIGTERM/SIGKILL. Implement gracefully.

## Recovery (restart-aware services)

On startup:
1. Load all tasks with status PENDING or RUNNING from the store.
2. For RUNNING, decide: re-execute, mark FAILED, or wait for the original worker.
3. For PENDING, schedule execution.

The choice depends on idempotency:
- Idempotent tasks: re-execute.
- Non-idempotent: mark FAILED + alert; require operator intervention.

Always log recovery decisions.

## Limits enforcement

The service must enforce `max_concurrent_tasks` and `max_tasks_per_session` from `BackgroundTasksConfig`. Options when limits hit:
- Reject spawn (return error to planner).
- Queue (PENDING) and execute when capacity frees up.

The in-process `InProcessTaskService` passes its `background_config` to the session (`configure_background_tasks`, explicitly set fields only). The session rejects a spawn when the active-task limit (`max_tasks_per_session`) or `SessionLimits.max_background_tasks` is hit (`task_limit_exceeded` / `background_task_limit_exceeded`), and queues tasks beyond `max_concurrent_tasks` until a slot frees. Production services usually add a deeper backpressure layer.

## Observability hooks

Emit `PlannerEvent` (or your own equivalent) for every lifecycle transition:
- `task_spawned`, `task_started`, `task_completed`, `task_failed`, `task_cancelled`, `task_merged`.
- `task_group_sealed`, `task_group_applied`.

Track:
- Queue depth (PENDING count) — saturation signal.
- p95 time-to-start (PENDING duration) — capacity signal.
- p95 task duration — workload signal.
- Failure rate by mode and tool.

## Anti-patterns

- **In-process `asyncio` for CPU-bound work** — blocks the event loop. Use subprocess or worker pool.
- **No persistence in multi-worker deployments** — tasks vanish on restart.
- **Persistence without lifecycle hooks** — store sees PENDING but never updates; debugging nightmare.
- **No idempotency for retried tasks** — duplicates side effects on recovery.
- **Subagents that can spawn more tasks** — recursion explosion; visibility policy.
- **No per-session limit** — one bad session DoSes the worker.
