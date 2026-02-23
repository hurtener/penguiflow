# Worker integration

## What it is / when to use it

This page is a runbook for integrating PenguiFlow into a **production worker**:

- a queue-backed job runner (Redis/SQS/Kafka/DB),
- a background task processor inside a service,
- a batch worker that executes flows at high throughput.

Use it when you need **throughput + reliability + graceful shutdown**, and you want predictable behavior under retries, timeouts, and cancellation.

## Non-goals / boundaries

- This doc does not provide a blessed job queue implementation.
- It does not define your job schema; it defines the *contracts* your queue integration must satisfy.
- It does not replace `ReactPlanner` session orchestration docs (pause/resume belongs in planner/session pages).

## Contract surface (what the worker must implement)

### 1) Job queue interface (recommended shape)

Workers are easiest to reason about when the queue interface is explicit:

- `fetch()` → returns a job (or times out)
- `mark_complete(job_id, result=...)`
- `retry_job(job_id, delay_s, reason=...)`
- `mark_failed(job_id, reason=..., details=...)`

Key requirement: **at-least-once delivery** means your flow execution must be retry-safe (idempotency).

### 2) Per-job correlation (trace id)

Treat each job as one trace:

- `trace_id = f"job-{job_id}"` (or UUID-based)
- envelope messages should set `Headers.tenant` (if multi-tenant)
- prefer `emit(..., trace_id=trace_id)` + `fetch(trace_id=trace_id)` for deterministic correlation

See **[Messages & envelopes](../core/messages-and-envelopes.md)**.

### 3) Time budgets and cancellation

You need *two* time bounds:

- **node timeouts** (`NodePolicy.timeout_s`) for each external boundary,
- a **job-level deadline** (message `deadline_s` and/or a `wait_for(fetch, ...)` bound).

On job timeout:

- call `await flow.cancel(trace_id)` (best-effort),
- then mark the job failed/retry based on your policy.

See **[Cancellation](../core/cancellation.md)**.

### 4) Flow lifecycle

Every worker must manage:

- `flow.run(...)` before processing jobs,
- `await flow.stop()` during shutdown (and after each job if you use stateless-per-job).

## Operational defaults (recommended)

- **Stateless per job** is the default: new flow instance per job (best isolation, easiest debugging).
- Use **envelopes** (`Message`) in production workers:
  - deadlines, cancellation, and deterministic trace correlation.
- Keep **bounded queues** (`queue_maxsize` > 0) and treat queue depth as a scaling signal.
- Turn on **structured logs** and capture `FlowEvent` early in the process.

## Worker patterns

### Pattern 1: Stateless worker pool (recommended)

Best for:

- high throughput,
- independent jobs,
- strict isolation between tenants/jobs.

Minimal skeleton (queue-specific pieces are placeholders):

```python
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from penguiflow import Headers, Message, configure_logging


logger = logging.getLogger("worker")


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    tenant: str
    payload: dict[str, Any]
    attempts: int = 0


class JobQueue(Protocol):
    async def fetch(self) -> Job: ...
    async def mark_complete(self, job_id: str, *, result: Any, latency_s: float) -> None: ...
    async def retry_job(self, job_id: str, *, delay_s: float, reason: str) -> None: ...
    async def mark_failed(self, job_id: str, *, reason: str) -> None: ...


async def process_job(job: Job, queue: JobQueue) -> None:
    trace_id = f"job-{job.id}"
    started = time.perf_counter()

    flow = build_flow()  # your factory: returns PenguiFlow
    flow.run()
    try:
        message = Message(payload=job.payload, headers=Headers(tenant=job.tenant), trace_id=trace_id)
        await flow.emit(message, trace_id=trace_id)
        result = await asyncio.wait_for(flow.fetch(trace_id=trace_id), timeout=300.0)
        await queue.mark_complete(job.id, result=result, latency_s=time.perf_counter() - started)
    except asyncio.TimeoutError:
        await flow.cancel(trace_id)
        await queue.retry_job(job.id, delay_s=5.0, reason="job_timeout")
    except Exception as exc:  # noqa: BLE001
        await queue.retry_job(job.id, delay_s=5.0, reason=f"exception:{type(exc).__name__}")
        logger.exception("job_failed", extra={"job_id": job.id, "trace_id": trace_id})
    finally:
        await flow.stop()


async def worker_loop(worker_id: int, queue: JobQueue) -> None:
    while True:
        job = await queue.fetch()
        logger.info("job_fetched", extra={"worker_id": worker_id, "job_id": job.id})
        await process_job(job, queue)


async def main() -> None:
    configure_logging(structured=True)
    queue = build_queue()  # your queue adapter

    workers = [asyncio.create_task(worker_loop(i, queue)) for i in range(8)]
    await asyncio.gather(*workers)


if __name__ == "__main__":
    asyncio.run(main())
```

### Pattern 2: Long-lived flow worker (advanced)

Best for:

- expensive flow initialization (connection pools, model loading),
- very high job rate where `create(...)` overhead matters.

Risks:

- state leaks between jobs if you cache in globals or reuse mutable context,
- harder failure recovery (one bad state can poison subsequent jobs).

Guidance:

- still use per-job `trace_id` and envelopes,
- restart the flow on a rolling basis (e.g., after N jobs or M failures),
- treat any “unexpected exception” as a reason to rebuild the flow.

## Failure modes & recovery

### Duplicate side effects

Cause: job retries re-executed side-effecting nodes.

Fix:

- implement idempotency on side effects (idempotency key derived from `trace_id` and the step),
- or split compute/commit and only commit once.

### Jobs stuck “in flight”

Causes:

- worker died mid-job and the queue never re-delivered,
- `fetch()` is unbounded and nothing routes to Rookery,
- downstream dependency hang and missing timeouts.

Fix:

- enforce job lease timeouts in the queue (visibility timeout),
- ensure egress nodes exist and return/emit results,
- enforce `NodePolicy.timeout_s` on all I/O boundaries.

### Cancellation doesn’t stop everything

Cause: cancellation is best-effort and requires cooperative await points.

Fix:

- avoid blocking calls inside nodes; use async clients,
- propagate cancellation into downstream calls where possible,
- keep per-job deadlines as the ultimate bound.

## Observability

Minimum production worker observability:

- structured logs with `job_id`, `trace_id`, `tenant`, `worker_id`,
- runtime `FlowEvent` capture via middleware (`log_flow_events`),
- derived metrics:
  - throughput (jobs/s),
  - success/error/timeout counters,
  - node latency histograms and queue-depth gauges.

See:

- **[Logging](../observability/logging.md)**
- **[Telemetry patterns](../observability/telemetry-patterns.md)**
- **[Metrics & alerts](../observability/metrics-and-alerts.md)**

## Security / multi-tenancy notes

- Always set `Headers.tenant` for multi-tenant workers, and never allow cross-tenant fetch/cancel.
- Treat tool credentials as secrets; inject via env/secret manager (never in config YAML committed to git).
- Avoid logging raw payloads by default; log references/ids and use artifacts/resources for large data.

## Runnable examples

The repo examples are single-process, but the runtime shape is the same:

```bash
uv run python examples/quickstart/flow.py
uv run python examples/roadmap_status_updates/flow.py
```

## Troubleshooting checklist

- **Queue depth grows**: you are saturated; reduce retries/timeouts first, then scale worker replicas.
- **High timeout rate**: downstream dependency latency or missing `timeout_s`; tighten timeouts and add circuit-breaker behavior at the tool boundary.
- **Mixed results across jobs**: enforce `trace_id` uniqueness and use `fetch(trace_id=...)`.
