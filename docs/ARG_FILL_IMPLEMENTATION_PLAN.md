# Arg-Fill Feature Implementation Plan

## Problem Statement

Small/cheap LLMs correctly choose tools but fail to populate required args with valid values.
The current repair loop asks them to re-emit the full JSON action schema, which is exactly
what they struggle with. This leads to:
- Infinite loops until max_iters
- Forced finish with `requires_followup=True`
- Diagnostic payloads shown to users (poor UX)

## Solution: Arg-Fill Turn

When args are invalid/missing but tool choice is correct:
1. **Keep `next_node` fixed** - don't discard the correct tool selection
2. **Ask only for missing values** in a simpler format
3. **Parse and merge** into existing action
4. **Continue normally** with the tool call

## Implementation Details

### 1. New Prompt Template (`prompts.py`)

Add `render_arg_fill_prompt()` that generates a minimal prompt asking only for missing keys:

```python
def render_arg_fill_prompt(
    tool_name: str,
    missing_fields: list[str],
    field_descriptions: dict[str, str],
    user_query: str | None = None,
) -> str:
    """Generate a minimal prompt asking only for missing arg values."""
```

Format options:
- **Primary**: Simple JSON `{"field": "value"}`
- **Fallback**: Tagged format `<field>value</field>`

### 2. Arg-Fill Method (`react.py`)

Add `_attempt_arg_fill()` method:

```python
async def _attempt_arg_fill(
    self,
    trajectory: Trajectory,
    spec: NodeSpec,
    action: PlannerAction,
    missing_fields: list[str],
) -> dict[str, Any] | None:
    """
    Attempt to fill missing args with a simplified LLM call.

    Returns:
        Filled args dict if successful, None if arg-fill failed.
    """
```

Key behaviors:
- Uses **full conversation context** (model needs same info to fill args)
- Single LLM call with minimal output schema
- Parses JSON first, falls back to tagged extraction
- Returns None on failure (triggers user clarification path)

### 3. Integration Point (`react.py:2060-2130`)

Current flow:
```
arg_validation_error → check force_finish → repair_msg → continue loop
```

New flow:
```
arg_validation_error → check force_finish → check arg_fill_eligible
                       ↓ (eligible)           ↓ (not eligible)
                   _attempt_arg_fill()     repair_msg → continue
                       ↓
                   merge & re-validate
                       ↓ (success)   ↓ (fail)
                   continue          user_clarification / force_finish
```

### 4. Eligibility Criteria

Arg-fill triggers when:
1. `next_node` is valid (tool exists in catalog)
2. Missing fields are **required** (not optional)
3. Missing fields are **simple types** (string, number, boolean)
4. Not already attempted for this action (`arg_fill_attempted` flag)

### 5. Configuration Options

Add to `ReactPlanner.__init__`:
```python
arg_fill_enabled: bool = True        # Master switch
arg_fill_max_attempts: int = 1       # Usually 1 is enough
arg_fill_timeout_s: float = 30.0     # Separate timeout for arg-fill calls
```

### 6. Metrics & Events

New event types:
- `arg_fill_attempt`: When arg-fill is triggered
- `arg_fill_success`: When args were successfully filled
- `arg_fill_failure`: When arg-fill failed (triggers fallback)

Metadata keys:
- `arg_fill_attempts`: Count of arg-fill attempts this run
- `arg_fill_success_count`: Successful arg-fills
- `arg_fill_failure_count`: Failed arg-fills

### 7. Fallback Path

If arg-fill fails:
1. **First failure**: Generate user clarification prompt
   - "I'm trying to use [tool], but I need you to provide: [field1], [field2]"
2. **Forced finish with better UX**:
   - Clear message about what info is needed
   - Not a diagnostic dump

## File Changes Summary

| File | Changes |
|------|---------|
| `penguiflow/planner/prompts.py` | Add `render_arg_fill_prompt()`, `render_arg_fill_clarification()` |
| `penguiflow/planner/react.py` | Add `_attempt_arg_fill()`, `_parse_arg_fill_response()`, integration in main loop, new config params |
| `penguiflow/planner/models.py` | Add `ArgFillConfig` dataclass (optional) |

## Testing Strategy

1. **Unit tests**: `test_arg_fill.py`
   - Test prompt generation
   - Test response parsing (JSON and tagged)
   - Test merge logic
   - Test eligibility checks

2. **Integration tests**:
   - Mock LLM returning valid arg-fill response
   - Mock LLM returning invalid response (test fallback)
   - End-to-end with cheap model (e.g., Haiku)

## Rollout

1. Feature flag: `arg_fill_enabled=True` (default on)
2. Monitor success rate via events
3. If success rate < 50%, investigate or disable
