# Memory Context Example

This example demonstrates how to inject custom memory and context structures into the ReactPlanner.

## Key Concepts

### Separation of Concerns

- **Library responsibility**: Inject context via the user prompt (baseline behavior)
- **Developer responsibility**: Define the format and interpretation rules

### Two Parameters

1. **`llm_context`** (in `planner.run()`): The actual memory/context data
   - Can be any structure: JSON object, flat text, nested hierarchies
   - Should NOT include internal metadata (tenant_id, trace_id, etc.)

2. **`system_prompt_extra`** (in `ReactPlanner()`): Instructions for interpreting the context
   - Optional: only needed if you want custom interpretation semantics
   - Documents the format and how the planner should use it

## Examples in this Demo

### Example 1: JSON-structured preferences
```python
system_prompt_extra = (
    "The context.user_prefs contains a JSON object with user preferences. "
    "When selecting tools and arguments, prioritize these preferences."
)

llm_context = {
    "user_prefs": {"preferred_language": "Python", "experience": "beginner"}
}
```

### Example 2: Free-form knowledge
```python
system_prompt_extra = (
    "The context.previous_failures lists approaches that failed. "
    "Avoid repeating the same tool calls or arguments."
)

llm_context = {
    "previous_failures": [
        "search with 'Python web' returned no results"
    ]
}
```

### Example 3: Hierarchical structure
```python
system_prompt_extra = (
    "The context.memory has nested structure:\n"
    "- user_profile: Long-term preferences\n"
    "- session_history: Recent interactions\n"
    "- task_context: Current task info\n"
    "Prioritize more specific context (task > session > profile)."
)

llm_context = {
    "memory": {
        "user_profile": {"skill_level": "intermediate"},
        "session_history": ["asked about Python"],
        "task_context": {"deadline": "3 months"}
    }
}
```

## Running the Example

```bash
uv run python examples/react_memory_context/main.py
```

## Design Rationale

This design is **flexible** because:
- Developers can use any format (JSON, text, mixed)
- No assumptions about schema or structure
- Easy to evolve without library changes

It's **composable** because:
- Baseline behavior is always the same (inject via user prompt)
- Custom semantics layer on top via system_prompt_extra
- Can combine with other features (planning hints, tool policies, etc.)

## When to Use system_prompt_extra

Only use it when you need **format-specific interpretation rules** beyond the baseline. Examples:

- **Need it**: "Prioritize user_prefs over defaults"
- **Need it**: "Avoid tools listed in previous_failures"
- **Don't need it**: Memories are self-explanatory from structure
- **Don't need it**: Context is simple key-value pairs
