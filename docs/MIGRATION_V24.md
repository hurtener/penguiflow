# Migrating to PenguiFlow v2.4

This guide captures the minimal changes needed to adopt v2.4's React Planner refinements. The release is backward compatible; no breaking changes are expected, but several behaviors are now **deprecated** and emit warnings.

## What Changed

- New `tool_context` argument on `ReactPlanner.run()`/`resume()` keeps non-serializable objects away from the LLM prompt.
- New `ToolContext` protocol gives tools typed access to `llm_context`, `tool_context`, `pause()`, and `emit_chunk()`.
- Explicit join injection for parallel plans via `join.inject` replaces the old magic field naming.
- Documentation and examples now model the JSON-only prompt contract and typed tools.

## Deprecations (remove before v2.6)

- `context_meta` parameter on `ReactPlanner.run()` — use `llm_context`.
- `_SerializableContext` wrapper — send serializable data via `llm_context` and keep callbacks in `tool_context`.
- `ctx.meta` direct access — prefer `ctx.tool_context` (tools), `ctx.llm_context` (LLM-visible data).
- Implicit/magic join field injection — use `join.inject` mapping.

## Quick Migration Steps

1) **Split context surfaces**

```python
# Before
combined = _SerializableContext({
    "status_publisher": publish_status,  # not JSON-safe
    "tenant_id": tenant,
    "memories": memories,                # intended for LLM
})
await planner.run(query, llm_context=combined)

# After
await planner.run(
    query,
    llm_context={"memories": memories},
    tool_context={
        "status_publisher": publish_status,
        "tenant_id": tenant,
    },
)
```

2) **Annotate tools with `ToolContext`**

```python
from penguiflow.planner import ToolContext

@tool(desc="Search docs")
async def search(args: SearchArgs, ctx: ToolContext) -> SearchOut:
    ctx.tool_context["status_publisher"](...)
```

3) **Make join injection explicit**

```python
# Before: relies on "results" magic field
{"plan": [...], "join": {"node": "merge_results"}}

# After: explicit mapping
{
    "plan": [...],
    "join": {
        "node": "merge_results",
        "inject": {"branch_outputs": "$results"},
        "args": {"note": "fanout complete"},
    },
}
```

4) **Pause/resume still works**

Pause semantics are unchanged. If you stored tool-only data in `context_meta`, move it to `tool_context`. Resume supports `tool_context` override for regenerated sinks/callbacks.

## Updated Examples

- `examples/planner_enterprise_agent_v2` — enterprise blueprint using `tool_context`.
- `examples/react_memory_context` — llm/tool context split with stub LLM.
- `examples/react_parallel_join` — explicit join injection for parallel plans.
- `examples/react_typed_tools` — end-to-end `ToolContext` usage with type hints.

## Validation Checklist

- No `context_meta` usage in your codebase.
- Tools accept `ToolContext`; IDE autocomplete works.
- Joins declare `inject` mappings; no deprecation warnings in logs.
- `llm_context` values are JSON-serializable (fail fast on invalid objects).

