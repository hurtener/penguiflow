# Worker Patterns

## Pattern: planner per job (best isolation)

One planner instance per job, built from a factory. Worker loop:

```python
running: dict[str, SteeringInbox] = {}

async def worker(queue: Queue, shutting_down: asyncio.Event) -> None:
    while not shutting_down.is_set():
        job = await queue.fetch(timeout_s=1.0)
        if job is None:
            continue
        planner = make_planner()
        inbox = SteeringInbox()
        running[job.id] = inbox
        try:
            result = await asyncio.wait_for(
                planner.run(
                    job.query,
                    tool_context={"session_id": job.session_id, "tenant": job.tenant, "trace_id": f"job-{job.id}"},
                    steering=inbox,
                ),
                timeout=job.deadline_s,
            )
            if isinstance(result, PlannerPause):
                await queue.park(job.id, resume_token=result.resume_token, prompt=result.payload)
            elif result.reason == "answer_complete":
                await queue.mark_complete(job.id, result=result.payload)
            else:
                await queue.mark_failed(job.id, reason=result.reason, details=result.metadata.get("error"))
        except TimeoutError:
            await queue.retry_job(job.id, delay_s=30, reason="deadline")
        finally:
            running.pop(job.id, None)
```

Prefer this when:
- Jobs are independent.
- You want a crash to affect one job only.
- Per-run callbacks (metrics, streaming) must not mix.

Planner construction is cheap compared to an LLM call; reuse the LLM client, catalog and `StateStore` across planners.

## Pattern: sessions-backed service (shared runtime)

`SessionManager` owns one `StreamingSession` per conversation; each request becomes a session task run by `PlannerTaskPipeline(planner_factory=make_planner)`:

```python
manager = SessionManager(state_store=store, limits=SessionLimits(max_concurrent_tasks=4))

async def handle(session_id: str, query: str) -> TaskResult:
    session = await manager.get_or_create(session_id)
    return await session.run_task(PlannerTaskPipeline(planner_factory=make_planner), query=query)
```

Prefer this when:
- Clients need live updates, reconnect/replay, or steering (`session.steer(...)`, `session.cancel_task(...)`).
- Planners spawn background tasks.
- You want per-session concurrency limits and task telemetry out of the box.

## Resuming paused jobs

A `PlannerPause` is a checkpoint, not a failure. Store `resume_token` with the job; when the human (or external system) answers, any worker can continue:

```python
planner = make_planner()                 # same state_store as the run that paused
result = await planner.resume(token, user_input=answer, tool_context={"session_id": job.session_id, "tenant": job.tenant})
```

This requires a `StateStore` with `save_planner_state` / `load_planner_state`. See [[penguiflow-hitl-pause-resume]].

## Queue contracts

Whatever queue you use (Redis, SQS, Kafka, DB-backed), the worker integration is easiest when the queue exposes:

```python
class Queue(Protocol):
    async def fetch(self, *, timeout_s: float | None) -> Job | None: ...
    async def mark_complete(self, job_id: str, *, result: Any) -> None: ...
    async def retry_job(self, job_id: str, *, delay_s: float, reason: str) -> None: ...
    async def mark_failed(self, job_id: str, *, reason: str, details: Any | None) -> None: ...
    async def park(self, job_id: str, *, resume_token: str, prompt: Any) -> None: ...   # HITL pauses
```

Required invariants:
- **At-least-once delivery**. Build for idempotent tools.
- **Visibility timeout** larger than your worst-case run duration (`deadline_s` + headroom), so the queue doesn't redeliver mid-run.
- **Dead-letter** for terminally failed jobs (`no_path`, repeated errors).

## Per-job correlation

Put a job-derived id in `tool_context` (e.g. `trace_id=f"job-{job.id}"`). Benefits:
- Logs and LLM spans are searchable by it.
- Tools can use it as an idempotency key.
- `StateStore` records group by it.

Don't reuse ids across retries — generate a fresh one and keep a parent reference if you need linkage.

## Idempotency

At-least-once delivery + retries = side effects can repeat. Strategies:

### Idempotency keys
Derive a key from the job id and the tool call (`f"{trace_id}:{tool}:{hash(args)}"`) and pass it to external services. Most APIs (Stripe, SQS, AWS) support this natively.

### Plan/commit split
- **Plan tool** — pure; produces an action description.
- **Commit tool** — gated by an idempotency check, a guardrail, or HITL approval.

### Atomic write-and-mark
```python
@tool(desc="Commit the order", side_effects="write")
async def commit_order(args: CommitArgs, ctx) -> CommitResult:
    key = f"{ctx.tool_context['trace_id']}:{args.order_id}"
    async with db.transaction():
        if existing := await find_commit(key):
            return existing
        result = await do_write(args)
        await mark_committed(key, result)
        return result
```

## Time budgets

Three layers:
1. **Per-tool**: `asyncio.timeout(...)` inside local tools; `ExternalToolConfig.timeout_s` for ToolNode tools. Tight (seconds).
2. **Per-LLM-call**: `llm_timeout_s` / `llm_max_retries` on the planner.
3. **End-to-end**: planner `deadline_s` (the planner finishes with `budget_exhausted` and a best-effort answer) plus an outer `asyncio.wait_for` as a hard stop. Loose (tens of seconds to minutes).

On end-to-end timeout:
1. Push a steering `CANCEL` (cooperative) or cancel the run task (hard).
2. `retry_job` (if transient) or `mark_failed`.

## Graceful shutdown

On `SIGTERM`:
1. Set `shutting_down`; stop fetching new jobs; health check returns 503.
2. Wait for in-flight runs up to the drain deadline.
3. Push `SteeringEventType.CANCEL` into the inboxes of runs still going; requeue their jobs.
4. `await a2a_service.stop()` / close sessions and LLM clients.

```python
async def graceful_shutdown(tasks: set[asyncio.Task], deadline_s: float = 30) -> None:
    done, pending = await asyncio.wait(tasks, timeout=deadline_s)
    for job_id, inbox in list(running.items()):
        await inbox.push(SteeringEvent(session_id=job_id, task_id=job_id, event_type=SteeringEventType.CANCEL, payload={"reason": "shutdown"}))
    if pending:
        await asyncio.wait(pending, timeout=5)
    for task in pending:
        task.cancel()
```

Paused runs need nothing — their state is already in the `StateStore`.

## Tenant isolation

For multi-tenant workers:
- `tenant` (and `user_id`) in `tool_context` on every run; tools must enforce it.
- Per-tenant rate-limiting at the queue level (one tenant doesn't starve others).
- Tenant-scoped credentials (don't share API tokens across tenants).
- Tenant-scoped `session_id`s and state-store keys (`tenant:<id>:...`).
- Tenant-scoped artifact retrieval (authz on the retrieval endpoint).

A single worker can serve many tenants safely if isolation is enforced at every layer.

## Health checks

Expose `/health` returning:
- 200 while the worker is processing.
- 503 once `shutting_down` is set (lets the load balancer drain).
- 503 if a critical dependency (queue, state store, LLM gateway) is unreachable.

Keep probes cheap; don't call the LLM from a health check.

## Anti-patterns

- **One planner instance shared across concurrent jobs with per-job callbacks** — events and metrics mix.
- **No steering inbox** — you can't cancel a run cooperatively.
- **Synchronous I/O inside a tool** — blocks the event loop for every run on the worker.
- **Per-job DB/HTTP client** — connection churn; use pools.
- **Returning huge results from tools** — push to the artifact store; return a ref.
- **`asyncio.create_task` without tracking** — fire-and-forget that you can't wait for or cancel.
