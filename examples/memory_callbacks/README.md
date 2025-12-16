# Memory Callbacks (Monitoring & Analytics)

This example demonstrates:
- `on_turn_added`, `on_summary_updated`, `on_health_changed`
- Rolling summary with a summarizer that fails once and then succeeds (to show health transitions)

Run it:

```bash
uv run python examples/memory_callbacks/flow.py
```

What to look for:
- `health` contains transitions (e.g., `retry` → `healthy`)
- `summaries` contains at least one summary update after the retry succeeds

Notes:
- Callbacks are best-effort; they are not intended for critical control flow.
