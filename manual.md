PenguiFlow Usage Manual

  1. Core Concepts

  1.1 Architecture Overview

  PenguiFlow is an in-process asyncio orchestrator that manages typed message flows through a directed graph of nodes. Key principles:

  - Bounded queues (asyncio.Queue) per edge enforce backpressure
  - OpenSea (ingress) and Rookery (egress) are synthetic endpoints
  - Messages are Pydantic models with headers, trace_id, and mutable meta bag
  - Nodes wrap async functions with validation, retry, and timeout policies

  1.2 Runtime Lifecycle

  from penguiflow import create, Node, NodePolicy, ModelRegistry, Message, Headers

  # 1. Define nodes
  async def validate_input(msg: Message, ctx):
      # Process message, return result
      return validated_data

  validate_node = Node(validate_input, name="validate", policy=NodePolicy(timeout_s=10))

  # 2. Build flow graph
  flow = create(
      validate_node.to(process_node, enhance_node),  # validate → process, enhance
      process_node.to(emit_node),
      queue_maxsize=64,
      allow_cycles=False
  )

  # 3. Register models (optional but recommended)
  registry = ModelRegistry()
  registry.register("validate", InputModel, OutputModel)

  # 4. Start flow
  flow.run(registry=registry)

  # 5. Emit messages
  message = Message(
      payload={"data": "value"},
      headers=Headers(tenant="tenant1"),
      trace_id="unique-trace-123"
  )
  await flow.emit(message)

  # 6. Fetch results from Rookery
  result = await flow.fetch()

  # 7. Stop flow
  await flow.stop()

  ---
  2. Node Configuration

  2.1 NodePolicy Options

  policy = NodePolicy(
      validate="both",        # "both"|"in"|"out"|"none"
      timeout_s=30.0,        # Per-invocation timeout
      max_retries=2,         # Number of retries (0 = no retries)
      backoff_base=0.5,      # Initial backoff delay
      backoff_mult=2.0,      # Exponential multiplier
      max_backoff=10.0       # Cap on backoff delay
  )

  2.2 Node Signature Requirements

  Nodes MUST:
  - Be declared with async def
  - Accept exactly 2 positional parameters: (message, ctx)
  - Return a value (or None to skip emission)

  # ✅ Correct
  async def my_node(message, ctx):
      result = await process(message)
      await ctx.emit(result)
      return result

  # ❌ Wrong - not async
  def my_node(message, ctx):
      return process(message)

  # ❌ Wrong - wrong number of parameters
  async def my_node(message):
      return process(message)

  ---
  3. Context API

  The Context object provides message routing within nodes:

  3.1 Emitting Messages

  async def my_node(message, ctx):
      # Emit to all successors
      await ctx.emit(processed_data)

      # Emit to specific nodes
      await ctx.emit(data, to=specific_node)
      await ctx.emit(data, to=[node_a, node_b])

      # Non-blocking emit (use with caution)
      ctx.emit_nowait(data)

  3.2 Streaming Chunks

  async def streaming_node(message: Message, ctx):
      # Emit multiple chunks
      for chunk_text in generate_text():
          await ctx.emit_chunk(
              parent=message,
              text=chunk_text,
              stream_id=message.trace_id,  # Auto-uses trace_id if omitted
              done=False
          )

      # Final chunk
      await ctx.emit_chunk(
          parent=message,
          text="",
          done=True
      )

  3.3 Fetching from Incoming Queues

  async def my_node(message, ctx):
      # Fetch from any incoming edge
      incoming = await ctx.fetch()

      # Fetch from specific predecessor
      incoming = await ctx.fetch(from_=previous_node)

      # Race multiple incoming edges
      incoming = await ctx.fetch_any(from_=[node_a, node_b])

  ---
  4. Message Types

  4.1 Standard Message

  from penguiflow.types import Message, Headers

  msg = Message(
      payload={"key": "value"},       # Any Pydantic model or dict
      headers=Headers(tenant="org1", topic="sales"),
      trace_id="trace-123",           # Auto-generated if omitted
      deadline_s=time.time() + 60,    # Wall-clock deadline (optional)
      meta={"user_id": "12345"}       # Mutable side-channel data
  )

  4.2 Controller Loop Messages

  from penguiflow.types import WM, FinalAnswer

  # Working Memory - triggers controller loop
  wm = WM(
      query="What is the revenue?",
      facts=["fact1", "fact2"],
      hops=0,                 # Auto-incremented by runtime
      budget_hops=8,          # Max hops before auto-termination
      tokens_used=100,
      budget_tokens=1000,     # Max tokens before auto-termination
      confidence=0.85
  )

  # Final Answer - terminates controller loop
  final = FinalAnswer(
      text="The revenue is $1M",
      citations=["source1", "source2"]
  )

  Controller Loop Behavior:
  - When a node emits Message(payload=WM(...)), runtime auto-increments hops and re-routes to the same node
  - Loop terminates when:
    - Node emits FinalAnswer
    - budget_hops exceeded → auto-generates FinalAnswer("Hop budget exhausted")
    - budget_tokens exceeded → auto-generates FinalAnswer("Token budget exhausted")
    - deadline_s expired → auto-generates FinalAnswer("Deadline exceeded")

  ---
  5. Type Safety with ModelRegistry

  from penguiflow import ModelRegistry
  from pydantic import BaseModel

  class InputModel(BaseModel):
      query: str
      filters: dict

  class OutputModel(BaseModel):
      results: list[str]

  registry = ModelRegistry()
  registry.register("my_node", InputModel, OutputModel)

  # Runtime validates:
  # - Input: message payload against InputModel
  # - Output: node return value against OutputModel
  # - Controlled by NodePolicy.validate ("both", "in", "out", "none")

  ---
  6. Patterns

  6.1 map_concurrent - Parallel Processing

  from penguiflow.patterns import map_concurrent

  async def my_node(message, ctx):
      items = message.payload["items"]

      async def process_item(item):
          return await expensive_operation(item)

      results = await map_concurrent(
          items,
          process_item,
          max_concurrency=4  # Semaphore-bounded parallelism
      )

      return {"results": results}

  6.2 predicate_router - Conditional Routing

  from penguiflow.patterns import predicate_router

  def route_logic(message):
      priority = message.payload.get("priority")
      if priority == "high":
          return [high_priority_node]
      elif priority == "low":
          return [low_priority_node]
      return None  # Skip routing

  router = predicate_router("priority_router", route_logic)

  flow = create(
      ingress_node.to(router),
      router.to(high_priority_node, low_priority_node),
      # ...
  )

  6.3 union_router - Discriminated Union Routing

  from penguiflow.patterns import union_router
  from pydantic import BaseModel
  from typing import Literal

  class TaskA(BaseModel):
      kind: Literal["task_a"]
      data: str

  class TaskB(BaseModel):
      kind: Literal["task_b"]
      value: int

  UnionTask = TaskA | TaskB

  router = union_router("task_router", UnionTask)

  # Automatically routes TaskA to node named "task_a", TaskB to "task_b"
  flow = create(
      ingress.to(router),
      router.to(task_a_handler, task_b_handler),  # names must match discriminator
      # ...
  )

  6.4 join_k - Aggregation

  from penguiflow.patterns import join_k

  # Buffer 3 messages per trace_id, then emit aggregated batch
  aggregator = join_k("batch_aggregator", k=3)

  flow = create(
      fan_out_node.to(worker_1, worker_2, worker_3),
      worker_1.to(aggregator),
      worker_2.to(aggregator),
      worker_3.to(aggregator),
      aggregator.to(final_node)
  )

  ---
  7. Playbooks (Subflows)

  from penguiflow import call_playbook, create, ModelRegistry

  def create_subflow_playbook():
      """Factory returns (flow, registry) tuple."""
      sub_node_1 = Node(async_func_1, name="sub1")
      sub_node_2 = Node(async_func_2, name="sub2")

      subflow = create(sub_node_1.to(sub_node_2))

      registry = ModelRegistry()
      registry.register("sub1", InputModel, IntermediateModel)
      registry.register("sub2", IntermediateModel, OutputModel)

      return subflow, registry

  # Use in parent node
  async def parent_node(message: Message, ctx):
      result = await ctx.call_playbook(
          create_subflow_playbook,
          message,
          timeout=30.0
      )
      # Returns first payload emitted to subflow's Rookery
      return result

  Playbook Features:
  - Preserves parent's trace_id and headers
  - Mirrors cancellation from parent flow
  - Auto-stops subflow after first Rookery emission
  - Timeout applies to entire subflow execution

  ---
  8. Cancellation

  # Cancel specific trace
  await flow.cancel("trace-123")

  # Cancellation propagates:
  # - Drops pending messages in queues
  # - Cancels running node invocations
  # - Raises TraceCancelled inside affected nodes
  # - Mirrors to subflows via call_playbook

  Cancellation is idempotent - safe to call multiple times.

  ---
  9. Deadlines and Budgets

  9.1 Deadline Enforcement

  import time

  message = Message(
      payload=data,
      headers=Headers(tenant="org1"),
      deadline_s=time.time() + 60  # 60 seconds from now
  )

  # Runtime checks deadline BEFORE invoking each node
  # If expired: skips node, emits FinalAnswer("Deadline exceeded") to Rookery

  9.2 Controller Budgets

  wm = WM(
      query="question",
      budget_hops=10,      # Max controller loop iterations
      budget_tokens=5000   # Max token usage (manual tracking required)
  )

  # Runtime auto-increments hops and checks budgets
  # Exhaustion → auto-emits FinalAnswer with reason

  ---
  10. Error Handling

  10.1 FlowError - Traceable Exceptions

  from penguiflow.errors import FlowError, FlowErrorCode

  flow = create(
      # ...
      emit_errors_to_rookery=True  # Emit FlowError to Rookery on final failure
  )

  # When node exhausts retries, runtime creates FlowError with:
  # - trace_id
  # - node_name, node_id
  # - FlowErrorCode (NODE_TIMEOUT, NODE_EXCEPTION, etc.)
  # - Original exception
  # - Metadata (attempt count, latency, etc.)

  result = await flow.fetch()
  if isinstance(result, FlowError):
      print(f"Error in {result.node_name}: {result.message}")
      original_exc = result.unwrap()  # Get original exception

  10.2 Retry Behavior

  policy = NodePolicy(
      max_retries=3,       # Total attempts = max_retries + 1
      backoff_base=0.5,    # First retry after 0.5s
      backoff_mult=2.0,    # Exponential: 0.5s, 1.0s, 2.0s
      max_backoff=5.0      # Cap at 5s
  )

  # Retries cover:
  # - TimeoutError (from timeout_s)
  # - All Exception subclasses (not CancelledError)

  ---
  11. Observability

  11.1 Middleware Hook

  from penguiflow.metrics import FlowEvent

  async def my_middleware(event: FlowEvent):
      # Structured event with:
      # - event_type: "node_start", "node_success", "node_error", etc.
      # - node_name, node_id, trace_id
      # - latency_ms, attempt
      # - queue_depth_in, queue_depth_out
      # - trace_pending, trace_inflight, trace_cancelled

      if event.event_type == "node_error":
          logger.error(f"Node {event.node_name} failed", extra=event.to_payload())

  flow.add_middleware(my_middleware)

  11.2 Structured Logging

  All events are automatically logged via logging.getLogger("penguiflow.core") with JSON-compatible payloads.

  ---
  12. Testing

  12.1 FlowTestKit

  from penguiflow.testkit import run_one, assert_node_sequence, simulate_error

  # Run single trace end-to-end
  async def test_my_flow():
      flow = create(node_a.to(node_b).to(node_c))
      message = Message(payload=data, headers=Headers(tenant="test"))

      result = await run_one(flow, message, registry=registry, timeout_s=5.0)

      assert result.payload == expected_output
      assert_node_sequence(message.trace_id, ["node_a", "node_b", "node_c"])

  # Simulate failures for retry testing
  fail_func = simulate_error(
      "flaky_node",
      FlowErrorCode.NODE_EXCEPTION,
      fail_times=2,  # Fail first 2 attempts
      result="success"
  )
  flaky_node = Node(fail_func, name="flaky", policy=NodePolicy(max_retries=3))

  ---
  13. Advanced Features

  13.1 Routing Policies

  from penguiflow.policies import DictRoutingPolicy, RoutingRequest

  # Config-driven routing overrides
  policy = DictRoutingPolicy(
      mapping={"trace-123": "node_a", "trace-456": "node_b"},
      default="node_a"
  )

  router = predicate_router("my_router", predicate_fn, policy=policy)

  # Hot-swap routing config at runtime
  policy.update_mapping({"trace-789": "node_c"})

  13.2 Cycle Detection

  # Global allow cycles (controller loops everywhere)
  flow = create(*adjacencies, allow_cycles=True)

  # Per-node self-cycle (controller loop pattern)
  controller = Node(controller_fn, name="controller", allow_cycle=True)
  flow = create(controller.to(controller, final_node))

  ---
  14. Performance Tuning

  flow = create(
      *adjacencies,
      queue_maxsize=128,  # Increase for high throughput (default: 64)
      # 0 = unbounded queues (no backpressure)
  )

  # Backpressure behavior:
  # - When queue full, emit() blocks until space available
  # - Prevents memory exhaustion from fast producers

---

## 15. Visualization

### 15.1 Generate Mermaid Diagrams

```python
from penguiflow.viz import flow_to_mermaid

# Generate Mermaid diagram
mermaid_graph = flow_to_mermaid(flow, direction="TD")  # "TD", "LR", etc.
print(mermaid_graph)

# Highlights:
# - OpenSea/Rookery shown as ovals (ingress/egress)
# - Controller loops (allow_cycle=True) highlighted
# - Loop edges labeled as "loop"
```

### 15.2 Generate Graphviz DOT

```python
from penguiflow.viz import flow_to_dot

# Generate DOT diagram
dot_graph = flow_to_dot(flow, rankdir="TB")  # "TB", "LR", etc.
print(dot_graph)

# Use with: dot -Tpng flow.dot -o flow.png
```

---

## 16. Message.meta Usage Patterns

The `meta` dictionary is critical for dependency injection and side-channel data:

### 16.1 Resource Injection Pattern

```python
# At flow ingress
async def ingress_node(message: Message, ctx):
    # Inject dependencies into meta
    message.meta["resource_manager"] = resource_manager
    message.meta["db_session"] = db_session
    message.meta["embedding_service"] = embedding_service
    message.meta["user_id"] = "user-123"

    return message

# In downstream nodes - dependencies automatically propagate
async def enhancement_node(message: Message, ctx):
    resource_manager = message.meta["resource_manager"]
    lm = resource_manager.get_language_model()

    # Use resource
    result = await enhance_with_llm(message.payload, lm)
    return result
```

### 16.2 Meta Propagation Rules

- **Preserved across retries**: `meta` survives node failures and retries
- **Preserved in playbooks**: Subflows inherit parent's `meta`
- **Mutable**: Nodes can modify `meta` for downstream nodes
- **Cloned in streaming**: `emit_chunk` copies parent's `meta` to each chunk

### 16.3 Common Meta Patterns

```python
# Configuration injection
message.meta["dspy_signature"] = DimensionEnhancementSignature
message.meta["max_tokens"] = 1000
message.meta["temperature"] = 0.7

# Locks and synchronization
message.meta["faiss_lock"] = asyncio.Lock()

# Tracking and observability
message.meta["start_time"] = time.time()
message.meta["request_id"] = "req-123"

# Rollback artifacts
message.meta["backup_ids"] = []
message.meta["created_objects"] = []
```

---

## 17. Common Patterns for This Migration

### 17.1 Database Transaction Pattern

```python
# Stage changes across multiple nodes
async def persist_topic_version(message: Message, ctx):
    db_session = message.meta["db_session"]
    topic_version = create_topic_version(message.payload)
    db_session.add(topic_version)
    # DON'T commit yet - just stage

    # Track for rollback
    message.meta.setdefault("created_objects", []).append(topic_version)
    return topic_version

async def commit_node(message: Message, ctx):
    db_session = message.meta["db_session"]
    try:
        db_session.commit()
        return message
    except Exception:
        db_session.rollback()
        raise  # Triggers retry or FlowError

# Rollback handler (if emit_errors_to_rookery=True)
result = await flow.fetch()
if isinstance(result, FlowError):
    db_session = result.metadata.get("db_session")
    if db_session:
        db_session.rollback()
```

### 17.2 Generic Enhancement Node Pattern

```python
async def generic_enhancement_node(message: Message, ctx):
    """Reusable LLM enhancement node - signature configured via meta."""
    resource_manager = message.meta["resource_manager"]
    dspy_signature = message.meta["dspy_signature"]

    lm = resource_manager.get_language_model()
    with dspy.settings.context(lm=lm):
        predictor = dspy.Predict(dspy_signature)
        result = await predictor.acall(**message.payload)

    return process_enhancement_result(result)

# Different flows inject different signatures
dimension_msg.meta["dspy_signature"] = DimensionEnhancementSignature
measure_msg.meta["dspy_signature"] = MeasureEnhancementSignature
```

### 17.3 Concurrent Resource Protection Pattern

```python
# Shared lock at application level
faiss_lock = asyncio.Lock()

async def update_faiss_node(message: Message, ctx):
    lock = message.meta.get("faiss_lock", faiss_lock)

    async with lock:
        # Critical section - only one trace at a time
        backup_index(topic_id)
        try:
            update_vectors(embeddings)
            validate_consistency()
        except Exception:
            restore_from_backup()
            raise

    return message
```

### 17.4 Progress Tracking Pattern

```python
async def track_progress_middleware(event: FlowEvent):
    if event.event_type == "node_success":
        # Update job progress in database
        job_id = event.trace_id
        await update_job_progress(
            job_id,
            node_name=event.node_name,
            latency_ms=event.latency_ms
        )
```

---

## 18. Best Practices

### 18.1 Node Design Principles

1. **Single Responsibility**: Each node does one thing well
2. **Stateless**: No global state - use `message.meta` for context
3. **Idempotent**: Safe to retry (use transaction staging)
4. **Fail Fast**: Validate inputs early, let retries handle transient failures
5. **Return Values**: Always return something (or `None` to skip emission)

### 18.2 Error Handling Principles

1. **Retriable vs Fatal**: Let transient errors retry, raise fatal errors immediately
2. **Cleanup in Meta**: Track artifacts to clean up on failure
3. **Use FlowError**: Enable `emit_errors_to_rookery=True` for centralized error handling
4. **Structured Logging**: Use middleware to log all errors with context

### 18.3 Performance Principles

1. **Bounded Queues**: Use appropriate `queue_maxsize` for your workload
2. **Concurrency**: Use `map_concurrent` for parallel processing within nodes
3. **Timeouts**: Set realistic `timeout_s` per node based on expected latency
4. **Retries**: Limit `max_retries` to avoid long delays (use exponential backoff)

### 18.4 Testing Principles

1. **Use FlowTestKit**: `run_one` and `assert_node_sequence` for unit tests
2. **Mock Dependencies**: Inject mocks via `message.meta`
3. **Test Retries**: Use `simulate_error` to test retry behavior
4. **Test Cancellation**: Verify cleanup happens on `flow.cancel(trace_id)`

---

## 19. Migration-Specific Notes

### 19.1 From Legacy Orchestrators

**Key Differences**:
- **No global state**: Inject dependencies via `message.meta`
- **Explicit flow definition**: Use `create()` with adjacency list
- **Type safety**: Register Pydantic models in `ModelRegistry`
- **Built-in retries**: Configure via `NodePolicy` instead of custom logic

### 19.2 Worker Integration

```python
# Each worker runs independent flow instance
async def worker_loop():
    while True:
        job = await fetch_job()

        flow = create_topic_generation_flow()
        flow.run(registry=registry)

        message = Message(
            payload=job.request,
            headers=Headers(tenant=job.tenant),
            trace_id=job.job_id
        )
        message.meta["db_session"] = create_db_session()
        message.meta["resource_manager"] = resource_manager

        try:
            result = await flow.fetch()
            await save_result(job.job_id, result)
        except Exception as e:
            await mark_failed(job.job_id, str(e))
        finally:
            await flow.stop()
```

### 19.3 Feature Flag Integration

```python
# At API endpoint
if settings.TOPIC_PIPELINE_IMPL == "penguiflow":
    flow = create_penguiflow_topic_generation()
    flow.run(registry=registry)
    message = Message(payload=request, headers=Headers(tenant=tenant))
    result = await flow.fetch()
    await flow.stop()
else:
    # Legacy orchestrator
    result = await legacy_orchestrator.generate_topic(request)
```

---

## 20. Quick Reference

### 20.1 Essential Imports

```python
from penguiflow import create, Node, NodePolicy, ModelRegistry, call_playbook
from penguiflow.types import Message, Headers, StreamChunk, WM, FinalAnswer
from penguiflow.patterns import map_concurrent, predicate_router, union_router, join_k
from penguiflow.errors import FlowError, FlowErrorCode
from penguiflow.testkit import run_one, assert_node_sequence, simulate_error
```

### 20.2 Typical Flow Structure

```python
# 1. Define nodes
validate = Node(validate_fn, name="validate", policy=NodePolicy(timeout_s=10))
process = Node(process_fn, name="process", policy=NodePolicy(max_retries=2))
emit = Node(emit_fn, name="emit")

# 2. Build flow
flow = create(
    validate.to(process),
    process.to(emit),
    queue_maxsize=64,
    emit_errors_to_rookery=True
)

# 3. Register models
registry = ModelRegistry()
registry.register("validate", InputModel, ValidatedModel)
registry.register("process", ValidatedModel, ProcessedModel)
registry.register("emit", ProcessedModel, OutputModel)

# 4. Add middleware
flow.add_middleware(mlflow_middleware)

# 5. Run
flow.run(registry=registry)

# 6. Process
message = Message(payload=data, headers=Headers(tenant="org1"))
message.meta["db_session"] = db_session
await flow.emit(message)
result = await flow.fetch()

# 7. Cleanup
await flow.stop()
```

---

21. Distributed Hooks (State & Bus)

21.1 Opt-in adapters

PenguiFlow 2.1 introduces optional adapters so the core runtime can publish
trace data for distributed or remote execution scenarios without forcing a
specific backend:

```
from penguiflow import create, Node
from penguiflow.state import StateStore
from penguiflow.bus import MessageBus

flow = create(
    controller.to(worker),
    state_store=my_state_store,   # implements StateStore
    message_bus=my_message_bus,   # implements MessageBus
)
flow.run()
```

If either adapter is omitted the runtime behaves exactly as v2.0 did — all
queues remain in-process and no persistence happens. This keeps existing
deployments working without modification.

21.2 StateStore lifecycle

`StateStore` adapters receive a `StoredEvent` every time the runtime emits a
`FlowEvent`. Each object captures `trace_id`, timestamp, event kind,
`node_name`, and the structured payload used for logging. Events are written in
order of occurrence, so you can rebuild a trace history after the fact:

```
history = await flow.load_history(trace_id)
for event in history:
    print(event.kind, event.payload)
```

Failures when calling `save_event` are logged with the
`state_store_save_failed` event and **never** bubble up to user code. This makes
adapters safe to deploy even before their backends are fully hardened. The
protocol also includes `save_remote_binding` to persist correlations between a
trace and an external worker or agent.

21.3 MessageBus envelopes

When a `MessageBus` is configured every floe publish creates a `BusEnvelope`
containing:

* edge identifier (`source->target`),
* `trace_id`,
* original message payload,
* headers + meta for convenience.

Envelopes are published for both `emit` and `emit_nowait`, including OpenSea →
ingress and node → Rookery transitions. Consumers can subscribe to these events
to trigger remote workers or cross-process queues. Publish errors are logged as
`message_bus_publish_failed` but never stop the flow, so transient outages do
not impact the in-process execution path.

21.4 Testing helpers

`tests/test_distribution_hooks.py` contains concrete reference adapters that
record events/envelopes. Use them as a template when building new backends or
extending coverage in downstream repositories.

22. Remote transports & agent-to-agent calls

22.1 RemoteTransport protocol

Phase 2 introduces the `RemoteTransport` protocol. Implementations expose three
coroutines:

* `send(request)` — unary RPC returning a `RemoteCallResult` with the final
  payload plus optional `context_id`/`task_id`/`agent_url` metadata.
* `stream(request)` — async iterator yielding `RemoteStreamEvent` structures that
  contain partial text (`text`), completion flags (`done`), and optional
  `result` payloads.
* `cancel(agent_url=..., task_id=...)` — fires when PenguiFlow cancels a trace so
  the remote agent can unwind its own resources.

`RemoteCallRequest` packages the originating `Message`, the remote `skill`
identifier, the target `agent_url`, and any discovered agent card metadata. The
runtime copies the parent message's `meta` dictionary into the request so
transports can forward audit or billing annotations.

22.2 RemoteNode usage

`RemoteNode(...)` wraps a remote skill in a regular node. It requires a
`RemoteTransport`, the remote `skill` string, and the `agent_url`:

```
from penguiflow import Headers, Message, RemoteNode, create
from penguiflow.remote import RemoteTransport, RemoteCallResult

class HttpA2ATransport(RemoteTransport):
    ...  # implement send/stream/cancel over JSON-RPC + SSE

search = RemoteNode(
    transport=HttpA2ATransport(base_url="https://search-agent"),
    skill="SearchAgent.find",
    agent_url="https://search-agent",
    name="remote-search",
    streaming=True,
)

flow = create(search.to(), state_store=postgres_store)
flow.run()
await flow.emit(Message(payload={"query": "penguins"}, headers=Headers(tenant="acme")))
```

When a transport yields a `context_id`/`task_id`, the runtime persists the
binding via `StateStore.save_remote_binding`. This allows dashboards and future
resubscription logic to correlate PenguiFlow traces with remote agent tasks.

22.3 Streaming & cancellation handshake

While streaming, `RemoteNode` forwards each `RemoteStreamEvent.text` through
`Context.emit_chunk` so downstream nodes (and ultimately the Rookery) receive
partial results with the correct trace metadata. Final payloads returned via
`event.result` become the node's return value, propagating to successors as
usual.

PenguiFlow now exposes `PenguiFlow.ensure_trace_event(trace_id)` and
`PenguiFlow.register_external_task(trace_id, task)` so remote nodes can observe
per-trace cancellation. Once a binding is recorded the runtime creates a watcher
task that waits for the trace cancellation event and calls
`RemoteTransport.cancel`. If a trace is already cancelled when the binding is
discovered the runtime cancels immediately and raises `TraceCancelled` so the
worker unwinds without executing additional remote calls.

22.4 Reference tests

`tests/test_remote.py` contains in-memory transports that cover:

* Unary invocation (`RemoteTransport.send`) with binding persistence.
* Streaming invocations (`RemoteTransport.stream`) emitting `StreamChunk`
  payloads and returning a terminal result.
* Per-trace cancellation mirroring into `RemoteTransport.cancel` to guarantee
  remote tasks are cleaned up.

Use these test doubles as templates when adapting PenguiFlow to real A2A or
HTTP transports.

22.5 A2A server adapter

Sometimes PenguiFlow itself must behave as an A2A agent. The optional
`penguiflow_a2a` package provides a FastAPI adapter that projects any flow over
the standard `message/send`, `message/stream`, and `tasks/cancel` surface.

```bash
pip install "penguiflow[a2a-server]"
```

```python
from penguiflow import Message, Node, create
from penguiflow_a2a import (
    A2AAgentCard,
    A2AServerAdapter,
    A2ASkill,
    create_a2a_app,
)

async def orchestrate(message: Message, ctx):
    await ctx.emit_chunk(parent=message, text="thinking...", meta={"step": 0})
    return {"result": "ok"}

node = Node(orchestrate, name="main")
flow = create(node.to(), state_store=my_store)

card = A2AAgentCard(
    name="Main Agent",
    description="Primary orchestration entrypoint",
    version="2.1.0",
    skills=[A2ASkill(name="orchestrate", description="Primary entrypoint", mode="both")],
)

adapter = A2AServerAdapter(
    flow,
    agent_card=card,
    agent_url="https://main-agent.example",
)
app = create_a2a_app(adapter)
```

Key behaviors:

* **Startup/shutdown** — the FastAPI lifespan hooks call `flow.run(...)` and
  `flow.stop()` automatically. Provide a `state_store` when creating the flow to
  persist remote bindings for monitoring or resubscription.
* **Agent discovery** — `GET /agent` serves the declared `A2AAgentCard`, so
  upstream orchestrators can discover capabilities and skill metadata.
* **Unary execution** — `POST /message/send` accepts a JSON payload containing
  `payload`, `headers`, optional `meta`, and returns `{status, output, taskId}`.
  If the flow raises a `FlowError`, the response sets `status="failed"` with the
  structured error payload.
* **Streaming** — `POST /message/stream` returns an SSE stream. Chunks are
  formatted via `format_sse_event`, enriched with `taskId`/`contextId`, and a
  final `artifact` event carries the terminal payload. A trailing `done` event
  closes the stream.
* **Cancellation** — `POST /tasks/cancel` looks up the `taskId` and mirrors the
  request into `PenguiFlow.cancel(trace_id)`. The SSE stream emits a
  `TRACE_CANCELLED` error event if a trace is cancelled mid-flight.
* **Validation** — headers must at least include `tenant` to build a
  `penguiflow.types.Headers` object. Missing headers raise a 422 response. Tests
  live in `tests/test_a2a_server.py` and document the expected error payloads.

The adapter intentionally keeps FastAPI isolated in the optional extra so the
core package remains dependency-light. Bring your own middleware, auth, or CORS
configuration by extending the returned FastAPI app.
