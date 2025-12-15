# Memory Persistence (StateStore Extension)

This example demonstrates persisting short-term memory across planner instances via optional `state_store` methods:

- `save_memory_state(key: str, state: dict) -> None`
- `load_memory_state(key: str) -> dict | None`

Run it:

```bash
uv run python examples/memory_persistence/flow.py
```

What to look for:
- The second planner instance sees the previous turn injected as `context.conversation_memory` because it hydrates from the shared store.

Notes:
- This does not modify PenguiFlow’s core `StateStore` protocol; the memory methods are optional and checked via `hasattr`.
