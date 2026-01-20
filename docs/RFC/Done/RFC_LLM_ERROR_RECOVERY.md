# RFC: LLM Error Recovery

> **Status**: Implemented
> **Created**: 2026-01-06
> **Author**: PenguiFlow Team

## Summary

This RFC proposes a smart error recovery system for LLM-related errors in PenguiFlow's ReactPlanner. Instead of failing tasks when LLM errors occur, the system will attempt automatic recovery strategies based on error classification.

## Motivation

Currently, when the ReactPlanner encounters an LLM error (e.g., `BadRequestError`), the error propagates as a hard failure with raw, nested JSON error messages that are confusing to end users:

```
litellm.BadRequestError: DatabricksException - {"error_code":"BAD_REQUEST","message":"{"message":"Input is too long for requested model."}"}
```

**Problems:**
1. **No recovery attempt**: The system gives up immediately without trying to fix recoverable errors
2. **Poor UX**: Raw technical errors are displayed to users
3. **Wasted work**: Multi-step trajectories are lost when context grows too large

## Design

### Error Classification

Errors are classified into categories with different recovery strategies:

| Error Type | Detection Pattern | Recovery Strategy |
|------------|-------------------|-------------------|
| `CONTEXT_LENGTH_EXCEEDED` | "input is too long", "context length", "max_tokens" | Auto-compress trajectory, retry |
| `RATE_LIMIT` | Exception class name | Existing exponential backoff |
| `SERVICE_UNAVAILABLE` | Exception class name | Existing exponential backoff |
| `BAD_REQUEST_OTHER` | `BadRequest*` class, not context length | Let LLM apologize gracefully |
| `UNKNOWN` | Default | Re-raise original error |

### Recovery Strategies

#### Strategy 1: Auto-Compress for Context Length Exceeded

When the trajectory grows too large for the model's context window:

1. **Detect**: Classify error as `CONTEXT_LENGTH_EXCEEDED`
2. **Compress**: For each step in the trajectory with large `llm_observation`:
   - Call the summarizer model to create a condensed summary
   - Replace the raw observation with `{"_compressed": True, "summary": "..."}`
3. **Retry**: Execute the step again with the compressed trajectory
4. **Emit Event**: `trajectory_compressed` event for observability

```
┌─────────────────┐    Error    ┌─────────────────┐
│   LLM Call      │ ─────────> │  Classify Error │
└─────────────────┘            └────────┬────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
                    v                                       v
          ┌─────────────────┐                    ┌─────────────────┐
          │ CONTEXT_LENGTH  │                    │ BAD_REQUEST_    │
          │    EXCEEDED     │                    │     OTHER       │
          └────────┬────────┘                    └────────┬────────┘
                   │                                      │
                   v                                      v
          ┌─────────────────┐                    ┌─────────────────┐
          │   Compress      │                    │ Inject Error    │
          │   Trajectory    │                    │ as Observation  │
          └────────┬────────┘                    └────────┬────────┘
                   │                                      │
                   v                                      v
          ┌─────────────────┐                    ┌─────────────────┐
          │   Retry LLM     │                    │ LLM Apologizes  │
          │     Call        │                    │   Gracefully    │
          └─────────────────┘                    └─────────────────┘
```

#### Strategy 2: Graceful Failure for Other BadRequest Errors

When an unknown `BadRequestError` occurs:

1. **Detect**: Classify error as `BAD_REQUEST_OTHER`
2. **Extract**: Parse the error message to get a clean, user-readable version
3. **Inject**: Return a synthetic `PlannerAction` with the error as context
4. **Respond**: Let the LLM generate an appropriate apology/explanation

This ensures users get a helpful response instead of a cryptic error.

### Compression Algorithm

The compression targets `llm_observation` fields in trajectory steps (not the full `observation` which is kept for record-keeping):

```python
async def compress_trajectory(planner, trajectory) -> Trajectory:
    compressed = trajectory.model_copy(deep=True)

    for step in compressed.steps:
        if step.llm_observation and _is_large(step.llm_observation):
            summary = await summarise_single_observation(
                planner._summarizer_client,
                step.action.next_node,
                step.llm_observation,
            )
            step.llm_observation = {"_compressed": True, "summary": summary}

    return compressed
```

**Compression Threshold**: Observations larger than 2000 characters (configurable) are candidates for compression.

**Summarizer Model**: Uses the existing `_summarizer_client` if configured, otherwise falls back to the main planner client.

### Configuration

```python
@dataclass
class ErrorRecoveryConfig:
    """Configuration for LLM error recovery."""

    enabled: bool = True
    max_compress_retries: int = 1
    compression_threshold_chars: int = 2000
    summarize_on_compress: bool = True
```

### Observability

New events emitted during recovery:

| Event | Description |
|-------|-------------|
| `trajectory_compressed` | Emitted when trajectory is compressed before retry |
| `error_recovery_attempt` | Emitted when recovery is attempted |
| `error_recovery_success` | Emitted when recovery succeeds |
| `error_recovery_failed` | Emitted when recovery fails |

Example event payload:
```json
{
  "event_type": "trajectory_compressed",
  "extra": {
    "attempt": 1,
    "reason": "context_length",
    "steps_compressed": 3,
    "original_size_chars": 45000,
    "compressed_size_chars": 12000
  }
}
```

## Implementation

### New Files

1. **`penguiflow/planner/error_recovery.py`**: Central recovery logic
2. **`penguiflow/planner/compress.py`**: Trajectory compression utilities

### Modified Files

1. **`penguiflow/planner/llm.py`**: Add `LLMErrorType` enum and `classify_llm_error()`
2. **`penguiflow/planner/react_step.py`**: Add `step_with_recovery()` wrapper
3. **`penguiflow/planner/react_runtime.py`**: Use `step_with_recovery()` in main loop

### API Changes

The recovery system is internal and does not change the public API. Existing code continues to work unchanged.

## Future Optimizations

As we encounter specific error patterns, we can add targeted recovery strategies:

1. **Invalid JSON errors**: Attempt JSON repair before retry
2. **Tool validation errors**: Suggest alternative tool parameters
3. **Authentication errors**: Trigger re-authentication flow
4. **Model deprecation**: Fallback to alternative model

## Alternatives Considered

1. **Proactive compression**: Compress trajectory before errors occur
   - Rejected: Adds latency to normal operations; prefer lazy compression

2. **User-initiated retry**: Show error and let user click "retry with compression"
   - Rejected: Poor UX; automatic recovery is better for most cases

3. **Truncation instead of summarization**: Just drop old trajectory steps
   - Rejected: Loses important context; summarization preserves key information

## References

- Existing trajectory summarization: `penguiflow/planner/llm.py:summarise_trajectory()`
- LiteLLM error handling: [LiteLLM Docs](https://docs.litellm.ai/docs/exception_mapping)
