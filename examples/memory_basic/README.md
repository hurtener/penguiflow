# Memory Basic (Rolling Summary)

This example demonstrates the built-in short-term memory integration for `ReactPlanner` using the `"rolling_summary"` strategy.

What it shows:
- Enabling memory via `short_term_memory=ShortTermMemoryConfig(...)`
- Passing an explicit `MemoryKey` to keep the session isolated
- How `conversation_memory` is injected into the **user prompt** via `llm_context`
- A deterministic scripted LLM client so the example runs without network access

Run it:

```bash
uv run python examples/memory_basic/flow.py
```

What to look for:
- The printed JSON output contains `conversation_memory.recent_turns` with the previous user/assistant exchange from turn 1.

Notes:
- The example uses a tiny tool (`echo`) so you can see that memory can carry tool context (via trajectory digests) when enabled.
