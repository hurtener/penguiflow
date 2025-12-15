# Memory Custom (Layer 4 Implementation)

This example demonstrates writing a custom `ShortTermMemory` implementation and passing it to `ReactPlanner`.

Use this approach when you need:
- A custom in-memory policy (eviction, formatting)
- A distributed/shared backend
- A custom injection shape for `llm_context`

Run it:

```bash
uv run python examples/memory_custom/flow.py
```

What to look for:
- The second turn’s prompt context includes `conversation_memory.recent_turns` from the custom memory object.

Notes:
- Even for custom memory objects, the recommended best practice is to pass an explicit `memory_key=` for isolation.
