# Worker Integration & Deployment Guide

## Overview

This guide covers deploying PenguiFlow in production worker environments, including job queue integration, graceful shutdown, monitoring, and scaling strategies.

---

## Table of Contents

1. [Worker Architecture Patterns](#worker-architecture-patterns)
2. [Job Queue Integration](#job-queue-integration)
3. [Lifecycle Management](#lifecycle-management)
4. [Error Handling & Recovery](#error-handling--recovery)
5. [Monitoring & Observability](#monitoring--observability)
6. [Scaling Strategies](#scaling-strategies)
7. [Deployment Checklist](#deployment-checklist)

---

## Worker Architecture Patterns

### Pattern 1: Stateless Worker Pool (Recommended)

**Best for:** High-throughput, independent jobs

```python
import asyncio
import signal
from typing import Optional
from penguiflow import create, ModelRegistry
from penguiflow.types import Message, Headers

class StatelessWorkerPool:
    """
    Worker pool where each job gets a fresh flow instance.

    Benefits:
    - No state sharing between jobs
    - Easy to scale horizontally
    - Failures isolated per job
    - Simple deployment model
    """

    def __init__(
        self,
        flow_factory: callable,
        registry: ModelRegistry,
        job_queue,
        concurrency: int = 10,
        job_timeout_s: float = 300.0
    ):
        self._flow_factory = flow_factory
        self._registry = registry
        self._job_queue = job_queue
        self._concurrency = concurrency
        self._job_timeout_s = job_timeout_s
        self._shutdown = asyncio.Event()
        self._active_jobs = {}  # trace_id -> task
        self._stats = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "timeouts": 0
        }

    async def run(self):
        """Start worker pool and run until shutdown."""
        logger.info(f"Starting worker pool with {self._concurrency} workers")

        # Start worker tasks
        workers = [
            asyncio.create_task(self._worker_loop(worker_id=i))
            for i in range(self._concurrency)
        ]

        # Wait for shutdown signal
        await self._shutdown.wait()

        logger.info("Shutdown signal received, stopping workers...")

        # Cancel all workers
        for worker in workers:
            worker.cancel()

        # Wait for graceful completion
        await asyncio.gather(*workers, return_exceptions=True)

        logger.info(f"Worker pool stopped. Stats: {self._stats}")

    async def _worker_loop(self, worker_id: int):
        """Individual worker processing loop."""
        logger.info(f"Worker {worker_id} started")

        while not self._shutdown.is_set():
            try:
                # Fetch job with timeout (allows checking shutdown)
                job = await asyncio.wait_for(
                    self._job_queue.fetch(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue  # No job available, check shutdown and retry
            except Exception as exc:
                logger.error(f"Worker {worker_id} job fetch error: {exc}")
                await asyncio.sleep(1.0)
                continue

            # Process job
            await self._process_job(job, worker_id)

        logger.info(f"Worker {worker_id} stopped")

    async def _process_job(self, job, worker_id: int):
        """Process a single job with full error handling."""
        trace_id = f"job-{job.id}"

        # Track active job (for graceful shutdown)
        task = asyncio.current_task()
        self._active_jobs[trace_id] = task

        try:
            # Create fresh flow instance
            flow = self._flow_factory()
            flow.run(registry=self._registry)

            # Build message
            message = Message(
                payload=job.payload,
                headers=Headers(tenant=job.tenant),
                trace_id=trace_id
            )

            # Add trace-specific metadata
            message.meta.update({
                "job_id": job.id,
                "worker_id": worker_id,
                "start_time": time.time(),
                "retry_count": job.retry_count
            })

            try:
                # Emit and wait for result
                await flow.emit(message)

                result = await asyncio.wait_for(
                    flow.fetch(),
                    timeout=self._job_timeout_s
                )

                # Handle result
                if isinstance(result, FlowError):
                    await self._handle_flow_error(job, result)
                    self._stats["failed"] += 1
                else:
                    await self._handle_success(job, result)
                    self._stats["succeeded"] += 1

            except asyncio.TimeoutError:
                await self._handle_timeout(job, trace_id, flow)
                self._stats["timeouts"] += 1

            except Exception as exc:
                await self._handle_exception(job, exc)
                self._stats["failed"] += 1

            finally:
                # Always stop flow
                await flow.stop()

        finally:
            # Remove from active jobs
            self._active_jobs.pop(trace_id, None)
            self._stats["processed"] += 1

    async def _handle_success(self, job, result):
        """Handle successful job completion."""
        await self._job_queue.mark_complete(
            job.id,
            result=result,
            processing_time_s=time.time() - job.started_at
        )
        logger.info(f"Job {job.id} completed successfully")

    async def _handle_flow_error(self, job, error: FlowError):
        """Handle FlowError from flow execution."""
        should_retry = (
            error.code in ["NODE_TIMEOUT", "NODE_EXCEPTION"] and
            job.retry_count < job.max_retries
        )

        if should_retry:
            await self._job_queue.retry_job(
                job.id,
                error_code=error.code,
                error_message=error.message,
                retry_delay_s=self._calculate_retry_delay(job.retry_count)
            )
            logger.warning(
                f"Job {job.id} failed with {error.code}, "
                f"scheduling retry {job.retry_count + 1}/{job.max_retries}"
            )
        else:
            await self._job_queue.mark_failed(
                job.id,
                error_code=error.code,
                error_message=error.message,
                error_payload=error.to_payload()
            )
            logger.error(f"Job {job.id} failed permanently: {error.message}")

    async def _handle_timeout(self, job, trace_id: str, flow):
        """Handle job timeout."""
        # Cancel the trace
        await flow.cancel(trace_id)

        # Mark as failed
        await self._job_queue.mark_failed(
            job.id,
            error_code="JOB_TIMEOUT",
            error_message=f"Job exceeded {self._job_timeout_s}s timeout"
        )

        logger.error(f"Job {job.id} timed out after {self._job_timeout_s}s")

    async def _handle_exception(self, job, exc: Exception):
        """Handle unexpected exceptions."""
        await self._job_queue.mark_failed(
            job.id,
            error_code="UNEXPECTED_ERROR",
            error_message=str(exc),
            error_traceback=traceback.format_exc()
        )

        logger.exception(f"Unexpected error processing job {job.id}")

    def _calculate_retry_delay(self, retry_count: int) -> float:
        """Calculate exponential backoff delay."""
        return min(2 ** retry_count, 60.0)  # Max 60s

    def shutdown(self):
        """Signal graceful shutdown."""
        self._shutdown.set()

    async def wait_for_active_jobs(self, timeout: float = 30.0):
        """Wait for active jobs to complete."""
        if not self._active_jobs:
            return

        logger.info(f"Waiting for {len(self._active_jobs)} active jobs to complete...")

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._active_jobs.values(), return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for active jobs, forcing shutdown")
```

---

### Pattern 2: Long-Lived Flow (Advanced)

**Best for:** Stateful workflows, shared resources

```python
class LongLivedFlowWorker:
    """
    Worker that maintains a single flow instance across jobs.

    Use when:
    - Flow has expensive initialization (connection pools, model loading)
    - Jobs share state or resources
    - Job rate is high (avoid flow startup overhead)

    Cautions:
    - More complex error handling
    - Must prevent state leaks between jobs
    - Harder to debug
    """

    def __init__(
        self,
        flow_factory: callable,
        registry: ModelRegistry,
        job_queue,
        restart_after_failures: int = 100
    ):
        self._flow_factory = flow_factory
        self._registry = registry
        self._job_queue = job_queue
        self._restart_after_failures = restart_after_failures
        self._flow = None
        self._failure_count = 0

    async def run(self):
        """Run worker with flow restart logic."""
        # Initialize flow once
        await self._initialize_flow()

        while True:
            try:
                job = await self._job_queue.fetch()
                await self._process_job(job)

            except Exception as exc:
                logger.exception(f"Job processing failed: {exc}")
                self._failure_count += 1

                # Restart flow if too many failures
                if self._failure_count >= self._restart_after_failures:
                    logger.warning(
                        f"Restarting flow after {self._failure_count} failures"
                    )
                    await self._restart_flow()
                    self._failure_count = 0

    async def _initialize_flow(self):
        """Initialize flow instance."""
        self._flow = self._flow_factory()
        self._flow.run(registry=self._registry)
        logger.info("Flow initialized")

    async def _restart_flow(self):
        """Restart flow instance."""
        if self._flow:
            await self._flow.stop()
        await self._initialize_flow()

    async def _process_job(self, job):
        """Process job with existing flow."""
        message = Message(
            payload=job.payload,
            headers=Headers(tenant=job.tenant),
            trace_id=f"job-{job.id}"
        )

        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Handle result...
```

---

## Job Queue Integration

### Redis-Based Job Queue

```python
import redis.asyncio as redis
import json

class RedisJobQueue:
    """Redis-backed job queue with retry logic."""

    def __init__(self, redis_url: str, queue_name: str = "penguiflow:jobs"):
        self._redis_url = redis_url
        self._queue_name = queue_name
        self._processing_queue = f"{queue_name}:processing"
        self._failed_queue = f"{queue_name}:failed"
        self._redis = None

    async def initialize(self):
        """Initialize Redis connection."""
        self._redis = await redis.from_url(self._redis_url)

    async def enqueue(self, job_data: dict, priority: int = 0) -> str:
        """Add job to queue."""
        job_id = str(uuid.uuid4())

        job = {
            "id": job_id,
            "payload": job_data,
            "priority": priority,
            "enqueued_at": time.time(),
            "retry_count": 0,
            "max_retries": 3
        }

        # Add to sorted set (priority queue)
        await self._redis.zadd(
            self._queue_name,
            {json.dumps(job): priority}
        )

        return job_id

    async def fetch(self, timeout: float = 30.0) -> Optional[dict]:
        """Fetch next job (blocking with timeout)."""
        # BZPOPMIN with timeout
        result = await self._redis.bzpopmin(self._queue_name, timeout=timeout)

        if not result:
            raise asyncio.TimeoutError("No job available")

        _, job_json, _ = result
        job = json.loads(job_json)

        # Move to processing queue
        job["started_at"] = time.time()
        await self._redis.hset(
            self._processing_queue,
            job["id"],
            json.dumps(job)
        )

        return job

    async def mark_complete(self, job_id: str, result: Any, processing_time_s: float):
        """Mark job as completed."""
        # Remove from processing
        await self._redis.hdel(self._processing_queue, job_id)

        # Store result (with TTL)
        await self._redis.setex(
            f"{self._queue_name}:result:{job_id}",
            3600,  # 1 hour TTL
            json.dumps({
                "status": "completed",
                "result": result,
                "processing_time_s": processing_time_s,
                "completed_at": time.time()
            })
        )

    async def retry_job(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        retry_delay_s: float
    ):
        """Requeue job for retry."""
        # Get job from processing
        job_json = await self._redis.hget(self._processing_queue, job_id)
        if not job_json:
            return

        job = json.loads(job_json)
        job["retry_count"] += 1
        job["last_error"] = {
            "code": error_code,
            "message": error_message,
            "time": time.time()
        }

        # Remove from processing
        await self._redis.hdel(self._processing_queue, job_id)

        # Re-enqueue with delay (use sorted set score for delayed execution)
        retry_at = time.time() + retry_delay_s
        await self._redis.zadd(
            self._queue_name,
            {json.dumps(job): retry_at}
        )

    async def mark_failed(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        **kwargs
    ):
        """Mark job as permanently failed."""
        job_json = await self._redis.hget(self._processing_queue, job_id)
        if not job_json:
            return

        job = json.loads(job_json)
        job["status"] = "failed"
        job["error"] = {
            "code": error_code,
            "message": error_message,
            **kwargs
        }
        job["failed_at"] = time.time()

        # Move to failed queue
        await self._redis.hdel(self._processing_queue, job_id)
        await self._redis.hset(
            self._failed_queue,
            job_id,
            json.dumps(job)
        )

    async def close(self):
        """Close Redis connection."""
        await self._redis.close()
```

---

## Lifecycle Management

### Graceful Shutdown with Signal Handling

```python
import signal

class WorkerManager:
    """Manage worker lifecycle with graceful shutdown."""

    def __init__(self, worker_pool: StatelessWorkerPool):
        self._worker_pool = worker_pool
        self._shutdown_timeout = 30.0

    async def run(self):
        """Run worker with signal handlers."""
        # Register signal handlers
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._shutdown(s))
            )

        logger.info("Worker manager started, listening for signals...")

        # Run worker pool
        await self._worker_pool.run()

    async def _shutdown(self, sig):
        """Handle shutdown signal."""
        logger.info(f"Received signal {sig.name}, initiating graceful shutdown...")

        # Signal workers to stop accepting new jobs
        self._worker_pool.shutdown()

        # Wait for active jobs to complete (with timeout)
        await self._worker_pool.wait_for_active_jobs(
            timeout=self._shutdown_timeout
        )

        logger.info("Graceful shutdown complete")

# Usage
async def main():
    # Setup
    job_queue = RedisJobQueue("redis://localhost")
    await job_queue.initialize()

    worker_pool = StatelessWorkerPool(
        flow_factory=create_topic_generation_flow,
        registry=model_registry,
        job_queue=job_queue,
        concurrency=10
    )

    manager = WorkerManager(worker_pool)

    # Run (blocks until SIGTERM/SIGINT)
    await manager.run()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Error Handling & Recovery

### Dead Letter Queue Pattern

```python
class JobQueueWithDLQ(RedisJobQueue):
    """Job queue with dead letter queue for debugging."""

    def __init__(self, redis_url: str, queue_name: str = "penguiflow:jobs"):
        super().__init__(redis_url, queue_name)
        self._dlq_name = f"{queue_name}:dlq"

    async def mark_failed(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        **kwargs
    ):
        """Mark job as failed and send to DLQ."""
        # Call parent implementation
        await super().mark_failed(job_id, error_code, error_message, **kwargs)

        # Add to DLQ for analysis
        job_json = await self._redis.hget(self._failed_queue, job_id)
        if job_json:
            await self._redis.lpush(self._dlq_name, job_json)

            # Trim DLQ to max size
            await self._redis.ltrim(self._dlq_name, 0, 9999)

    async def get_dlq_sample(self, count: int = 10) -> list[dict]:
        """Get recent failed jobs for debugging."""
        items = await self._redis.lrange(self._dlq_name, 0, count - 1)
        return [json.loads(item) for item in items]

    async def requeue_from_dlq(self, job_id: str) -> bool:
        """Manually requeue a failed job."""
        # Find job in failed queue
        job_json = await self._redis.hget(self._failed_queue, job_id)
        if not job_json:
            return False

        job = json.loads(job_json)

        # Reset job state
        job["retry_count"] = 0
        job["status"] = "pending"
        job.pop("error", None)
        job.pop("failed_at", None)

        # Re-enqueue
        await self._redis.zadd(
            self._queue_name,
            {json.dumps(job): 0}
        )

        # Remove from failed queue
        await self._redis.hdel(self._failed_queue, job_id)

        logger.info(f"Requeued job {job_id} from DLQ")
        return True
```

---

## Monitoring & Observability

### Health Check Endpoint

```python
from fastapi import FastAPI
from pydantic import BaseModel

class HealthStatus(BaseModel):
    status: str
    active_jobs: int
    queue_depth: int
    stats: dict

app = FastAPI()

@app.get("/health", response_model=HealthStatus)
async def health_check():
    """Health check endpoint for load balancer."""
    return HealthStatus(
        status="healthy" if worker_pool._stats["failed"] < 100 else "degraded",
        active_jobs=len(worker_pool._active_jobs),
        queue_depth=await job_queue.get_queue_depth(),
        stats=worker_pool._stats
    )

@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    stats = worker_pool._stats

    metrics = f"""
# HELP penguiflow_jobs_processed_total Total jobs processed
# TYPE penguiflow_jobs_processed_total counter
penguiflow_jobs_processed_total {stats['processed']}

# HELP penguiflow_jobs_succeeded_total Total jobs succeeded
# TYPE penguiflow_jobs_succeeded_total counter
penguiflow_jobs_succeeded_total {stats['succeeded']}

# HELP penguiflow_jobs_failed_total Total jobs failed
# TYPE penguiflow_jobs_failed_total counter
penguiflow_jobs_failed_total {stats['failed']}

# HELP penguiflow_jobs_active Active jobs
# TYPE penguiflow_jobs_active gauge
penguiflow_jobs_active {len(worker_pool._active_jobs)}
"""

    return Response(content=metrics, media_type="text/plain")
```

### Structured Logging Middleware

```python
import logging
import json

def create_logging_middleware():
    """Create middleware for structured logging."""

    async def log_middleware(event: FlowEvent):
        """Log FlowEvents as structured JSON."""
        if event.event_type in ["node_error", "node_failed", "node_timeout"]:
            # Error events - always log
            logger.error(
                "Node failure",
                extra={
                    "event_type": event.event_type,
                    "trace_id": event.trace_id,
                    "node_name": event.node_name,
                    "attempt": event.attempt,
                    "latency_ms": event.latency_ms,
                    **event.extra
                }
            )
        elif event.event_type == "node_success":
            # Success - log with lower level
            logger.info(
                "Node completed",
                extra={
                    "trace_id": event.trace_id,
                    "node_name": event.node_name,
                    "latency_ms": event.latency_ms
                }
            )

    return log_middleware
```

---

## Scaling Strategies

### Horizontal Scaling

```yaml
# docker-compose.yml
version: "3.8"

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  worker:
    image: myapp/penguiflow-worker:latest
    environment:
      REDIS_URL: redis://redis:6379
      WORKER_CONCURRENCY: 10
      LOG_LEVEL: INFO
    deploy:
      replicas: 5  # Scale to 5 worker instances
      restart_policy:
        condition: on-failure
        max_attempts: 3
    depends_on:
      - redis
```

### Kubernetes Deployment

```yaml
# worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: penguiflow-worker
spec:
  replicas: 10
  selector:
    matchLabels:
      app: penguiflow-worker
  template:
    metadata:
      labels:
        app: penguiflow-worker
    spec:
      containers:
      - name: worker
        image: myapp/penguiflow-worker:latest
        env:
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: redis-credentials
              key: url
        - name: WORKER_CONCURRENCY
          value: "10"
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: penguiflow-worker-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: penguiflow-worker
  minReplicas: 5
  maxReplicas: 50
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Pods
    pods:
      metric:
        name: queue_depth
      target:
        type: AverageValue
        averageValue: "100"
```

---

## Deployment Checklist

### Pre-Deployment

- [ ] **Flow factory tested**: Unit tests with FlowTestKit
- [ ] **Dependencies injected correctly**: No infrastructure in `message.meta`
- [ ] **Connection pools configured**: Database, Redis, external APIs
- [ ] **Retries configured**: Appropriate `max_retries` and `backoff` settings
- [ ] **Timeouts set**: Both node-level (`timeout_s`) and job-level
- [ ] **Error handling**: `emit_errors_to_rookery=True` enabled
- [ ] **Logging middleware**: Structured logging configured
- [ ] **Health checks**: `/health` and `/metrics` endpoints working

### Production Deployment

- [ ] **Environment variables**: All secrets in environment, not code
- [ ] **Resource limits**: Memory and CPU limits set in container
- [ ] **Graceful shutdown**: Signal handlers registered
- [ ] **Monitoring**: Prometheus/Datadog metrics collection
- [ ] **Alerting**: Alerts for failed jobs, queue depth, worker health
- [ ] **Dead letter queue**: DLQ configured for failed job analysis
- [ ] **Log aggregation**: Logs shipped to centralized system (ELK, Splunk)
- [ ] **Horizontal scaling**: Autoscaling rules configured

### Post-Deployment

- [ ] **Monitor error rates**: Track FlowError codes and frequencies
- [ ] **Check queue depth**: Ensure workers keeping up with load
- [ ] **Review DLQ**: Investigate patterns in failed jobs
- [ ] **Optimize policies**: Adjust timeouts/retries based on metrics
- [ ] **Capacity planning**: Scale workers based on traffic patterns

---

## Summary

**Key principles for production worker deployment:**

1. **Stateless workers** (fresh flow per job) for most use cases
2. **Proper dependency injection** via closures, not `message.meta`
3. **Connection pooling** for databases and external services
4. **Graceful shutdown** with signal handling and job completion wait
5. **Comprehensive error handling** with retries, DLQ, and structured logging
6. **Health checks** for load balancer integration
7. **Horizontal scaling** with container orchestration
8. **Observability** via Prometheus metrics and structured logs

For more information, see:
- `docs/migration/penguiflow-adoption.md` - Migration patterns
- `docs/patterns/topic-generation-flow.md` - Complete flow example
- Manual Section 10 - Error handling details
- Manual Section 11 - Observability and middleware
