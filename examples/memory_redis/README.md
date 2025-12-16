# Memory “Redis” (Layer 3 Serialization)

This example demonstrates persisting short-term memory using `to_dict()` / `from_dict()` (serialization layer),
backed by a small in-memory “Redis-like” client (no external services required).

Run it:

```bash
uv run python examples/memory_redis/flow.py
```

What to look for:
- The second turn’s prompt context includes the first turn in `conversation_memory.recent_turns`.

Notes:
- Replace `FakeRedis` with `redis.asyncio.Redis` and swap `get/set` to integrate with a real Redis deployment.
