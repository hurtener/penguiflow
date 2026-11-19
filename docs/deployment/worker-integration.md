# Worker integration

## What it is / when to use it

This page is a runbook for running `ReactPlanner` agents inside a **production worker**:

- a queue-backed job runner (Redis/SQS/Kafka/DB),
- a background task processor inside a service,
- a batch worker that runs many planner jobs.

Use it when you need **throughput + reliability + graceful shutdown**, and predictable behavior under retries, timeouts, cancellation and pauses.

## Non-goals / boundaries

- This doc does not provide a blessed job queue implementation.
- It does not define your job schema; it defines the *contracts* your queue integration must satisfy.
- Interactive multi-turn sessions belong in the session docs (see **[Background tasks](../planner/background-tasks.md)**).

## Contract surface (what the worker must implement)

### 1) Job queue interface (recommended shape)

- `fetch()` → returns a job (or times out)
- `mark_complete(job_id, result=...)`
- `retry_job(job_id, delay_s, reason=...)`
- `mark_failed(job_id, reason=..., details=...)`

Key requirement: **at-least-once delivery** means planner runs and their tools must be retry-safe (idempotency).

### 2) Per-job correlation

Treat each job as one planner run:

- pass `tool_context={"tenant": ..., "session_id": f"job-{job_id}", ...}`,
- log the job id with every planner event your callback records.

### 3) Time budgets and cancellation

You need *two* time bounds:

- **tool timeouts** for each external boundary (ToolNode config, or your own tool code),
- a **job-level deadline** (`asyncio.wait_for(planner.run(...), timeout=...)` and planner budgets).

On job timeout, `wait_for` cancels the run and its in-flight tool calls. To cancel from elsewhere (for example an operator request), pass `steering=SteeringInbox()` to `run` and push a `CANCEL` `SteeringEvent` into it.

### 4) Pauses

A run can return `PlannerPause` (approval, OAuth, missing input). Store the job id → `resume_token` mapping, configure a durable `StateStore` so any worker can call `planner.resume(token, user_input=...)` later, and mark the job as waiting rather than failed.

## Operational defaults (recommended)

- Build the planner once per worker process and reuse it; runs with different `session_id`s execute concurrently.
- Use a shared `StateStore` when more than one worker may resume a paused job.
- Turn on **structured logs** and attach an `event_callback` early in the process.

## Worker pattern

```python
from __future__ import annotations

import asyncio
from typing import Any

from penguiflow.planner import PlannerFinish, PlannerPause, ReactPlanner


async def handle_job(planner: ReactPlanner, queue: Any, job: Any) -> None:
    tool_context = {"tenant": job.tenant, "session_id": f"job-{job.id}"}
    try:
        result = await asyncio.wait_for(planner.run(job.query, tool_context=tool_context), timeout=300.0)
    except TimeoutError:
        await queue.retry_job(job.id, delay_s=30, reason="timeout")
        return
    if isinstance(result, PlannerPause):
        await queue.mark_waiting(job.id, resume_token=result.resume_token, reason=result.reason)
    elif isinstance(result, PlannerFinish) and result.reason == "answer_complete":
        await queue.mark_complete(job.id, result=result.payload)
    else:
        await queue.mark_failed(job.id, reason=result.reason)
```

## Failure modes & recovery

### Duplicate side effects

At-least-once delivery re-runs jobs. Make write tools idempotent (idempotency keys) and mark their `side_effects`.

### Jobs stuck "in flight"

- No job-level timeout: wrap `planner.run` in `asyncio.wait_for`.
- A tool ignores cancellation: give it its own timeout.

### Resume fails on another worker

Pause records are in memory. Configure a shared `StateStore` (see **[State store](../tools/statestore.md)**).

## Observability

- planner events via `event_callback` (finish reason, tool latency, repairs),
- job-level logs with job id, tenant, latency and outcome,
- LLM telemetry for provider latency and cost.

## Security / multi-tenancy notes

- Never put secrets in job payloads or `llm_context`; inject credentials through `tool_context` at run time.
- Keep tenant and session scoping consistent between `run` and `resume`.

## Runnable examples

```bash
uv run python examples/react_pause_resume/main.py
uv run python examples/react_parallel/main.py
```

## Troubleshooting checklist

- Need the deployment model: see **[Deployment](overview.md)**.
- Need pause/resume details: see **[Pause/resume](../planner/pause-resume-hitl.md)**.
