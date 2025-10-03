# PenguiFlow Migration Guide

## Overview

This guide helps teams migrate from legacy orchestration systems to PenguiFlow. It focuses on **correct patterns** and architectural decisions that leverage PenguiFlow's design rather than fighting it.

---

## Table of Contents

1. [Key Architectural Differences](#key-architectural-differences)
2. [Dependency Management (Correct Patterns)](#dependency-management-correct-patterns)
3. [Database Integration](#database-integration)
4. [External Service Integration](#external-service-integration)
5. [Worker Architecture](#worker-architecture)
6. [Gradual Migration Strategy](#gradual-migration-strategy)
7. [Common Anti-Patterns to Avoid](#common-anti-patterns-to-avoid)

---

## Key Architectural Differences

### Legacy System vs PenguiFlow

| Aspect | Legacy Orchestrator | PenguiFlow |
|--------|---------------------|------------|
| **State** | Global singletons | Node closures or registry injection |
| **Flow definition** | Implicit/config-driven | Explicit adjacency graph |
| **Type safety** | Runtime duck typing | Pydantic validation at boundaries |
| **Retries** | Custom per-operation | Declarative `NodePolicy` |
| **Observability** | Ad-hoc logging | Structured `FlowEvent` middleware |
| **Cancellation** | No support | Per-trace `flow.cancel(trace_id)` |

---

## Dependency Management (Correct Patterns)

### ❌ Anti-Pattern: Storing Infrastructure in `message.meta`

**Don't do this:**
```python
# ❌ BAD - couples infrastructure to messages
message.meta["db_session"] = db_session
message.meta["resource_manager"] = resource_manager
message.meta["embedding_service"] = embedding_service
message.meta["faiss_lock"] = asyncio.Lock()
```

**Problems:**
- Database connections have lifecycles independent of messages
- Retries may use stale/closed connections
- Locks in `meta` create concurrency bugs (not properly scoped)
- Makes testing difficult (mocking requires message manipulation)

---

### ✅ Correct Pattern 1: Closure-Based Dependency Injection

**Recommended for simple cases:**

```python
from penguiflow import Node, create

class Dependencies:
    """Application-level dependencies with proper lifecycle."""
    def __init__(self, db_pool, embedding_service, config):
        self.db_pool = db_pool
        self.embedding_service = embedding_service
        self.config = config

def create_flow(deps: Dependencies):
    """Factory that closes over dependencies."""

    async def validate_input(data: InputModel, ctx) -> ValidatedData:
        # Access deps from closure - no meta needed
        max_length = deps.config.max_input_length
        if len(data.text) > max_length:
            raise ValueError(f"Input too long: {len(data.text)} > {max_length}")
        return ValidatedData(text=data.text, validated=True)

    async def enrich_with_llm(data: ValidatedData, ctx) -> EnrichedData:
        # Get fresh connection per invocation
        async with deps.db_pool.acquire() as conn:
            context = await conn.fetchone(
                "SELECT context FROM users WHERE id = $1",
                data.user_id
            )

        # Use embedding service
        embedding = await deps.embedding_service.embed(data.text)

        return EnrichedData(
            text=data.text,
            embedding=embedding,
            context=context
        )

    validate = Node(validate_input, name="validate")
    enrich = Node(enrich_with_llm, name="enrich")

    return create(validate.to(enrich))

# Usage
deps = Dependencies(
    db_pool=create_connection_pool(),
    embedding_service=OpenAIEmbeddings(),
    config=load_config()
)

flow = create_flow(deps)
```

**Benefits:**
- Dependencies properly scoped (not coupled to messages)
- Easy to test (pass mock dependencies to factory)
- Connection pooling works correctly
- No stale resource issues on retries

---

### ✅ Correct Pattern 2: Registry-Based Dependency Injection

**Recommended for complex cases with many services:**

```python
from typing import Protocol
from penguiflow import Node, create, ModelRegistry

class ServiceRegistry(Protocol):
    """Define service interface."""
    def get_db_session(self) -> AsyncContextManager:
        """Return context manager for DB session."""
        ...

    def get_llm_client(self) -> LLMClient:
        """Return LLM client (stateless, pooled)."""
        ...

    def get_config(self, key: str) -> Any:
        """Get configuration value."""
        ...

# Implementation
class ProductionServiceRegistry:
    def __init__(self, db_pool, llm_client, config):
        self._db_pool = db_pool
        self._llm_client = llm_client
        self._config = config

    def get_db_session(self):
        return self._db_pool.acquire()

    def get_llm_client(self):
        return self._llm_client

    def get_config(self, key: str):
        return self._config[key]

def create_flow_with_registry(service_registry: ServiceRegistry):
    async def process_with_db(data: InputData, ctx) -> ProcessedData:
        # Get fresh session per invocation
        async with service_registry.get_db_session() as session:
            result = await session.execute(
                "SELECT * FROM items WHERE id = :id",
                {"id": data.item_id}
            )
            item = result.fetchone()

        llm = service_registry.get_llm_client()
        enhanced = await llm.enhance(item.description)

        return ProcessedData(item_id=data.item_id, enhanced=enhanced)

    node = Node(process_with_db, name="process")
    return create(node.to())

# Usage
services = ProductionServiceRegistry(
    db_pool=create_pool(),
    llm_client=OpenAIClient(),
    config=load_config()
)

flow = create_flow_with_registry(services)
```

**Benefits:**
- Clean separation of concerns
- Easy to swap implementations (test vs production)
- Follows dependency inversion principle
- No message coupling

---

### ✅ Correct Pattern 3: Limited Use of `message.meta`

**Only for trace-specific metadata:**

```python
# ✅ GOOD - trace-specific data only
message.meta["request_id"] = "req-abc-123"
message.meta["user_id"] = "user-456"
message.meta["start_time"] = time.time()
message.meta["experiment_variant"] = "v2"

# Later in nodes
async def track_cost(data: Result, ctx) -> Result:
    user_id = ctx.message.meta["user_id"]
    elapsed = time.time() - ctx.message.meta["start_time"]

    await billing_service.record_usage(
        user_id=user_id,
        duration_s=elapsed,
        tokens=data.token_count
    )

    return data
```

**Rule of thumb:** If it has a lifecycle independent of the message, it doesn't belong in `meta`.

---

## Database Integration

### ❌ Anti-Pattern: Session in Meta

```python
# ❌ BAD
message.meta["db_session"] = db_session

async def node_a(data, ctx):
    session = ctx.message.meta["db_session"]
    session.add(obj)  # What if node_a retries? Session might be closed!
```

---

### ✅ Correct Pattern: Connection Pool with Context Managers

```python
from contextlib import asynccontextmanager

class DatabasePool:
    """Proper connection pool abstraction."""
    def __init__(self, connection_string: str):
        self._pool = None

    async def initialize(self):
        self._pool = await asyncpg.create_pool(self.connection_string)

    @asynccontextmanager
    async def acquire(self):
        """Get connection for single operation."""
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        """Get transactional connection."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

def create_flow_with_db(db_pool: DatabasePool):
    async def stage_changes(data: InputData, ctx) -> StagedData:
        """Stage changes without committing."""
        async with db_pool.transaction() as tx:
            # All operations in this block are transactional
            await tx.execute(
                "INSERT INTO topics (name, tenant) VALUES ($1, $2)",
                data.topic_name, data.tenant
            )

            topic_id = await tx.fetchval("SELECT lastval()")

            for dimension in data.dimensions:
                await tx.execute(
                    "INSERT INTO dimensions (topic_id, name) VALUES ($1, $2)",
                    topic_id, dimension.name
                )

            # Transaction commits when context exits
            return StagedData(topic_id=topic_id, dimensions=data.dimensions)

    async def validate_consistency(data: StagedData, ctx) -> ValidatedData:
        """Read-only validation query."""
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM dimensions WHERE topic_id = $1",
                data.topic_id
            )

            if count != len(data.dimensions):
                raise ValueError(f"Inconsistent dimension count: {count} != {len(data.dimensions)}")

            return ValidatedData(topic_id=data.topic_id, valid=True)

    stage = Node(stage_changes, name="stage")
    validate = Node(validate_consistency, name="validate")

    return create(stage.to(validate))

# Usage
db_pool = DatabasePool("postgresql://localhost/mydb")
await db_pool.initialize()

flow = create_flow_with_db(db_pool)
flow.run(registry=registry)

try:
    await flow.emit(Message(payload=input_data))
    result = await flow.fetch()
finally:
    await flow.stop()
```

**Benefits:**
- Transactions properly scoped to operations
- Retries get fresh connections
- No stale session issues
- Standard asyncpg patterns

---

### Handling Rollbacks on Error

```python
def create_flow_with_compensation(db_pool: DatabasePool):
    async def create_topic(data: InputData, ctx) -> CreatedTopic:
        async with db_pool.transaction() as tx:
            topic_id = await tx.fetchval(
                "INSERT INTO topics (name) VALUES ($1) RETURNING id",
                data.name
            )
            return CreatedTopic(topic_id=topic_id, name=data.name)

    async def create_dimensions(data: CreatedTopic, ctx) -> FinalResult:
        async with db_pool.transaction() as tx:
            for dim in data.dimensions:
                await tx.execute(
                    "INSERT INTO dimensions (topic_id, name) VALUES ($1, $2)",
                    data.topic_id, dim.name
                )

            # If this raises, transaction auto-rolls back
            if not await validate_dimensions(tx, data.topic_id):
                raise ValueError("Dimension validation failed")

            return FinalResult(topic_id=data.topic_id, status="success")

    create = Node(create_topic, name="create")
    enhance = Node(create_dimensions, name="enhance",
                   policy=NodePolicy(max_retries=3))

    flow = create(
        create.to(enhance),
        emit_errors_to_rookery=True
    )

    return flow

# Caller handles FlowError
flow = create_flow_with_compensation(db_pool)
flow.run(registry=registry)

await flow.emit(Message(payload=input_data))
result = await flow.fetch()

if isinstance(result, FlowError):
    # Transaction already rolled back (context manager exited)
    logger.error(f"Flow failed: {result.to_payload()}")

    # Optionally clean up external state
    await cleanup_external_resources(result.trace_id)
```

---

## External Service Integration

### ✅ Correct Pattern: Service Abstractions

```python
from abc import ABC, abstractmethod

class EmbeddingService(ABC):
    """Abstract interface for embedding services."""
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...

class OpenAIEmbeddings(EmbeddingService):
    """Production implementation."""
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(
            input=text,
            model=self._model
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            input=texts,
            model=self._model
        )
        return [item.embedding for item in response.data]

class MockEmbeddings(EmbeddingService):
    """Test implementation."""
    async def embed(self, text: str) -> list[float]:
        return [0.1] * 1536

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]

def create_embedding_flow(embedding_service: EmbeddingService):
    async def process_text(data: TextData, ctx) -> EmbeddedData:
        # Service injected via closure
        embedding = await embedding_service.embed(data.text)
        return EmbeddedData(text=data.text, embedding=embedding)

    node = Node(process_text, name="embed")
    return create(node.to())

# Production
flow = create_embedding_flow(OpenAIEmbeddings(api_key=settings.OPENAI_KEY))

# Testing
test_flow = create_embedding_flow(MockEmbeddings())
```

---

## Worker Architecture

### ✅ Correct Pattern: Worker Pool with Proper Lifecycle

```python
import asyncio
from typing import Optional

class PenguiFlowWorker:
    """Worker that processes jobs using PenguiFlow."""

    def __init__(
        self,
        flow_factory: callable,
        registry: ModelRegistry,
        job_queue: JobQueue,
        concurrency: int = 10
    ):
        self._flow_factory = flow_factory
        self._registry = registry
        self._job_queue = job_queue
        self._concurrency = concurrency
        self._shutdown_event = asyncio.Event()

    async def run(self):
        """Run worker pool until shutdown."""
        tasks = [
            asyncio.create_task(self._worker_loop(worker_id=i))
            for i in range(self._concurrency)
        ]

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Graceful shutdown
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker_loop(self, worker_id: int):
        """Single worker processing loop."""
        logger.info(f"Worker {worker_id} started")

        while not self._shutdown_event.is_set():
            try:
                # Fetch job with timeout
                job = await asyncio.wait_for(
                    self._job_queue.fetch(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue  # Check shutdown and retry

            # Process job in isolated flow
            await self._process_job(job, worker_id)

    async def _process_job(self, job: Job, worker_id: int):
        """Process single job with proper error handling."""
        # Create fresh flow instance per job
        flow = self._flow_factory()
        flow.run(registry=self._registry)

        # Build message with job-specific trace
        message = Message(
            payload=job.payload,
            headers=Headers(tenant=job.tenant),
            trace_id=f"job-{job.id}"
        )

        # Add trace-specific metadata (NOT infrastructure)
        message.meta["job_id"] = job.id
        message.meta["worker_id"] = worker_id
        message.meta["start_time"] = time.time()

        try:
            await flow.emit(message)
            result = await asyncio.wait_for(
                flow.fetch(),
                timeout=job.timeout_s
            )

            if isinstance(result, FlowError):
                await self._handle_error(job, result)
            else:
                await self._handle_success(job, result)

        except asyncio.TimeoutError:
            await self._handle_timeout(job)
            await flow.cancel(message.trace_id)

        except Exception as exc:
            await self._handle_exception(job, exc)

        finally:
            await flow.stop()

    async def _handle_success(self, job: Job, result: Any):
        await self._job_queue.mark_complete(job.id, result=result)
        logger.info(f"Job {job.id} completed successfully")

    async def _handle_error(self, job: Job, error: FlowError):
        await self._job_queue.mark_failed(
            job.id,
            error_code=error.code,
            error_message=error.message
        )
        logger.error(f"Job {job.id} failed: {error.to_payload()}")

    async def _handle_timeout(self, job: Job):
        await self._job_queue.mark_failed(
            job.id,
            error_code="JOB_TIMEOUT",
            error_message=f"Job exceeded {job.timeout_s}s timeout"
        )

    async def _handle_exception(self, job: Job, exc: Exception):
        await self._job_queue.mark_failed(
            job.id,
            error_code="UNEXPECTED_ERROR",
            error_message=str(exc)
        )
        logger.exception(f"Unexpected error processing job {job.id}")

    def shutdown(self):
        """Signal graceful shutdown."""
        self._shutdown_event.set()

# Usage
def create_topic_generation_flow():
    # Your flow factory with closures
    ...

worker = PenguiFlowWorker(
    flow_factory=create_topic_generation_flow,
    registry=model_registry,
    job_queue=redis_job_queue,
    concurrency=10
)

# Run in background
asyncio.create_task(worker.run())

# Later: graceful shutdown
worker.shutdown()
```

---

## Gradual Migration Strategy

### Phase 1: Side-by-Side Execution (Feature Flag)

```python
from enum import Enum

class PipelineImpl(str, Enum):
    LEGACY = "legacy"
    PENGUIFLOW = "penguiflow"
    BOTH = "both"  # Run both, compare results

class PipelineRunner:
    def __init__(
        self,
        impl: PipelineImpl,
        legacy_orchestrator,
        penguiflow_factory,
        registry: ModelRegistry
    ):
        self._impl = impl
        self._legacy = legacy_orchestrator
        self._flow_factory = penguiflow_factory
        self._registry = registry

    async def execute(self, request: Request) -> Response:
        if self._impl == PipelineImpl.LEGACY:
            return await self._legacy.execute(request)

        elif self._impl == PipelineImpl.PENGUIFLOW:
            return await self._execute_penguiflow(request)

        elif self._impl == PipelineImpl.BOTH:
            return await self._execute_both(request)

    async def _execute_penguiflow(self, request: Request) -> Response:
        flow = self._flow_factory()
        flow.run(registry=self._registry)

        try:
            message = Message(
                payload=request.to_dict(),
                headers=Headers(tenant=request.tenant),
                trace_id=f"req-{request.id}"
            )

            await flow.emit(message)
            result = await flow.fetch()

            if isinstance(result, FlowError):
                raise RuntimeError(f"Flow error: {result.message}")

            return Response.from_payload(result)

        finally:
            await flow.stop()

    async def _execute_both(self, request: Request) -> Response:
        """Run both implementations and compare."""
        legacy_task = asyncio.create_task(self._legacy.execute(request))
        penguiflow_task = asyncio.create_task(self._execute_penguiflow(request))

        legacy_result, penguiflow_result = await asyncio.gather(
            legacy_task,
            penguiflow_task,
            return_exceptions=True
        )

        # Log comparison
        await self._compare_results(request, legacy_result, penguiflow_result)

        # Return legacy result (safer during migration)
        if isinstance(legacy_result, Exception):
            raise legacy_result

        return legacy_result

# Usage
runner = PipelineRunner(
    impl=PipelineImpl(settings.PIPELINE_IMPL),
    legacy_orchestrator=LegacyOrchestrator(),
    penguiflow_factory=create_topic_flow,
    registry=model_registry
)

@app.post("/generate")
async def generate_endpoint(request: Request):
    return await runner.execute(request)
```

---

## Common Anti-Patterns to Avoid

### ❌ 1. Storing Mutable Infrastructure in `meta`

**Don't:**
```python
message.meta["db_session"] = db_session  # ❌ Lifecycle mismatch
message.meta["faiss_lock"] = asyncio.Lock()  # ❌ Concurrency bug
message.meta["resource_manager"] = resource_mgr  # ❌ Tight coupling
```

**Do:**
```python
# ✅ Use closures or service registry
def create_flow(db_pool: DatabasePool):
    async def node(data, ctx):
        async with db_pool.acquire() as conn:
            # Use fresh connection
            ...
```

---

### ❌ 2. Global Singletons

**Don't:**
```python
# ❌ Global state
DB_SESSION = None

async def node(data, ctx):
    global DB_SESSION
    await DB_SESSION.execute(...)  # Race conditions, testing nightmares
```

**Do:**
```python
# ✅ Dependency injection
def create_flow(db_pool):
    async def node(data, ctx):
        async with db_pool.acquire() as conn:
            await conn.execute(...)
```

---

### ❌ 3. Mixing Business Logic with Infrastructure

**Don't:**
```python
async def node(data, ctx):
    # ❌ Hardcoded infrastructure
    db = await asyncpg.connect("postgresql://...")
    embedding = OpenAI(api_key="sk-...")

    result = await process(data, db, embedding)
    return result
```

**Do:**
```python
def create_flow(services: ServiceRegistry):
    async def node(data, ctx):
        # ✅ Injected services
        async with services.get_db_session() as db:
            embedding_svc = services.get_embedding_service()
            result = await process(data, db, embedding_svc)
        return result
```

---

### ❌ 4. Ignoring Retries in Design

**Don't:**
```python
async def node(data, ctx):
    # ❌ Non-idempotent - retries will duplicate records
    await db.execute("INSERT INTO logs (msg) VALUES ($1)", data.msg)
    return data
```

**Do:**
```python
async def node(data, ctx):
    # ✅ Idempotent - safe to retry
    await db.execute(
        """
        INSERT INTO logs (id, msg) VALUES ($1, $2)
        ON CONFLICT (id) DO NOTHING
        """,
        data.id, data.msg
    )
    return data
```

---

## Summary

**Key Principles:**

1. **Separate concerns**: Infrastructure lifecycle ≠ message lifecycle
2. **Use closures or registries**: Not `message.meta` for dependencies
3. **Connection pools**: Not sessions in `meta`
4. **Idempotent nodes**: Design for retries
5. **Proper abstractions**: Service interfaces, not concrete classes in nodes
6. **Test with mocks**: Inject mock services via factory

**Before deploying:**
- [ ] No database sessions in `meta`
- [ ] No locks in `meta`
- [ ] All nodes idempotent or properly transactional
- [ ] Connection pools properly initialized
- [ ] Service abstractions allow test mocking
- [ ] Worker pool handles errors and timeouts correctly

For questions, see the main PenguiFlow manual or open an issue.
