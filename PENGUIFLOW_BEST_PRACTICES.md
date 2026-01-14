# PenguiFlow DAG Implementation Guide: Production Best Practices

## Introduction

This guide distills production-tested patterns from an internal PenguiFlow deployment (referenced here as `memory_service`), a memory management system built entirely on PenguiFlow. The codebase demonstrates advanced flow orchestration patterns, error handling, validation, and testing practices to be adopted.

---

## 1. Node Design Patterns

### 1.1 Node Function Structure

**Basic Pattern:**
```python
async def _node_function(message: Message, _ctx: Any) -> Message:
    # 1. Validate and extract message
    base_message = message if isinstance(message, Message) else Message.model_validate(message)

    # 2. Extract and validate payload
    candidate = base_message.payload
    payload = (
        candidate
        if isinstance(candidate, ExpectedPayloadType)
        else ExpectedPayloadType.model_validate(candidate)
    )

    # 3. Execute business logic
    result = await perform_operation(payload)

    # 4. Return updated message with new payload
    return base_message.copy(update={"payload": result})
```

**Real Example from `src/memory_service/memory/ingest_flow.py`:**

```python
async def _normalize_node(message: Message, _ctx: Any) -> Message:
    base_message = message if isinstance(message, Message) else Message.model_validate(message)
    candidate = base_message.payload
    payload = (
        candidate
        if isinstance(candidate, IngestInteractionPayload)
        else IngestInteractionPayload.model_validate(candidate)
    )
    now = datetime.now(UTC)
    provided = payload.request.timestamp
    # ... business logic ...
    normalized = NormalizedInteraction(
        fragment_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        # ... more fields ...
    )
    return base_message.copy(update={"payload": normalized})
```

**Why this pattern?**
- **Defensive parsing:** Handles both Message objects and dictionaries (for different executors)
- **Type safety:** Validates payloads using Pydantic models
- **Immutability:** Uses `copy(update={...})` instead of mutation
- **Preserves envelope:** Maintains headers, trace_id, and meta across the flow

### 1.2 Factory Pattern for Nodes with Dependencies

When nodes need external dependencies (stores, clients, etc.), use factory functions:

```python
def _make_persist_node(
    store: InteractionStore,
) -> Callable[[Message, Any], Awaitable[Message]]:
    async def _persist(message: Message, _ctx: Any) -> Message:
        # Node has access to `store` via closure
        base_message = message if isinstance(message, Message) else Message.model_validate(message)
        # ... use store ...
        await store.persist_fragment(record)
        return base_message.copy(update={"payload": result})

    return _persist
```

**Why factories?**
- **Dependency injection:** Clean way to provide services to nodes
- **Testability:** Easy to mock dependencies in tests
- **Closure safety:** Captures dependencies at creation time

### 1.3 Async vs Sync Nodes

**Use async nodes when:**
- Performing I/O operations (database, network, file system)
- Calling other async functions
- Need to use `await`

**Use sync nodes when:**
- Pure computation (transformations, validation)
- No I/O required
- Performance-critical transformations

**Example from `src/memory_service/memory/ingest_flow.py`:**

```python
# Sync node - pure transformation
async def _normalize_node(message: Message, _ctx: Any) -> Message:
    # No I/O, just data transformation
    # ...
    return base_message.copy(update={"payload": normalized})

# Async node - database I/O
def _make_persist_node(store: InteractionStore) -> Node:
    async def _persist(message: Message, _ctx: Any) -> Message:
        # Awaits database operation
        await store.persist_fragment(record)
        return base_message.copy(update={"payload": persisted})
    return _persist
```

### 1.4 Error Handling in Nodes

**Pattern: Raise domain-specific errors, let NodePolicy handle retries**

```python
async def _apply(message: Message, _ctx: Any) -> Message:
    # ... validation ...

    result = await store.set_fragment_privacy(...)
    if result is None:
        # Raise descriptive domain error
        raise FragmentPrivacyNotFoundError(work_item.fragment_id)

    # ... continue processing ...
    return base_message.copy(update={"payload": updated})
```

**Why?**
- NodePolicy handles transient errors with retries
- Domain errors communicate clear failure reasons
- Flows can catch and transform errors at orchestrator level

### 1.5 Validation Patterns with NodePolicy

**NodePolicy Configuration:**

```python
# Validate output only
Node(
    _normalize_node,
    name="normalize",
    policy=NodePolicy(validate="out"),
)

# Validate both input and output
Node(
    _make_persist_node(store),
    name="persist",
    policy=NodePolicy(validate="both"),
)

# With retries and timeout
Node(
    _make_embed_node(embeddings_client),
    name="embed",
    policy=NodePolicy(
        validate="both",
        max_retries=2,
        backoff_base=0.5,
        backoff_mult=2.0,
        timeout_s=30.0,
    ),
)
```

**From `src/memory_service/memory/auto_retrieve_flow.py`:**

```python
def _make_rerank_node(
    *,
    service_registry: ServiceRegistry,
    lambda_param: float = 0.65,
    timeout_s: float,
) -> Node:
    # ... node implementation ...

    return Node(
        _rerank,
        name="rerank_and_diversify",
        policy=NodePolicy(
            validate="both",           # Validate input and output
            max_retries=2,             # Retry on failure
            backoff_base=0.5,          # Initial backoff
            backoff_mult=2.0,          # Exponential backoff
            timeout_s=timeout_s,       # Timeout protection
        ),
    )
```

**NodePolicy Best Practices:**
- Use `validate="both"` for critical nodes (database writes, external API calls)
- Use `validate="out"` for pure transformations
- Set appropriate `timeout_s` based on expected operation duration
- Use retries for operations that might have transient failures
- Exponential backoff prevents thundering herd

---

## 2. Flow Composition

### 2.1 Building Flows with create()

**Pattern: Linear flow construction**

```python
from penguiflow.core import create

# Define nodes
normalize_node = Node(_normalize, name="normalize", policy=NodePolicy(validate="out"))
persist_node = Node(_persist, name="persist", policy=NodePolicy(validate="both"))
finalize_node = Node(_finalize, name="finalize", policy=NodePolicy(validate="both"))

# Compose flow with .to() edges
flow = create(
    normalize_node.to(persist_node),
    persist_node.to(finalize_node),
)
```

**Complex flow from `src/memory_service/memory/auto_retrieve_flow.py`:**

```python
flow = create(
    prepare_node.to(search_node),
    search_node.to(rerank_node),
    rerank_node.to(finalize_node),
)
```

### 2.2 Edge Patterns

**Direct edges:**
```python
# A -> B
node_a.to(node_b)
```

**Multiple edges in create():**
```python
flow = create(
    node_a.to(node_b),
    node_b.to(node_c),
    node_c.to(node_d),
)
```

### 2.3 Flow Bundle Pattern

**Organize flow components in a dataclass:**

```python
from dataclasses import dataclass

@dataclass(slots=True)
class _FlowBundle:
    flow: PenguiFlow
    registry: ModelRegistry
    normalize_node: Node
    persist_node: Node
    finalize_node: Node

def _build_flow(*, store: InteractionStore, queue: EmbeddingQueue) -> _FlowBundle:
    """Create the PenguiFlow definition for ingesting interactions."""

    # Create nodes
    normalize_node = Node(...)
    persist_node = Node(...)
    finalize_node = Node(...)

    # Build flow
    flow = create(
        normalize_node.to(persist_node),
        persist_node.to(finalize_node),
    )

    # Create registry
    registry = ModelRegistry()
    registry.register("normalize", Message, Message)
    registry.register("persist", Message, Message)
    registry.register("finalize", Message, Message)

    return _FlowBundle(
        flow=flow,
        registry=registry,
        normalize_node=normalize_node,
        persist_node=persist_node,
        finalize_node=finalize_node,
    )
```

**Why this pattern?**
- **Encapsulation:** All flow components in one place
- **Testability:** Easy access to individual nodes for testing
- **Type safety:** Explicit return type with all components

---

## 3. Orchestrator Patterns

### 3.1 CRITICAL: Why NOT to Use run_one() in Production

**❌ ANTI-PATTERN - DO NOT DO THIS:**

```python
class BadOrchestrator:
    async def execute(self, payload: SomePayload) -> SomeResponse:
        message = Message(payload=payload, headers=Headers(...))

        # ❌ WRONG: run_one() is for testing only!
        result = await run_one(self._flow, message, registry=self._registry)
        return result.payload
```

**✅ CORRECT PATTERN - Use emit/fetch:**

```python
class GoodOrchestrator:
    def __init__(self, service_registry: ServiceRegistry):
        bundle = _build_flow(...)
        self._flow = bundle.flow
        self._registry = bundle.registry

        # Start flow once during initialization
        self._flow.run(registry=self._registry)
        self._flow_started = True

    async def execute(self, payload: SomePayload) -> SomeResponse:
        message = Message(payload=payload, headers=Headers(...))

        # ✅ CORRECT: emit/fetch on running flow
        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Handle result
        if isinstance(result, Message):
            result_payload = result.payload
        else:
            result_payload = result

        return SomeResponse.model_validate(result_payload)
```

**Why this matters (from commit c188c30):**

1. **`run_one()` is a testing utility** - It starts and stops the flow for each message
2. **Production requires persistent flows** - Start once in `__init__`, reuse for all requests
3. **Performance** - Starting/stopping flows is expensive
4. **Concurrency** - Running flows support concurrent message processing
5. **Middleware** - Only running flows execute middleware correctly

**Evidence from `src/memory_service/memory/ingest_flow.py`:**

```python
class IngestInteractionOrchestrator:
    def __init__(
        self,
        service_registry: ServiceRegistry,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        # ... setup ...
        bundle = _build_flow(store=self._store, queue=self._queue)
        self._flow = bundle.flow
        self._registry = bundle.registry
        self._timeout_s = timeout_s
        # Start flow once - critical pattern
        self._flow.run(registry=self._registry)
        self._flow_started = True

    async def ingest(self, payload: IngestInteractionPayload) -> IngestInteractionResponse:
        """Execute the ingest flow for the provided payload."""
        message = Message(
            payload=payload,
            headers=Headers(
                tenant=payload.request.tenant_id,
                topic="memory.ingest",
            ),
        )
        # Follow PenguiFlow best practices: emit/fetch on running flow
        await self._flow.emit(message)
        result = await self._flow.fetch()
        # ... process result ...
        return response
```

### 3.2 Complete Orchestrator Pattern

**Full orchestrator lifecycle:**

```python
from penguiflow.errors import FlowError

class MyOrchestrator:
    def __init__(
        self,
        service_registry: ServiceRegistry,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        self._service_registry = service_registry
        self._store = service_registry.get_store()

        # Build flow components
        bundle = _build_flow(
            store=self._store,
            # ... other dependencies ...
        )
        self._flow = bundle.flow
        self._registry = bundle.registry
        self._timeout_s = timeout_s

        # Optional: Add middleware
        self._flow.add_middleware(log_flow_events(...))

        # Start flow once - CRITICAL
        self._flow.run(registry=self._registry)
        self._flow_started = True

    async def execute(self, payload: MyPayload) -> MyResponse:
        """Execute the flow for the provided payload."""
        message = Message(
            payload=payload,
            headers=Headers(
                tenant=payload.tenant_id,
                topic="my.operation",
            ),
        )

        # Emit and fetch on running flow
        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Handle FlowError
        if isinstance(result, FlowError):
            raise MyFlowError(result)

        # Extract payload
        if isinstance(result, Message):
            result_payload = result.payload
        else:
            result_payload = result

        # Validate and return
        if isinstance(result_payload, MyResponse):
            response = result_payload
        else:
            response = MyResponse.model_validate(result_payload)

        return response

    async def stop(self) -> None:
        """Stop the flow during application shutdown."""
        if self._flow_started:
            await self._flow.stop()
            self._flow_started = False

    @property
    def store(self):
        """Expose for testing and diagnostics."""
        return self._store
```

### 3.3 Error Handling in Orchestrators

**Pattern from `src/memory_service/memory/auto_retrieve_flow.py`:**

```python
class AutoRetrieveFlowError(RuntimeError):
    """Raised when the auto_retrieve flow surfaces a FlowError."""

    def __init__(self, flow_error: FlowError) -> None:
        message = flow_error.message or str(flow_error)
        super().__init__(message)
        self.flow_error = flow_error

class AutoRetrieveOrchestrator:
    async def auto_retrieve(self, payload: AutoRetrievePayload) -> AutoRetrieveResponse:
        # ... setup message ...
        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Check for FlowError
        if isinstance(result, FlowError):
            _LOGGER.error(
                "Auto retrieve flow failed: code=%s, message=%s",
                result.code,
                result.message,
                extra={"trace_id": message.trace_id, "flow_error": result.to_payload()},
            )
            raise AutoRetrieveFlowError(result)

        # ... process successful result ...
```

**Why wrap FlowError?**
- **Domain-specific context:** Convert generic flow errors to business errors
- **Logging:** Capture error details before raising
- **Error chaining:** Preserve original FlowError for debugging
- **API contracts:** Return domain errors to callers

### 3.4 Service Integration Patterns

**Dependency injection via ServiceRegistry:**

```python
class EmbeddingJobProcessor:
    def __init__(
        self,
        service_registry: ServiceRegistry,
        *,
        actor_id: str = "embedding-worker",
        timeout_s: float = 10.0,
    ) -> None:
        # Extract dependencies from registry
        self._service_registry = service_registry
        self._store = service_registry.get_interaction_store()
        self._embeddings_client = service_registry.get_embeddings_client()
        self._vector_index = service_registry.get_vector_index()

        # Build flow with dependencies
        bundle = _build_flow(
            store=self._store,
            embeddings_client=self._embeddings_client,
            vector_index=self._vector_index,
            actor_id=actor_id,
        )
        # ...
```

---

## 4. Model Registry

### 4.1 Registering Input/Output Models

**Pattern:**

```python
from penguiflow.registry import ModelRegistry
from penguiflow.types import Message

registry = ModelRegistry()
registry.register("node_name", Message, Message)
```

**Complete example from `src/memory_service/memory/ingest_flow.py`:**

```python
def _build_flow(*, store: InteractionStore, queue: EmbeddingQueue) -> _FlowBundle:
    # Create nodes
    normalize_node = Node(
        _normalize_node,
        name="normalize",
        policy=NodePolicy(validate="out"),
    )
    persist_node = Node(
        _make_persist_node(store),
        name="persist",
        policy=NodePolicy(validate="both"),
    )
    finalize_node = Node(
        _make_finalize_node(store, queue),
        name="finalize",
        policy=NodePolicy(validate="both"),
    )

    # Build flow
    flow = create(
        normalize_node.to(persist_node),
        persist_node.to(finalize_node),
    )

    # Register models - node name must match Node(name=...)
    registry = ModelRegistry()
    registry.register("normalize", Message, Message)
    registry.register("persist", Message, Message)
    registry.register("finalize", Message, Message)

    return _FlowBundle(
        flow=flow,
        registry=registry,
        normalize_node=normalize_node,
        persist_node=persist_node,
        finalize_node=finalize_node,
    )
```

### 4.2 Why Model Registration is Important

1. **Runtime validation:** PenguiFlow validates payloads against registered models
2. **Type safety:** Catches type mismatches early
3. **Documentation:** Registry serves as flow schema documentation
4. **Debugging:** Clear error messages when validation fails

### 4.3 Validation Patterns

**Using NodePolicy for validation:**

```python
# Validate output only (transformation nodes)
policy=NodePolicy(validate="out")

# Validate both input and output (I/O nodes)
policy=NodePolicy(validate="both")

# No validation (pure pass-through, rare)
policy=NodePolicy(validate="none")
```

**When to validate:**
- **"out"**: Transformation nodes where you control the output
- **"both"**: Nodes with external I/O (database, API calls)
- **"none"**: Only when performance is critical and types are guaranteed

---

## 5. Testing Patterns

### 5.1 Unit Testing Individual Nodes

**Using `assert_preserves_message_envelope` from `tests/test_ingest_flow.py`:**

```python
from penguiflow.testkit import assert_preserves_message_envelope
from penguiflow.types import Headers, Message

@pytest.mark.asyncio
async def test_normalize_node_preserves_envelope() -> None:
    store = InMemoryInteractionStore()
    queue = InMemoryEmbeddingQueue()
    bundle = _build_flow(store=store, queue=queue)

    message = Message(
        payload=IngestInteractionPayload(...),
        headers=Headers(tenant="tenant-123", topic="memory.ingest"),
        trace_id="trace-test-123",
        meta={"origin": "unit-test"},
    )

    # This helper verifies the node preserves envelope
    normalized_message = await assert_preserves_message_envelope(
        bundle.normalize_node,
        message=message,
    )

    # Assertions
    assert normalized_message.headers == message.headers
    assert normalized_message.trace_id == message.trace_id
    # Payload should be transformed
    assert normalized_message.payload.fragment_id
```

**Why test envelope preservation?**
- Ensures headers, trace_id, and meta flow through correctly
- Critical for distributed tracing and observability
- Common source of bugs when nodes mutate instead of copy

### 5.2 Integration Testing Flows with run_one()

**THIS IS WHERE run_one() BELONGS - IN TESTS:**

```python
from penguiflow.testkit import (
    assert_node_sequence,
    run_one,
)

@pytest.mark.asyncio
async def test_flowtestkit_records_db_effects_and_mlflow_meta() -> None:
    store = InMemoryInteractionStore()
    queue = InMemoryEmbeddingQueue()
    bundle = _build_flow(store=store, queue=queue)

    message = Message(
        payload=IngestInteractionPayload(...),
        headers=Headers(tenant="tenant-abc", topic="memory.ingest"),
        trace_id="trace-flow-123",
        meta={"origin": "integration-test"},
    )

    # run_one() is perfect for testing - starts, runs, stops flow
    result_message = await run_one(
        bundle.flow,
        message,
        registry=bundle.registry,
    )

    # Verify node execution order
    assert_node_sequence(
        message.trace_id,
        ["normalize", "persist", "finalize"],
    )

    # Check side effects
    fragments = store.list_fragments()
    assert len(fragments) == 1

    jobs = queue.drain()
    assert len(jobs) == 1
```

**Key takeaway:**
- ✅ **Use `run_one()` in tests** - It's designed for test scenarios
- ❌ **Never use `run_one()` in production code** - Use emit/fetch instead

### 5.3 Using Stubs and Mocks

**Stub pattern from `tests/test_auto_retrieve_flow.py`:**

```python
class _EmbeddingsClientStub:
    backend = "local"
    model = "test-model"

    def __init__(self, *, vector: list[float]) -> None:
        self._vector = vector
        self.requests: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.requests.append(texts)
        return [self._vector]

@pytest.mark.asyncio
async def test_auto_retrieve_with_stub() -> None:
    settings = PenguiSettings(env="test")
    registry = build_service_registry(settings)

    # Inject stub
    registry._embeddings_client = _EmbeddingsClientStub(vector=[0.1, 0.2, 0.3])

    orchestrator = AutoRetrieveOrchestrator(registry)
    response = await orchestrator.auto_retrieve(payload)

    # Verify stub was called
    assert registry._embeddings_client.requests
```

### 5.4 Testing Error Scenarios

**Using `simulate_error` from testkit:**

```python
from penguiflow.testkit import simulate_error, get_recorded_events

@pytest.mark.asyncio
async def test_node_retry_on_transient_error() -> None:
    # ... setup ...

    # Simulate error on first attempt
    simulate_error(
        bundle.embed_node,
        error=RuntimeError("temporary failure"),
        on_attempt=1,
    )

    # Node should retry and succeed
    await run_one(bundle.flow, message, registry=bundle.registry)

    # Verify retry happened
    events = get_recorded_events(message.trace_id)
    retry_events = [e for e in events if e.event_type == "node_retry"]
    assert len(retry_events) >= 1
```

---

## 6. Common Pitfalls to Avoid

### 6.1 Using run_one() in Production

**❌ WRONG:**
```python
async def my_operation(payload):
    result = await run_one(self._flow, message, registry=self._registry)
    return result
```

**✅ CORRECT:**
```python
def __init__(...):
    self._flow.run(registry=self._registry)

async def my_operation(payload):
    await self._flow.emit(message)
    result = await self._flow.fetch()
    return result
```

**Evidence:** Commit `c188c30` removed all `run_one()` calls from orchestrators, replacing with emit/fetch pattern.

### 6.2 Mutating Messages Instead of Copying

**❌ WRONG:**
```python
async def bad_node(message: Message, _ctx: Any) -> Message:
    message.payload = new_payload  # Mutation!
    return message
```

**✅ CORRECT:**
```python
async def good_node(message: Message, _ctx: Any) -> Message:
    return message.copy(update={"payload": new_payload})
```

### 6.3 Forgetting to Register Models

**❌ WRONG:**
```python
flow = create(node_a.to(node_b))
registry = ModelRegistry()
# Oops, forgot to register!
```

**✅ CORRECT:**
```python
flow = create(node_a.to(node_b))
registry = ModelRegistry()
registry.register("node_a", Message, Message)
registry.register("node_b", Message, Message)
```

**Symptoms if you forget:**
- Runtime validation errors
- "Model not registered" exceptions
- Flows fail to start

### 6.4 Not Handling FlowError

**❌ WRONG:**
```python
async def execute(payload):
    await self._flow.emit(message)
    result = await self._flow.fetch()
    # Assuming result is always Message
    return result.payload
```

**✅ CORRECT:**
```python
async def execute(payload):
    await self._flow.emit(message)
    result = await self._flow.fetch()

    # Check for FlowError
    if isinstance(result, FlowError):
        raise MyDomainError(result)

    # Then extract payload
    if isinstance(result, Message):
        return result.payload
    return result
```

### 6.5 Incorrect Node Names in Registry

**❌ WRONG:**
```python
node = Node(func, name="process")
registry.register("processor", Message, Message)  # Mismatch!
```

**✅ CORRECT:**
```python
node = Node(func, name="process")
registry.register("process", Message, Message)  # Matches node name
```

### 6.6 Not Starting the Flow

**❌ WRONG:**
```python
def __init__(...):
    self._flow = bundle.flow
    # Forgot to start!

async def execute(...):
    await self._flow.emit(message)  # Will fail!
```

**✅ CORRECT:**
```python
def __init__(...):
    self._flow = bundle.flow
    self._flow.run(registry=self._registry)
    self._flow_started = True
```

### 6.7 Not Implementing stop()

**❌ WRONG:**
```python
class MyOrchestrator:
    # No stop method - flow keeps running
```

**✅ CORRECT:**
```python
class MyOrchestrator:
    async def stop(self) -> None:
        """Stop the flow during application shutdown."""
        if self._flow_started:
            await self._flow.stop()
            self._flow_started = False
```

**Why it matters:**
- Graceful shutdown
- Resource cleanup
- Prevents hanging flows in tests

---

## 7. Advanced Patterns

### 7.1 Middleware Integration

**From `src/memory_service/memory/auto_retrieve_flow.py`:**

```python
from penguiflow import log_flow_events

class AutoRetrieveOrchestrator:
    def __init__(self, service_registry: ServiceRegistry, *, telemetry: MemoryRetrievalTelemetry):
        # ... build flow ...

        # Add logging middleware
        self._flow.add_middleware(
            log_flow_events(
                telemetry.logger,
                latency_callback=telemetry.record_flow_latency,
            )
        )

        # Add custom telemetry middleware
        self._flow.add_middleware(telemetry.record_flow_event)

        # Start flow AFTER adding middleware
        self._flow.run(registry=self._registry)
```

**Middleware best practices:**
- Add middleware BEFORE calling `flow.run()`
- Order matters - first added, first executed
- Use for cross-cutting concerns (logging, metrics, tracing)

### 7.2 Conditional Logic in Nodes

**Pattern from `src/memory_service/memory/auto_retrieve_flow.py`:**

```python
async def _rerank(message: Message, _ctx: Any) -> Message:
    # ... extract state ...

    # Conditional logic based on configuration
    bucket = _rollout_bucket(tenant_id=request.tenant_id, user_id=request.user_id)
    should_attempt = (
        enabled
        and rollout > 0
        and bucket < rollout
        and not str(getattr(reranker_client, "backend", "")).startswith("heuristic")
    )

    if should_attempt:
        try:
            # Try advanced reranking
            rerank_results = await reranker_client.rerank(...)
        except Exception as exc:
            # Fallback on error
            fallback_reason = exc.__class__.__name__
            rerank_results = []
    else:
        # Use heuristic fallback
        rerank_results = []

    # Continue with results (reranked or fallback)
    # ...
```

**Pattern: Feature flags + fallbacks**
- Feature flags control advanced behavior
- Always have a fallback path
- Log why fallback was used
- Don't raise errors for expected fallbacks

### 7.3 Metadata Propagation

**Tracking flow context through meta:**

```python
# In node
async def _finalize(message: Message, _ctx: Any) -> Message:
    # ... process ...

    # Update meta
    meta = dict(base_message.meta or {})
    mlflow_events = list(meta.get("mlflow_events", ()))
    mlflow_events.append({
        "event": "memory_event.created",
        "fragment_id": persisted.fragment_id,
        # ...
    })
    meta["mlflow_events"] = mlflow_events

    return base_message.copy(update={"payload": response, "meta": meta})

# In orchestrator
async def execute(payload):
    await self._flow.emit(message)
    result = await self._flow.fetch()

    # Extract meta
    meta = dict(result.meta or {})
    mlflow_events = tuple(meta.get("mlflow_events", ()))

    # Process events
    if mlflow_events:
        self._telemetry.emit_mlflow_events(mlflow_events)
```

### 7.4 Multi-Stage Payload Transformation

**Pattern: Evolving payload types through the flow**

```python
# Stage 1: Raw request
class RequestPayload(BaseModel):
    tenant_id: str
    user_id: str
    data: str

# Stage 2: Validated and normalized
class NormalizedPayload(BaseModel):
    tenant_id: str
    user_id: str
    fragment_id: str
    timestamp: datetime
    data: str

# Stage 3: Persisted
class PersistedPayload(BaseModel):
    fragment_id: str
    tenant_id: str
    timestamp: datetime

# Stage 4: Final response
class ResponsePayload(BaseModel):
    id: str
    success: bool

# Nodes transform through stages
async def normalize(msg: Message, _ctx: Any) -> Message:
    req = RequestPayload.model_validate(msg.payload)
    normalized = NormalizedPayload(...)
    return msg.copy(update={"payload": normalized})

async def persist(msg: Message, _ctx: Any) -> Message:
    norm = NormalizedPayload.model_validate(msg.payload)
    persisted = PersistedPayload(...)
    return msg.copy(update={"payload": persisted})

async def finalize(msg: Message, _ctx: Any) -> Message:
    pers = PersistedPayload.model_validate(msg.payload)
    response = ResponsePayload(...)
    return msg.copy(update={"payload": response})
```

**Why this works:**
- Clear stages with distinct responsibilities
- Type safety at each stage
- Easy to debug (know exactly what data exists at each step)
- Pydantic validation catches errors early

---

## 8. Case Study: Implementing Telemetry Middleware for Error Visibility

### 8.1 The Problem: Hidden Flow Errors

**Scenario:** During development of the `IngestInteractionOrchestrator`, classification nodes were failing with cryptic logs:

```
INFO memory_service.observability.events: memory_event.feedback_classified
ERROR penguiflow.core: node_error
ERROR penguiflow.core: node_failed
```

**No exception details, no stack traces, no actual error information!** The flow was swallowing exceptions.

### 8.2 The Root Cause

PenguiFlow's default error handling captures exceptions internally as `FlowError` objects, but without telemetry middleware, the detailed error payloads remain trapped in the flow's internal state. The orchestrator only sees generic failure events.

**What we were missing:**
- Full exception tracebacks
- Error context (which node, what data)
- Detailed error payloads from FlowEvents
- Visibility into the flow lifecycle

### 8.3 The Solution: Telemetry Middleware Pattern

The `MemoryRetrievalTelemetry` class provides structured observability by:
1. Intercepting FlowEvents at the middleware level
2. Extracting detailed error payloads
3. Logging comprehensive error information
4. Emitting structured events to MLflow/observability systems

**Implementation from `src/memory_service/observability/__init__.py`:**

```python
class MemoryRetrievalTelemetry:
    """Telemetry middleware for memory flows."""

    def __init__(
        self,
        *,
        tracking_uri: str,
        flow_name: str = "memory.operation",
    ) -> None:
        self.tracking_uri = tracking_uri
        self.flow_name = flow_name
        self.logger = logging.getLogger(f"memory_service.{flow_name}")
        # MLflow stub for structured event emission
        self._mlflow_stub = MLflowClientStub(tracking_uri=tracking_uri)

    async def record_flow_event(self, event: FlowEvent) -> FlowEvent:
        """Middleware function that intercepts all flow events."""
        event_type = event.event_type

        # Log node lifecycle
        if event_type == "node_start":
            self.logger.debug(
                "node_start",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                },
            )
        elif event_type == "node_success":
            self.logger.debug(
                "node_success",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "latency_ms": event.latency_ms,
                },
            )
        elif event_type == "node_error":
            # THIS IS THE CRITICAL PART - Extract error details!
            error_payload = event.error_payload or {}

            self.logger.error(
                "node_error",
                extra={
                    "node": event.node_name,
                    "trace_id": event.trace_id,
                    "error_class": error_payload.get("error_class"),
                    "error_message": error_payload.get("error_message"),
                    "error_traceback": error_payload.get("error_traceback"),
                    "flow_error_code": error_payload.get("code"),
                    "flow_error_message": error_payload.get("message"),
                    # Include full payload for debugging
                    **error_payload,
                },
            )

        # Always return the event unmodified
        return event

    def emit_mlflow_events(self, events: tuple[dict[str, Any], ...]) -> None:
        """Emit batched events to MLflow."""
        for event_data in events:
            self._mlflow_stub.log_event(
                flow=self.flow_name,
                event=event_data.get("event", "unknown"),
                payload=event_data.get("payload", {}),
            )
```

### 8.4 Wiring Telemetry into Orchestrators

**BEFORE (No observability - the problematic version):**

```python
class IngestInteractionOrchestrator:
    def __init__(self, service_registry: ServiceRegistry, *, timeout_s: float = 5.0):
        bundle = _build_flow(store=self._store, queue=self._queue)
        self._flow = bundle.flow
        self._registry = bundle.registry

        # Missing telemetry!
        self._flow.run(registry=self._registry)
        self._flow_started = True
```

**AFTER (Full observability - the fixed version):**

```python
from penguiflow import log_flow_events
from memory_service.observability import MemoryRetrievalTelemetry

class IngestInteractionOrchestrator:
    def __init__(
        self,
        service_registry: ServiceRegistry,
        *,
        timeout_s: float = 5.0,
        telemetry: MemoryRetrievalTelemetry | None = None,  # Injectable
    ):
        bundle = _build_flow(store=self._store, queue=self._queue)
        self._flow = bundle.flow
        self._registry = bundle.registry

        # Create or use provided telemetry
        self._telemetry = telemetry or MemoryRetrievalTelemetry(
            tracking_uri=settings.mlflow_tracking_uri,
            flow_name="memory.ingest",
        )

        # CRITICAL: Add middleware BEFORE flow.run()
        # 1. PenguiFlow's built-in event logger
        self._flow.add_middleware(
            log_flow_events(
                self._telemetry.logger,
                latency_callback=self._telemetry.record_flow_latency,
            )
        )
        # 2. Custom telemetry middleware for detailed error extraction
        self._flow.add_middleware(self._telemetry.record_flow_event)

        # Now start the flow with middleware active
        self._flow.run(registry=self._registry)
        self._flow_started = True

    async def ingest(self, payload: IngestInteractionPayload) -> IngestInteractionResponse:
        message = Message(
            payload=payload,
            headers=Headers(tenant=payload.request.tenant_id, topic="memory.ingest"),
        )

        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Extract MLflow events from message metadata
        if isinstance(result, Message):
            mlflow_events = tuple(result.meta.get("mlflow_events", ()))
            if mlflow_events:
                # Emit collected events to observability system
                self._telemetry.emit_mlflow_events(mlflow_events)
            result_payload = result.payload
        else:
            result_payload = result

        return IngestInteractionResponse.model_validate(result_payload)
```

### 8.5 The Breakthrough: Seeing the Real Error

With telemetry middleware in place, we immediately saw the actual error that was hidden before:

**WITHOUT telemetry (cryptic):**
```
ERROR penguiflow.core: node_error
ERROR penguiflow.core: node_failed
```

**WITH telemetry (actionable!):**
```json
{
  "event_type": "node_error",
  "node": "classify_intent",
  "trace_id": "abc123",
  "error_class": "IntegrityError",
  "error_message": "CHECK constraint failed: event_type IN ('created', 'recalled', 'auto_recalled', 'reinforced', 'privacy_changed', 'embedded', 'embedding_failed', 'shared')",
  "flow_error_message": "Node 'classify_intent' raised IntegrityError",
  "flow_error_code": "node_execution_failed",
  "error_traceback": "Traceback (most recent call last):\n  File \"...\", line 350, in _classify\n    await store.log_event(...)\nsqlalchemy.exc.IntegrityError: CHECK constraint failed...",
  "SQL": "INSERT INTO memory_event (event_id, event_type, timestamp, fragment_id, actor, metadata) VALUES (?, ?, ?, ?, ?, ?)",
  "parameters": ["uuid", "feedback_classified", "2025-10-14...", "fragment-id", "actor", "{}"]
}
```

**The problem was instantly clear:** The code was trying to insert `event_type='feedback_classified'` but the database CHECK constraint didn't allow it!

### 8.6 Key Patterns for Telemetry Middleware

**1. Middleware Order Matters:**
```python
# CORRECT: Add middleware BEFORE flow.run()
self._flow.add_middleware(log_flow_events(...))      # First
self._flow.add_middleware(telemetry.record_flow_event)  # Second
self._flow.run(registry=self._registry)

# WRONG: Adding after run() has no effect
self._flow.run(registry=self._registry)
self._flow.add_middleware(...)  # Too late!
```

**2. Extract Error Payloads from FlowEvents:**
```python
async def record_flow_event(self, event: FlowEvent) -> FlowEvent:
    if event.event_type == "node_error":
        # FlowEvent.error_payload contains the rich error information
        error_payload = event.error_payload or {}

        # Log everything for debugging
        self.logger.error(
            "node_error",
            extra={
                "node": event.node_name,
                "trace_id": event.trace_id,
                # Unpack all error details
                **error_payload,
            },
        )

    # Always return event unmodified - middleware is read-only
    return event
```

**3. Injectable Telemetry for Testing:**
```python
class MyOrchestrator:
    def __init__(
        self,
        service_registry: ServiceRegistry,
        *,
        telemetry: MyTelemetry | None = None,  # Optional injection
    ):
        # Use provided telemetry or create default
        self._telemetry = telemetry or MyTelemetry(...)

        # Wire it up
        self._flow.add_middleware(self._telemetry.record_flow_event)
```

This allows tests to inject mock telemetry or disable it entirely.

**4. Structured Event Collection in Nodes:**
```python
async def _finalize(message: Message, _ctx: Any) -> Message:
    # ... business logic ...

    # Collect events in message metadata
    meta = dict(base_message.meta or {})
    mlflow_events = list(meta.get("mlflow_events", ()))

    # Add event with structured data
    mlflow_events.append({
        "event": "memory_event.created",
        "payload": {
            "flow": "memory.ingest",
            "fragment_hash": hash_identifier(fragment_id),
            "tenant_hash": hash_identifier(tenant_id),
        }
    })

    meta["mlflow_events"] = mlflow_events
    return base_message.copy(update={"payload": response, "meta": meta})
```

**5. Extract and Emit Events in Orchestrator:**
```python
async def execute(self, payload):
    await self._flow.emit(message)
    result = await self._flow.fetch()

    # Extract events from final message
    if isinstance(result, Message):
        mlflow_events = tuple(result.meta.get("mlflow_events", ()))
        if mlflow_events:
            # Send to observability backend
            self._telemetry.emit_mlflow_events(mlflow_events)

    return response
```

### 8.7 Debugging Workflow with Telemetry

**The debugging cycle that telemetry enabled:**

1. **Observe cryptic error:** "node_error" with no details
2. **Add telemetry middleware** to IngestInteractionOrchestrator
3. **Restart server** with debug logging enabled
4. **Trigger the flow** with a test request
5. **See full error immediately** in logs with:
   - Exact SQL statement that failed
   - Parameter values being inserted
   - Complete stack trace
   - FlowError code and message
6. **Identify root cause** (CHECK constraint violation)
7. **Fix the issue** (add missing event types to migration)
8. **Verify fix** by seeing successful events in logs

**Before telemetry:** Hours of blind debugging, adding print statements, guessing

**After telemetry:** Minutes to root cause with actionable error information

### 8.8 Production Rollout Pattern

**Apply this pattern to ALL orchestrators:**

```python
# memory/ingest_flow.py ✅ (Done)
class IngestInteractionOrchestrator:
    def __init__(self, ..., telemetry: MemoryRetrievalTelemetry | None = None):
        self._telemetry = telemetry or MemoryRetrievalTelemetry(...)
        self._flow.add_middleware(log_flow_events(...))
        self._flow.add_middleware(self._telemetry.record_flow_event)

# memory/auto_retrieve_flow.py ✅ (Already has it)
class AutoRetrieveOrchestrator:
    def __init__(self, ..., telemetry: MemoryRetrievalTelemetry | None = None):
        # Same pattern

# memory/reinforce_flow.py ⚠️ (TODO)
class ReinforceFragmentOrchestrator:
    def __init__(self, ..., telemetry: MemoryRetrievalTelemetry | None = None):
        # TODO: Add telemetry middleware

# memory/set_privacy_flow.py ⚠️ (TODO)
# memory/share_flow.py ⚠️ (TODO)
# memory/unshare_flow.py ⚠️ (TODO)
# memory/federated_query_flow.py ⚠️ (TODO)
# memory/embedding_flow.py ✅ (Already has EmbeddingWorkerTelemetry)
```

### 8.9 Telemetry Best Practices Summary

**DO:**
- ✅ Add middleware BEFORE `flow.run()`
- ✅ Extract `error_payload` from FlowEvents for full error context
- ✅ Log with structured `extra={}` parameters for queryable logs
- ✅ Make telemetry injectable for testing
- ✅ Collect events in message `meta` and emit at orchestrator level
- ✅ Use the same telemetry pattern across all orchestrators
- ✅ Include trace_id in all log entries for request correlation

**DON'T:**
- ❌ Add middleware after `flow.run()` - it won't work
- ❌ Mutate FlowEvents in middleware - always return unmodified
- ❌ Ignore `error_payload` - it contains the crucial debugging info
- ❌ Use print statements instead of structured logging
- ❌ Forget to wire telemetry in new orchestrators

### 8.10 The Impact: Production-Ready Error Visibility

With telemetry middleware consistently applied:

- **Errors are visible immediately** - No more silent failures
- **Root cause analysis is fast** - Full context in logs
- **Debugging is systematic** - Trace IDs link related events
- **Monitoring is actionable** - Alert on specific error patterns
- **Performance tracking works** - Latency metrics per node
- **Production is observable** - MLflow events show system health

**This is the pattern that turns PenguiFlow from a local development tool into a production-grade orchestration framework.**

---

## 9. Production Checklist

Before deploying a PenguiFlow-based system:

- [ ] **All orchestrators use emit/fetch** (not run_one)
- [ ] **Flow.run() called once in __init__**
- [ ] **All nodes registered in ModelRegistry**
- [ ] **Node names match registry entries**
- [ ] **NodePolicy configured appropriately**
  - [ ] Timeouts set based on expected duration
  - [ ] Retries for transient failures
  - [ ] Validation levels chosen correctly
- [ ] **Error handling in orchestrators**
  - [ ] FlowError checked and handled
  - [ ] Domain errors raised with context
- [ ] **Graceful shutdown implemented**
  - [ ] stop() method exists
  - [ ] Flow stopped on shutdown
- [ ] **Tests cover**
  - [ ] Individual nodes (with assert_preserves_message_envelope)
  - [ ] Full flows (with run_one in tests)
  - [ ] Error scenarios (with simulate_error)
  - [ ] Node sequences (with assert_node_sequence)
- [ ] **Message envelope preserved**
  - [ ] Headers maintained
  - [ ] trace_id propagated
  - [ ] meta carried through
- [ ] **Middleware added before run()**
- [ ] **Dependencies injected via factories**

---

## 9. Quick Reference

### Orchestrator Template

```python
from penguiflow.core import PenguiFlow, create
from penguiflow.errors import FlowError
from penguiflow.node import Node, NodePolicy
from penguiflow.registry import ModelRegistry
from penguiflow.types import Headers, Message

class MyOrchestrator:
    def __init__(self, service_registry: ServiceRegistry, *, timeout_s: float = 5.0):
        # Extract dependencies
        self._store = service_registry.get_store()

        # Build flow
        bundle = _build_flow(store=self._store)
        self._flow = bundle.flow
        self._registry = bundle.registry

        # Add middleware (optional)
        # self._flow.add_middleware(...)

        # Start flow once
        self._flow.run(registry=self._registry)
        self._flow_started = True

    async def execute(self, payload: MyPayload) -> MyResponse:
        # Create message
        message = Message(
            payload=payload,
            headers=Headers(tenant=payload.tenant_id, topic="my.operation"),
        )

        # Execute flow
        await self._flow.emit(message)
        result = await self._flow.fetch()

        # Handle errors
        if isinstance(result, FlowError):
            raise MyFlowError(result)

        # Extract payload
        if isinstance(result, Message):
            result_payload = result.payload
        else:
            result_payload = result

        # Validate and return
        return MyResponse.model_validate(result_payload)

    async def stop(self) -> None:
        if self._flow_started:
            await self._flow.stop()
            self._flow_started = False
```

### Node Template

```python
async def _my_node(message: Message, _ctx: Any) -> Message:
    # 1. Extract message
    base_message = message if isinstance(message, Message) else Message.model_validate(message)

    # 2. Extract and validate payload
    candidate = base_message.payload
    payload = (
        candidate
        if isinstance(candidate, MyPayload)
        else MyPayload.model_validate(candidate)
    )

    # 3. Business logic
    result = await do_something(payload)

    # 4. Return updated message
    return base_message.copy(update={"payload": result})
```

### Flow Builder Template

```python
from dataclasses import dataclass

@dataclass(slots=True)
class _FlowBundle:
    flow: PenguiFlow
    registry: ModelRegistry
    node_a: Node
    node_b: Node

def _build_flow(*, store: Store) -> _FlowBundle:
    # Create nodes
    node_a = Node(_node_a_func, name="node_a", policy=NodePolicy(validate="both"))
    node_b = Node(_node_b_func, name="node_b", policy=NodePolicy(validate="both"))

    # Build flow
    flow = create(node_a.to(node_b))

    # Register models
    registry = ModelRegistry()
    registry.register("node_a", Message, Message)
    registry.register("node_b", Message, Message)

    return _FlowBundle(flow=flow, registry=registry, node_a=node_a, node_b=node_b)
```

---

## Conclusion

The `memory_service` codebase demonstrates production-ready PenguiFlow patterns that have been battle-tested in a complex memory management system. The most critical lesson is the distinction between testing utilities (`run_one()`) and production patterns (emit/fetch on running flows).

Key principles to remember:

1. **Start flows once, reuse for all messages**
2. **Always handle FlowError explicitly**
3. **Preserve message envelope through copy(update={...})**
4. **Register all models in ModelRegistry**
5. **Use NodePolicy for validation, retries, and timeouts**
6. **Implement graceful shutdown with stop()**
7. **Test with run_one(), deploy with emit/fetch**

By following these patterns, your PenguiFlow-based systems will be robust, maintainable, and production-ready.
