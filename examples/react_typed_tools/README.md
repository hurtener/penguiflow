# React Planner — Typed Tools Demo

Highlights:

- `ToolContext` everywhere (no `ctx: Any`)
- Clean split between `llm_context` (preferences) and `tool_context` (callbacks, stores)
- Status callback injected via `tool_context`
- Scripted planner actions keep the run deterministic

## Run

```bash
uv run python examples/react_typed_tools/main.py
```

Expected: triage → fetch_profile → respond, with status logs printed and final payload built from the typed models.
