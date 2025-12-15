# Memory Truncation (Cost-Effective)

This example demonstrates the `"truncation"` memory strategy for `ReactPlanner`.

What it shows:
- Keeping only the last N turns (`budget.full_zone_turns`)
- Deterministic behavior with no background summarization
- A deterministic scripted LLM client so the example runs without network access

Run it:

```bash
uv run python examples/memory_truncation/flow.py
```

What to look for:
- The printed JSON output contains only the last 2 prior turns in `recent_turns` (because `full_zone_turns=2`).
