# Persistence and Hooks

## Why persist memory

Without persistence, memory lives in the planner instance. Restarts and worker swaps drop it. Acceptable for single-process demos; unacceptable for production services.

## `SupportsMemoryState` capability

`DefaultShortTermMemory` (the built-in implementation) persists via two methods on the `StateStore`:

```python
class SupportsMemoryState(Protocol):
    async def save_memory_state(self, key: str, state: dict) -> None: ...
    async def load_memory_state(self, key: str) -> dict | None: ...
```

When the planner has a `StateStore` and `short_term_memory` is configured:
- `run()` calls `load_memory_state(key)` to hydrate.
- Each turn triggers `save_memory_state(key, updated_state)`.
- With `rolling_summary`, the memory saves again from a background task once summarization finishes (possibly after `run()` returns).
- Resume calls re-hydrate.

Each save includes a `revision` string. Hydrate skips a stored state whose `revision` this instance last wrote or loaded; store and return the state dict as given.

The `key` argument is the composite from `MemoryKey` (`"{tenant_id}:{user_id}:{session_id}"`), but the contract allows arbitrary composite keys — see "Reserved keyspace" below.

## Reserved keyspace: `kv:v1:`

The same `save_memory_state` / `load_memory_state` methods back the durable tool KV facade `ctx.kv`. Tools use it for cross-call state under prefix `kv:v1:<scope>:<key>`.

Implications for `StateStore` authors:
- Don't assume `key` is always `tenant:user:session` — it can be any string.
- Don't apply tenant/user/session parsing logic inside the store.
- Treat the key as opaque; store and return state as-is.

For the durable backend choice, see [[penguiflow-statestore]].

## Backends

### In-memory (no persistence)
Default. Memory lives in the planner instance. Use for tests and prototypes.

### Redis
```python
class RedisMemoryStore:
    async def save_memory_state(self, key: str, state: dict) -> None:
        await self.redis.set(f"memory:{key}", json.dumps(state))

    async def load_memory_state(self, key: str) -> dict | None:
        raw = await self.redis.get(f"memory:{key}")
        return json.loads(raw) if raw else None
```

See `examples/memory_redis/flow.py` for a runnable pattern.

### Database
Same shape, different driver. Either:
- One row per `(key, state_json)` (simple).
- Normalized schema with separate tables for turns and summaries (richer queries, harder migrations).

Pick the simple JSON-column approach unless you need to query over memory contents.

### Custom
Implement the protocol directly:

```python
class MyStore:
    async def save_memory_state(self, key: str, state: dict) -> None:
        await self.svc.put(self._namespace(key), state)

    async def load_memory_state(self, key: str) -> dict | None:
        return await self.svc.get(self._namespace(key))
```

The store can implement other `StateStore` capabilities too (events, bindings, pause state). Just add the methods.

## Hook semantics

All three hooks are **fire-and-forget** background tasks:

```python
on_turn_added: Callable[[ConversationTurn], Awaitable[None]] | None
on_summary_updated: Callable[[str, str], Awaitable[None]] | None
on_health_changed: Callable[[MemoryHealth, MemoryHealth], Awaitable[None]] | None
```

### Execution model
- The planner schedules each hook via `asyncio.create_task(...)`.
- Hooks run concurrently.
- Exceptions are **swallowed** intentionally (memory can't fail the planner run).
- The planner does **not** await hook completion before continuing.

### What to put in hooks
- Lightweight metrics (`increment counter`, `record histogram`).
- Structured logging.
- Cheap audit events.

### What NOT to put in hooks
- Blocking I/O (synchronous DB writes, network calls without timeouts).
- Memory-critical state writes (use a `StateStore` instead).
- Long-running operations (>10ms is risky on a busy event loop).

### Example: metrics
```python
async def on_health_changed(old, new):
    statsd.increment("stm.health_transition", tags=[f"to:{new.value}", f"from:{old.value}"])

async def on_turn_added(turn):
    statsd.histogram("stm.turn.user_chars", len(turn.user_message))
    statsd.histogram("stm.turn.assistant_chars", len(turn.assistant_response))
```

### Example: structured logs
```python
async def on_summary_updated(old, new):
    logger.info("stm.summary_updated",
                extra={"old_chars": len(old or ""), "new_chars": len(new)})
```

### Example: cheap audit (don't await heavy)
```python
async def on_turn_added(turn):
    # cheap event push, non-blocking
    audit_queue.put_nowait({"ts": turn.ts, "kind": "turn_added"})
```

If the audit queue is bounded and full, `put_nowait` raises `QueueFull`. Since hook exceptions are swallowed, this is safe — but you lose the audit. Decide whether to upsize the queue or push to a different sink.

## Verifying persistence works

1. Start planner with `state_store=YourStore()` and a `MemoryKey`.
2. Run a `planner.run(...)` that asks the LLM to remember a fact ("My favorite color is teal.").
3. Restart the worker.
4. Run a new `planner.run(...)` with the **same** `MemoryKey` and ask the LLM to recall.

If the model recalls correctly, persistence works. If it doesn't:
- Check the store: `await store.load_memory_state("<your-key>")` should return a dict.
- Check the key: `MemoryKey("t1", "u1", "s1")` produces key `"t1:u1:s1"`.
- Check the strategy: `none` won't store anything.

## Multi-worker considerations

- **Shared store required.** Each worker must read/write from the same backend (Redis, DB).
- **Concurrency** — two workers can race on the same key; persistence is best-effort (no compare-and-swap). A background save after summarization is skipped if the stored `revision` changed (another worker saved newer state), but concurrent turn saves are last-write-wins.
- **Hot keys** — high-traffic sessions hit the same key repeatedly. Use a fast backend (Redis, in-memory cache layer in front of DB).
- **TTL** — set a TTL on memory entries (e.g., 24-72h). Stale sessions accumulate; clean them up.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Memory empty after restart | Store missing or `state_store` not passed to planner | Confirm `state_store` is configured; check `load_memory_state` returns non-null |
| Cross-tenant data in memory | Key collision (same `MemoryKey` for different tenants) | Always include tenant in `MemoryKey` |
| Hook errors silent | Exceptions are swallowed | Add logging inside hook body; assertions in tests |
| Slow `run()` start | `load_memory_state` is slow | Cache reads; use a faster backend; reduce stored state size |
| Memory bloat over weeks | No TTL | Add TTL on the storage backend |
