# RFC: Unified Action Format for Penguiflow ReAct Planner

**Status**: Draft  
**Author**: Penguiflow Team  
**Created**: 2026-01-09  
**Target Version**: v2.0.0

---

## Executive Summary

This RFC proposes a simplified, unified action format for the Penguiflow ReAct planner that:

1. Reduces JSON schema complexity from 5 conditional fields to 2 fixed fields
2. Moves reasoning/thinking capture from in-JSON `thought` field to LiteLLM's native `reasoning_content`
3. Makes action types explicit via `next_node` value instead of null checks
4. Improves weak model compatibility by eliminating conditional field requirements
5. Maintains full streaming capability for final responses

---

## Table of Contents

1. [Motivation](#motivation)
2. [Current Architecture](#current-architecture)
3. [Proposed Architecture](#proposed-architecture)
4. [Schema Design](#schema-design)
5. [Streaming Protocol](#streaming-protocol)
6. [LiteLLM Integration](#litellm-integration)
7. [Migration Strategy](#migration-strategy)
8. [Implementation Plan](#implementation-plan)
9. [Appendix: Full Code Examples](#appendix-full-code-examples)

---

## Motivation

### Current Pain Points

1. **Complex Conditional Schema**: The current `PlannerAction` has 5 fields with complex interdependencies:
   - `thought` (always required)
   - `next_node` (null for terminal, string for tool call)
   - `args` (required when `next_node` is set OR when terminal with `raw_answer`)
   - `plan` (mutually exclusive with `next_node`)
   - `join` (only valid when `plan` is set)

2. **Weak Model Failures**: Models like GPT-OSS-120b,  and smaller Claude variants frequently emit:
   - `next_node: null` without `args.raw_answer`
   - Both `next_node` and `plan` set simultaneously
   - Missing `thought` field
   - Malformed JSON escaping in `thought` string

3. **Thought Quality**: Forcing reasoning into a JSON string field:
   - Requires careful escaping (newlines, quotes)
   - Limits reasoning length/quality
   - Wastes tokens on JSON structure overhead

4. **Implicit Action Types**: Determining action type requires checking multiple fields:
   ```python
   # Current: Complex detection logic
   if action.plan is not None:
       # Parallel execution
   elif action.next_node is None:
       # Terminal - check args.raw_answer
   else:
       # Tool call
   ```

### Goals

- **Simplicity**: One JSON shape for all action types
- **Explicitness**: Action type encoded directly in `next_node` value
- **Reliability**: Leverage LiteLLM's native reasoning extraction
- **Compatibility**: Maintain streaming, parallel execution, background tasks
- **Operational Robustness**: Reduce repair loops by tolerating legacy/hybrid outputs from weaker models (even after the prompt is updated)

---

## Current Architecture

### PlannerAction Model (Current)

```python
class PlannerAction(BaseModel):
    thought: str                              # Required - internal reasoning
    next_node: str | None = None              # null = terminal, str = tool name
    args: dict[str, Any] | None = None        # Tool args or {raw_answer: str}
    plan: list[ParallelCall] | None = None    # Parallel execution
    join: ParallelJoin | None = None          # Aggregation after parallel
```

### Example Current Outputs

**Tool Call:**
```json
{
  "thought": "Need to search for information",
  "next_node": "search_web",
  "args": {"query": "latest AI news"},
  "plan": null,
  "join": null
}
```

**Terminal (Streaming):**
```json
{
  "thought": "Have enough information to answer",
  "next_node": null,
  "args": {"raw_answer": "Based on my research..."},
  "plan": null,
  "join": null
}
```

**Parallel Execution:**
```json
{
  "thought": "Need multiple searches in parallel",
  "next_node": null,
  "args": null,
  "plan": [
    {"node": "search_a", "args": {"query": "topic A"}},
    {"node": "search_b", "args": {"query": "topic B"}}
  ],
  "join": {"node": "combine_results", "args": {}, "inject": null}
}
```

### Current Streaming Detection

The `_StreamingArgsExtractor` detects streaming by looking for:
```
"next_node": null → then find "args": {"raw_answer": "..." or "answer": "..."}
```

---

## Proposed Architecture

### Design Principles

1. **Always Two Fields**: Every action has exactly `next_node` and `args`
2. **Explicit Types**: `next_node` is always a non-null string indicating action type
3. **Native Reasoning**: Use LiteLLM `reasoning_content` instead of JSON `thought`
4. **Nested Complexity**: Complex structures (parallel, tasks) nest inside `args`

### New Action Types

| `next_node` Value | Description | Streams to User |
|-------------------|-------------|-----------------|
| `"<tool_name>"` | Call a specific tool | No |
| `"plan"` | Execute tools in parallel | No |
| `"task"` | Spawn background task | No |
| `"final_response"` | Terminal - answer user | **Yes** |

### PlannerAction Model (Proposed)

```python
class PlannerAction(BaseModel):
    """Unified action format - always exactly two fields."""
    
    next_node: str  # Never null - tool name or special type
    args: dict[str, Any] = Field(default_factory=dict)
    
    # Computed properties for type checking
    @property
    def is_tool_call(self) -> bool:
        return self.next_node not in SPECIAL_NODE_TYPES
    
    @property
    def is_parallel(self) -> bool:
        return self.next_node == "plan"
    
    @property
    def is_background_task(self) -> bool:
        return self.next_node == "task"
    
    @property
    def is_terminal(self) -> bool:
        return self.next_node == "final_response"

SPECIAL_NODE_TYPES = frozenset({"plan", "task", "final_response"})
```

---

## Schema Design

### Tool Call

```json
{"next_node": "search_web", "args": {"query": "latest AI news"}}
```

### Final Response (Streams to User)

```json
{"next_node": "final_response", "args": {"answer": "Based on my research..."}}
```

**Args Schema:**
```python
class FinalResponseArgs(TypedDict, total=False):
    answer: str           # The response text (streamed to user)
    artifacts: dict       # Heavy outputs collected during execution
    sources: list[dict]   # Citations
    suggested_actions: list[dict]  # Follow-up suggestions
    confidence: float     # Optional confidence score
```

### Parallel Execution

```json
{
  "next_node": "plan",
  "args": {
    "steps": [
      {"node": "search_a", "args": {"query": "topic A"}},
      {"node": "search_b", "args": {"query": "topic B"}}
    ],
    "join": {
      "node": "combine_results",
      "inject": {"results": "$all"}
    }
  }
}
```

**Args Schema:**
```python
class PlanStep(TypedDict):
    node: str
    args: dict[str, Any]

class PlanJoin(TypedDict, total=False):
    node: str | None      # Aggregation tool, or None for LLM combination
    args: dict[str, Any]
    inject: dict[str, str]  # Mapping of arg names to data sources

class PlanArgs(TypedDict):
    steps: list[PlanStep]
    join: NotRequired[PlanJoin]
```

### Background Task

```json
{
  "next_node": "task",
  "args": {
    "name": "Generate Monthly Report",
    "mode": "subagent",
    "query": "Generate the monthly report for 2024-12 as a PDF and summarize key findings",
    "merge_strategy": "HUMAN_GATED"
  }
}
```

**Args Schema:**
```python
class TaskArgs(TypedDict, total=False):
    name: str
    mode: Literal["subagent", "job"]

    # subagent mode (recommended): a background agent runs the query and may call tools internally.
    query: str

    # job mode (single-tool execution): spawn a tool job in the background.
    tool: str
    tool_args: dict[str, Any]

    # merge behavior for results
    merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]

    # --- Task Groups (optional) ---
    group: str                 # Display name to create/join a group in this turn
    group_id: str              # Stable ID (if known) to join an existing group
    group_sealed: bool         # If True, seal group after spawning this task
    group_report: Literal["all", "any", "none"]  # Reporting strategy for the group
    group_merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]
    retain_turn: bool          # If True, wait for group completion before yielding
```

### Task Groups (Semantics)

Task groups coordinate multiple background tasks so results can be reported and merged *together* instead of as fragmented per-task updates.
This maps directly to the existing `tasks.*` tool semantics already used by Penguiflow sessions.

**How grouping works**
- A `"task"` action spawns exactly one background task.
- Grouping is opt-in by including either `args.group` or `args.group_id`.
- If `group_id` is provided, the task joins that existing group.
- If only `group` (name) is provided, the runtime creates or joins an **OPEN** group with that name created earlier in the same foreground turn.
  - If no such OPEN group exists, a new one is created.
  - Name reuse across turns creates a new group (name is a label, not a global identifier).

**Sealing**
- If `group_sealed=true`, the group is sealed immediately after this task is added (no more tasks can join).
- If `group_sealed` is omitted/false, the group remains open until the foreground yields (auto-seal) or until sealed explicitly.

**Reporting**
- For grouped tasks, default `group_report="all"` (emit a single combined report when all tasks in the sealed group reach terminal state).
- For non-grouped tasks, default report behavior remains effectively `"any"` (notify per task).
- If `group_report="none"`, the agent must poll group status manually and decide when/how to surface results.

**Merging**
- `merge_strategy` controls how each task’s result is handled (HUMAN_GATED/APPEND/REPLACE).
- `group_merge_strategy` (if set) controls how the *group’s combined result* merges; otherwise it inherits from `merge_strategy`.
- For HUMAN_GATED groups, individual task results are held until the group completes; approval happens as a single batch (group-level apply/reject).

**Retain turn**
- If `retain_turn=true`, the foreground agent waits for group completion (or timeout) before continuing.
- Use this only when the user expects a single coherent answer that depends on the group’s results; otherwise yield and let tasks complete asynchronously.

**Examples**

Spawn 3 research tasks and seal on the last one:
```json
{"next_node":"task","args":{"name":"Sales analysis","mode":"subagent","query":"Analyze Q4 sales drivers","group":"q4_analysis","merge_strategy":"HUMAN_GATED"}}
{"next_node":"task","args":{"name":"Marketing analysis","mode":"subagent","query":"Analyze Q4 marketing performance","group":"q4_analysis","merge_strategy":"HUMAN_GATED"}}
{"next_node":"task","args":{"name":"Ops analysis","mode":"subagent","query":"Analyze Q4 operations efficiency","group":"q4_analysis","group_sealed":true,"merge_strategy":"HUMAN_GATED"}}
```

Wait for group completion before answering:
```json
{"next_node":"task","args":{"name":"Deep report","mode":"subagent","query":"Run full investigation and return a single synthesis","group":"investigation","group_sealed":true,"retain_turn":true,"merge_strategy":"APPEND","group_report":"all"}}
```

---

## Streaming Protocol

### Overview

Streaming happens when `next_node == "final_response"`. The `args.answer` field is streamed character-by-character to the frontend as the LLM generates it.

### Updated Streaming Extractor

```python
class _StreamingArgsExtractor(_JsonStringBufferExtractor):
    """
    Extracts 'args.answer' content when next_node is "final_response".
    
    Detection sequence:
    1. Find "next_node": "final_response"
    2. Find "args":
    3. Find opening brace {
    4. Find "answer":
    5. Find opening quote "
    6. Stream content character-by-character
    """

    __slots__ = (
        "_is_finish_action",
        "_next_node_seen",
        "_is_non_terminal",
        "_in_args_string",
        "_emitted_count",
        "_found_args_key",
        "_found_args_brace",
        "_found_answer_key",
    )

    # Pattern to detect next_node value
    _NEXT_NODE_PATTERN = re.compile(r'"next_node"\s*:\s*"([^"]*)"')
    
    # Terminal node type
    _TERMINAL_NODE = "final_response"

    def __init__(self) -> None:
        super().__init__()
        self._is_finish_action = False
        self._next_node_seen = False
        self._is_non_terminal = False
        self._in_args_string = False
        self._emitted_count = 0
        self._found_args_key = False
        self._found_args_brace = False
        self._found_answer_key = False

    @property
    def is_finish_action(self) -> bool:
        return self._is_finish_action

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    def feed(self, chunk: str) -> list[str]:
        """Feed a chunk of streaming JSON, return list of answer content to emit."""
        self._buffer += chunk
        emits: list[str] = []

        # Stage 0: Detect next_node value
        if not self._next_node_seen:
            match = self._NEXT_NODE_PATTERN.search(self._buffer)
            if match:
                self._next_node_seen = True
                node_value = match.group(1)
                if node_value == self._TERMINAL_NODE:
                    self._is_finish_action = True
                else:
                    self._is_non_terminal = True

        # Only proceed with streaming if this is a terminal action
        if self._is_non_terminal:
            return emits

        # Stage 1: Look for "args":
        if not self._found_args_key:
            args_match = re.search(r'"args"\s*:', self._buffer)
            if args_match:
                self._found_args_key = True
                self._buffer = self._buffer[args_match.end():]

        # Stage 2: Look for { after "args":
        if self._found_args_key and not self._found_args_brace:
            stripped = self._buffer.lstrip()
            if stripped.startswith("{"):
                self._found_args_brace = True
                self._buffer = stripped[1:]

        # Stage 3: Look for "answer":
        if self._found_args_brace and not self._found_answer_key:
            answer_match = re.search(r'"answer"\s*:', self._buffer)
            if answer_match:
                self._found_answer_key = True
                self._buffer = self._buffer[answer_match.end():]

        # Stage 4: Look for opening quote
        if self._found_answer_key and not self._in_args_string:
            stripped = self._buffer.lstrip()
            if stripped.startswith('"'):
                self._in_args_string = True
                self._buffer = stripped[1:]

        # Stage 5: Extract string content
        if self._in_args_string:
            extracted = self._extract_string_content("_in_args_string")
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits
```

### Streaming Event Protocol

Events emitted during streaming remain compatible with existing frontend:

```python
# During LLM generation of final_response
PlannerEvent(
    event_type="llm_stream_chunk",
    ts=timestamp,
    trajectory_step=step_num,
    extra={
        "text": "chunk of answer text",
        "done": False,
        "phase": "args",
        "channel": "answer",
        "action_seq": seq_num,
    }
)

# When streaming completes
PlannerEvent(
    event_type="llm_stream_chunk",
    ts=timestamp,
    trajectory_step=step_num,
    extra={
        "text": "",
        "done": True,
        "phase": "args",
        "channel": "answer",
        "action_seq": seq_num,
    }
)
```

### Streaming Decision Tree

```
LLM generates JSON
         │
         ▼
┌─────────────────────────────┐
│ Detect next_node value      │
└─────────────────────────────┘
         │
         ├── "final_response" ──▶ Stream args.answer to user
         │
         ├── "plan" ──▶ Execute parallel, no streaming
         │
         ├── "task" ──▶ Spawn background, no streaming
         │
         └── "<tool_name>" ──▶ Execute tool, no streaming
```

---

## LiteLLM Integration

### Reasoning Content Extraction

Instead of parsing `thought` from JSON, extract it from LiteLLM's native reasoning:

```python
from litellm import completion

async def get_planner_action(
    self,
    messages: list[dict],
) -> tuple[PlannerAction, str | None]:
    """
    Get next action from LLM with reasoning extracted separately.
    
    Returns:
        (action, reasoning) tuple where reasoning comes from native 
        LLM reasoning_content, not from JSON.
    """
    response = await completion(
        model=self.model,
        messages=messages,
        response_format={"type": "json_object"},
        reasoning_effort="low",  # or "medium", "high" for complex tasks
        stream=self._streaming_enabled,
        # ... other params
    )
    
    # Extract reasoning from native field (not JSON)
    reasoning = None
    if hasattr(response.choices[0].message, 'reasoning_content'):
        reasoning = response.choices[0].message.reasoning_content
    
    # Parse action JSON (now simpler, no thought field)
    action_json = response.choices[0].message.content
    action = PlannerAction.model_validate_json(action_json)
    
    return action, reasoning
```

### Supported Models for Reasoning Content

From LiteLLM docs, these providers support `reasoning_content`:

| Provider | Models | Setup |
|----------|--------|-------|
| Anthropic | Claude 3.5+, Claude 3.7 | `reasoning_effort="low"` |
| Deepseek | deepseek-chat, deepseek-reasoner | Native support |
| OpenRouter | Various reasoning models | `reasoning_effort` param |
| XAI | Grok models | Native support |
| Google | Gemini 2.0+ | Native support |
| Mistral | Magistral models | Native support |
| Bedrock | Anthropic + Deepseek hosted | Via AWS |
| Vertex AI | Anthropic hosted | Via GCP |

### Fallback for Non-Reasoning Models

For models without native reasoning support:

```python
async def get_planner_action(self, messages: list[dict]) -> tuple[PlannerAction, str | None]:
    response = await completion(
        model=self.model,
        messages=messages,
        response_format={"type": "json_object"},
        reasoning_effort="low" if self._supports_reasoning() else None,
        drop_params=True,  # LiteLLM drops unsupported params
        stream=self._streaming_enabled,
    )
    
    # Try native reasoning first
    reasoning = getattr(response.choices[0].message, 'reasoning_content', None)
    
    # Fallback: check if model emitted thinking before JSON
    content = response.choices[0].message.content
    if not reasoning and "```json" in content:
        # Model might have written thinking before the JSON block
        parts = content.split("```json", 1)
        if len(parts) == 2:
            reasoning = parts[0].strip()
            content = "```json" + parts[1]
    
    action = self._parse_action(content)
    return action, reasoning

def _supports_reasoning(self) -> bool:
    """Check if current model supports reasoning_content."""
    import litellm
    return litellm.supports_reasoning(model=self.model)
```

### Prompt Caching Compatibility

LiteLLM prompt caching works with the new format. Add cache control for system prompts:

```python
messages = [
    {
        "role": "system",
        "content": system_prompt,
        "cache_control": {"type": "ephemeral"}  # Cache system prompt
    },
    # ... conversation messages
]

response = await completion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=messages,
    # Prompt caching is automatic for supported models
)
```

---

## Migration Strategy

This project is a library and is intended to ship only once the unified schema is fully implemented.
That means we do **not** need multi-release backward compatibility as a product requirement.

However, weaker models can still emit legacy or hybrid JSON shapes even when prompted otherwise.
To reduce validation/repair steps and preserve streaming behavior, we keep a small normalization layer that:
- Accepts legacy/unified/hybrid payloads (best-effort)
- Produces a single canonical internal `PlannerAction` shape

### Phase 1: Normalization Support (Implementation)

Add a normalizer that accepts both old and new formats:

```python
def normalize_action(raw: str | dict) -> PlannerAction:
    """
    Normalize action from either old or new format.
    
    Old format detection:
    - Has "thought" field
    - next_node can be null
    - plan/join at top level
    
    New format:
    - No "thought" field
    - next_node always string
    - Complex data nested in args
    """
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    
    # Detect old format
    if "thought" in data:
        return _migrate_old_format(data)
    
    # New format
    return PlannerAction.model_validate(data)


def _migrate_old_format(data: dict) -> PlannerAction:
    """Convert old format to new format."""
    
    # Case 1: Parallel plan
    if data.get("plan") is not None:
        return PlannerAction(
            next_node="plan",
            args={
                "steps": data["plan"],
                "join": data.get("join"),
            }
        )
    
    # Case 2: Terminal (old: next_node=null)
    if data.get("next_node") is None:
        raw_answer = None
        if isinstance(data.get("args"), dict):
            raw_answer = data["args"].get("raw_answer") or data["args"].get("answer")
        return PlannerAction(
            next_node="final_response",
            args={"answer": raw_answer or ""}
        )
    
    # Case 3: Tool call (unchanged structure)
    return PlannerAction(
        next_node=data["next_node"],
        args=data.get("args") or {}
    )
```

### Phase 2: Prompt Migration (Week 2-3)

Update system prompts with feature flag:

```python
def build_system_prompt(
    *,
    use_unified_format: bool = False,
    # ... other params
) -> str:
    if use_unified_format:
        return NEW_FORMAT_PROMPT
    else:
        return LEGACY_FORMAT_PROMPT
```

### Phase 3: Streaming Extractor Update (Week 2)

Deploy updated `_StreamingArgsExtractor` that handles both:

```python
class _StreamingArgsExtractor(_JsonStringBufferExtractor):
    """Handles both old (next_node: null) and new (next_node: "final_response") formats."""
    
    # Updated pattern accepts both null and "final_response"
    _TERMINAL_PATTERN = re.compile(
        r'"next_node"\s*:\s*(null|"final_response")'
    )
    
    def feed(self, chunk: str) -> list[str]:
        # ... detection logic handles both patterns
```

### Phase 4: Deprecation (Week 4+)

If we keep the normalization layer as a robustness feature (not a compatibility promise), there is no required deprecation timeline.
If we decide to remove it later, do so as a deliberate simplification step after real-world model telemetry shows it is unnecessary.

### Configuration Flag

```python
class PlannerConfig(BaseModel):
    # Migration flags
    action_format: Literal["legacy", "unified", "auto"] = "auto"
    use_native_reasoning: bool = True
    
    # "auto" = accept legacy/unified inputs, emit unified internal actions
    # "legacy" = legacy-only prompting/parsing (developer-only escape hatch)
    # "unified" = unified-only prompting/parsing (strict mode)
```

---

## Implementation Plan

### Files to Modify

| File | Changes |
|------|---------|
| `models.py` | New `PlannerAction` model, remove `thought` field |
| `streaming.py` | Update `_StreamingArgsExtractor` for new terminal detection |
| `prompts.py` | New output format instructions |
| `react_step.py` | Use `normalize_action()`, extract reasoning from LiteLLM |
| `validation_repair.py` | Update salvage logic for new format |
| `llm.py` | Add `reasoning_effort` param, handle `reasoning_content` |
| `react_runtime.py` | Update action type detection |
| `payload_builders.py` | Update `_fallback_answer` for new args structure |

### New Files

| File | Purpose |
|------|---------|
| `migration.py` | Format normalization and conversion utilities |
| `action_types.py` | Type definitions for args schemas |

### Testing Strategy

1. **Unit Tests**: 
   - Parse both old and new formats
   - Streaming extraction for both terminal formats
   - Reasoning extraction with/without native support

2. **Integration Tests**:
   - End-to-end with unified format
   - Streaming to frontend
   - Parallel execution
   - Background tasks

3. **Model Compatibility Tests**:
   - Test with GPT-3.5 (weak model baseline)
   - Test with Claude 3.5 (reasoning_content)
   - Test with Deepseek (native reasoning)
   - Test with Llama-70B (no native reasoning)

---

## Appendix: Full Code Examples

### A. New `models.py`

```python
"""Unified planner models with simplified action format."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict, NotRequired

from pydantic import BaseModel, Field, computed_field

# Special node types that aren't tool names
SPECIAL_NODE_TYPES = frozenset({"plan", "task", "final_response"})


# --- Typed Args Schemas ---

class PlanStep(TypedDict):
    """Single step in a parallel plan."""
    node: str
    args: dict[str, Any]


class PlanJoin(TypedDict, total=False):
    """Aggregation config after parallel execution."""
    node: str | None
    args: dict[str, Any]
    inject: dict[str, str]


class PlanArgs(TypedDict):
    """Args schema for next_node="plan"."""
    steps: list[PlanStep]
    join: NotRequired[PlanJoin]


class TaskArgs(TypedDict, total=False):
    """Args schema for next_node="task"."""
    name: str
    mode: Literal["subagent", "job"]
    query: str
    tool: str
    tool_args: dict[str, Any]
    merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]
    group: str
    group_id: str
    group_sealed: bool
    group_report: Literal["all", "any", "none"]
    group_merge_strategy: Literal["HUMAN_GATED", "APPEND", "REPLACE"]
    retain_turn: bool


class FinalResponseArgs(TypedDict, total=False):
    """Args schema for next_node="final_response"."""
    answer: str
    artifacts: dict[str, Any]
    sources: list[dict[str, Any]]
    suggested_actions: list[dict[str, Any]]
    confidence: float
    language: str


# --- Main Action Model ---

class PlannerAction(BaseModel):
    """
    Unified action format for the ReAct planner.
    
    Every action has exactly two fields:
    - next_node: Action type (tool name or special type)
    - args: Action-specific arguments
    
    Special next_node values:
    - "final_response": Terminal action, args.answer streams to user
    - "plan": Parallel execution, args contains steps and join config
    - "task": Background task, args contains task configuration
    - Any other value: Tool call, args passed to the tool
    """
    
    next_node: str = Field(
        description="Tool name or special action type (plan, task, final_response)"
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific arguments"
    )
    
    @computed_field
    @property
    def is_tool_call(self) -> bool:
        """True if this is a regular tool call."""
        return self.next_node not in SPECIAL_NODE_TYPES
    
    @computed_field
    @property
    def is_parallel(self) -> bool:
        """True if this is a parallel execution plan."""
        return self.next_node == "plan"
    
    @computed_field
    @property
    def is_background_task(self) -> bool:
        """True if this is a background task spawn."""
        return self.next_node == "task"
    
    @computed_field
    @property
    def is_terminal(self) -> bool:
        """True if this is a terminal action (final response to user)."""
        return self.next_node == "final_response"
    
    def get_answer(self) -> str | None:
        """Extract answer text for terminal actions."""
        if not self.is_terminal:
            return None
        return self.args.get("answer")
    
    def get_plan_steps(self) -> list[PlanStep] | None:
        """Extract parallel plan steps."""
        if not self.is_parallel:
            return None
        return self.args.get("steps", [])
    
    def get_plan_join(self) -> PlanJoin | None:
        """Extract parallel plan join config."""
        if not self.is_parallel:
            return None
        return self.args.get("join")


# --- Reasoning Container ---

@dataclass
class ActionWithReasoning:
    """
    Container for planner action with associated reasoning.
    
    Reasoning comes from LiteLLM's native reasoning_content,
    not from a JSON field.
    """
    action: PlannerAction
    reasoning: str | None = None
    reasoning_tokens: int | None = None
    
    @classmethod
    def from_llm_response(
        cls,
        response: Any,  # LiteLLM response object
        action: PlannerAction,
    ) -> "ActionWithReasoning":
        """Create from LiteLLM response with reasoning extraction."""
        reasoning = None
        reasoning_tokens = None
        
        message = response.choices[0].message
        if hasattr(message, 'reasoning_content'):
            reasoning = message.reasoning_content
        
        # Some models return token counts for reasoning
        if hasattr(response, 'usage'):
            reasoning_tokens = getattr(response.usage, 'reasoning_tokens', None)
        
        return cls(
            action=action,
            reasoning=reasoning,
            reasoning_tokens=reasoning_tokens,
        )
```

### B. New `streaming.py` Extractor

```python
"""Updated streaming extractor for unified action format."""

import re
from typing import ClassVar


class _StreamingArgsExtractor(_JsonStringBufferExtractor):
    """
    Extracts answer content from streaming JSON for real-time display.
    
    Supports both formats during migration:
    - Legacy: "next_node": null with args.raw_answer
    - Unified: "next_node": "final_response" with args.answer
    
    Detection sequence:
    1. Find next_node value (null or "final_response")
    2. Find "args":
    3. Find opening brace {
    4. Find "answer": or "raw_answer":
    5. Find opening quote "
    6. Stream content character-by-character
    """

    __slots__ = (
        "_is_finish_action",
        "_next_node_seen",
        "_is_non_terminal",
        "_in_args_string",
        "_emitted_count",
        "_found_args_key",
        "_found_args_brace",
        "_found_answer_key",
    )

    # Pattern matches both null and "final_response"
    _NEXT_NODE_PATTERN: ClassVar[re.Pattern] = re.compile(
        r'"next_node"\s*:\s*(null|"([^"]*)")'
    )
    
    # Terminal indicators
    _TERMINAL_VALUES: ClassVar[frozenset[str]] = frozenset({
        "null",           # Legacy format
        "final_response", # Unified format
    })
    
    # Answer field names (legacy and unified)
    _ANSWER_KEY_PATTERN: ClassVar[re.Pattern] = re.compile(
        r'"(?:answer|raw_answer)"\s*:'
    )

    def __init__(self) -> None:
        super().__init__()
        self._is_finish_action = False
        self._next_node_seen = False
        self._is_non_terminal = False
        self._in_args_string = False
        self._emitted_count = 0
        self._found_args_key = False
        self._found_args_brace = False
        self._found_answer_key = False

    @property
    def is_finish_action(self) -> bool:
        return self._is_finish_action

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    def feed(self, chunk: str) -> list[str]:
        """Feed streaming JSON chunk, return answer content to emit."""
        self._buffer += chunk
        emits: list[str] = []

        # Stage 0: Detect next_node value
        if not self._next_node_seen:
            match = self._NEXT_NODE_PATTERN.search(self._buffer)
            if match:
                self._next_node_seen = True
                full_match = match.group(1)
                
                if full_match == "null":
                    # Legacy format: next_node: null
                    self._is_finish_action = True
                elif match.group(2):
                    # Quoted string value
                    node_value = match.group(2)
                    if node_value == "final_response":
                        self._is_finish_action = True
                    else:
                        self._is_non_terminal = True

        # Skip streaming for non-terminal actions
        if self._is_non_terminal:
            return emits

        # Stage 1: Look for "args":
        if not self._found_args_key:
            args_match = re.search(r'"args"\s*:', self._buffer)
            if args_match:
                self._found_args_key = True
                self._buffer = self._buffer[args_match.end():]

        # Stage 2: Look for { after "args":
        if self._found_args_key and not self._found_args_brace:
            stripped = self._buffer.lstrip()
            if stripped.startswith("{"):
                self._found_args_brace = True
                self._buffer = stripped[1:]

        # Stage 3: Look for "answer": or "raw_answer":
        if self._found_args_brace and not self._found_answer_key:
            answer_match = self._ANSWER_KEY_PATTERN.search(self._buffer)
            if answer_match:
                self._found_answer_key = True
                self._buffer = self._buffer[answer_match.end():]

        # Stage 4: Look for opening quote
        if self._found_answer_key and not self._in_args_string:
            stripped = self._buffer.lstrip()
            if stripped.startswith('"'):
                self._in_args_string = True
                self._buffer = stripped[1:]

        # Stage 5: Extract string content
        if self._in_args_string:
            extracted = self._extract_string_content("_in_args_string")
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits
```

### C. New System Prompt

```python
UNIFIED_FORMAT_PROMPT = '''

Respond with a single JSON object:

{"next_node": "", "args": {...}}

Where  is one of:

1. **Tool name** - Call a tool from the catalog
   {"next_node": "search_web", "args": {"query": "..."}}

2. **"plan"** - Execute multiple tools in parallel
   {"next_node": "plan", "args": {"steps": [{"node": "tool_a", "args": {...}}, ...], "join": {"node": "aggregator", "inject": {...}}}}

3. **"task"** - Spawn a background task
   {"next_node": "task", "args": {"name": "Task Name", "mode": "subagent", "query": "Do X in the background", "merge_strategy": "HUMAN_GATED"}}

4. **"final_response"** - Respond to the user (ONLY when you have the complete answer)
   {"next_node": "final_response", "args": {"answer": "Your response to the user..."}}

Rules:
- Always output valid JSON with exactly these two fields
- The "answer" field in final_response is shown directly to the user
- Do NOT use final_response until you have all information needed
- For parallel execution, list all steps in the "steps" array
- Background tasks are for long-running operations that don't block the conversation

Examples:

Single tool call:
{"next_node": "search_web", "args": {"query": "latest AI developments 2024"}}

Parallel execution:
{"next_node": "plan", "args": {"steps": [{"node": "search_news", "args": {"topic": "AI"}}, {"node": "search_papers", "args": {"topic": "AI"}}]}}

Final response:
{"next_node": "final_response", "args": {"answer": "Based on my research, here are the key findings..."}}

'''
```

### D. Migration Utility

```python
"""Format migration utilities for backward compatibility."""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import PlannerAction, SPECIAL_NODE_TYPES

logger = logging.getLogger("penguiflow.planner.migration")


def normalize_action(raw: str | dict[str, Any]) -> PlannerAction:
    """
    Normalize action from either legacy or unified format.
    
    Legacy format indicators:
    - Has "thought" field
    - next_node can be null
    - plan/join at top level
    
    Unified format:
    - No "thought" field  
    - next_node is always a string
    - Complex data nested in args
    
    Returns:
        PlannerAction in unified format
    """
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = dict(raw)
    
    # Detect legacy format by presence of "thought" field
    if "thought" in data:
        logger.debug("legacy_format_detected", extra={"keys": list(data.keys())})
        return _migrate_legacy_format(data)
    
    # Unified format - validate and return
    return PlannerAction.model_validate(data)


def _migrate_legacy_format(data: dict[str, Any]) -> PlannerAction:
    """
    Convert legacy format to unified format.
    
    Handles:
    1. Parallel plans (plan + join at top level)
    2. Terminal actions (next_node: null)
    3. Tool calls (next_node: string)
    """
    # Case 1: Parallel plan
    if data.get("plan") is not None:
        steps = data["plan"]
        join = data.get("join")
        
        args: dict[str, Any] = {"steps": steps}
        if join is not None:
            args["join"] = join
        
        return PlannerAction(next_node="plan", args=args)
    
    # Case 2: Terminal action (legacy: next_node = null)
    if data.get("next_node") is None:
        answer = _extract_legacy_answer(data.get("args"))
        return PlannerAction(
            next_node="final_response",
            args={"answer": answer} if answer else {}
        )
    
    # Case 3: Tool call
    return PlannerAction(
        next_node=data["next_node"],
        args=data.get("args") or {}
    )


def _extract_legacy_answer(args: Any) -> str | None:
    """Extract answer from legacy args format."""
    if not isinstance(args, dict):
        return None
    
    # Try various answer keys
    for key in ("raw_answer", "answer", "text", "response", "content"):
        if key in args and isinstance(args[key], str):
            return args[key]
    
    return None


def action_to_legacy_format(action: PlannerAction, thought: str = "") -> dict[str, Any]:
    """
    Convert unified action back to legacy format.
    
    Useful for:
    - Logging compatibility
    - Gradual rollback if needed
    """
    if action.is_parallel:
        return {
            "thought": thought,
            "next_node": None,
            "args": None,
            "plan": action.args.get("steps", []),
            "join": action.args.get("join"),
        }
    
    if action.is_terminal:
        return {
            "thought": thought,
            "next_node": None,
            "args": {"raw_answer": action.args.get("answer", "")},
            "plan": None,
            "join": None,
        }
    
    # Tool call or background task
    return {
        "thought": thought,
        "next_node": action.next_node,
        "args": action.args,
        "plan": None,
        "join": None,
    }
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-01-09 | Initial draft |

---

## Open Questions

1. **Streaming Empty Answers**: Should `{"next_node": "final_response", "args": {}}` (no answer key) trigger a follow-up LLM call to generate the response (Following or simplifying the current )

2. **Background Task Confirmation**: Should `task` actions require user confirmation before spawning? Current `BackgroundTasksConfig.spawn_requires_confirmation` would apply. I think, by default (if its not configured) model should seek for confirmation not only about spawning the task but also about keeping or releasing the turn. If its configured by the dev, follow the configuration.

3. **Plan Join Optional**: If `join` is omitted from parallel plans, should the system auto-aggregate results or require the LLM to combine them in a follow-up step? By current design, join is optional (and there can or cant be a join node). If there isnt, (or if llm put a wrong parameter) we shouldnt fail the call, but instead pass all the results back to the llm (because we also have an auto-compact security system in case there is a context overflown)

4. **Reasoning Token Budgets**: Should we expose `reasoning_effort` as a planner config option, or auto-select based on query complexity? We should expose it as configuration, with low, medium, high, and auto as options. If auto, we can prompt an llm to decide it as a router/triage, leaving the chance the developers to designate a smaller model for this task. It can be the same llm if no specialized llm is designed. As spin-off: lets think if its an opportunity to declare several main llms to be selected for the task, so the initial router (this functionality is opt-in) can select thinking effort level and model size/intelligence.

5. **Reasoning from litellm**: It should seamless replace our current though field so we dont break current functionality while simplifying the tasks of the agents increasing the reliability of the system.

---

## Amendments (Proposed)

These amendments are intended to preserve current behavior, reduce repair loops, and keep event types stable while moving to the unified action schema.

1. **Allow `answer` and `raw_answer` during migration**  
   For compatibility with existing tooling and streaming, treat `args.answer` as canonical in unified format but accept `args.raw_answer` as a synonym until deprecation.

2. **Streaming extractor should support both terminal indicators**  
   During migration, stream when either:
   - legacy: `"next_node": null` with `"raw_answer"` or `"answer"`  
   - unified: `"next_node": "final_response"` with `"answer"`  
   This keeps frontend behavior identical while the prompt format changes.

3. **Reasoning capture should be optional and non-blocking**  
   Use LiteLLM native `reasoning_content` when available, but do not require it. If it is missing, fall back to the existing JSON-only flow without retries.

4. **Planner action normalization is the single entrypoint**  
   All parsing should go through a normalization layer that accepts legacy or unified JSON and outputs a unified `PlannerAction`. This avoids distributed conditional checks and repair logic.

5. **Keep current event types and channels**  
   `llm_stream_chunk` with `phase=answer` and `phase=thinking` should remain unchanged; only the extractor logic shifts to unified format.

---

## References

- [LiteLLM Reasoning Content Docs](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM JSON Mode Docs](https://docs.litellm.ai/docs/completion/json_mode)
- [LiteLLM Prompt Caching Docs](https://docs.litellm.ai/docs/completion/prompt_caching)
- [Anthropic Thinking Blocks](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)

---

## Migration / Phase Plan (Implementation)

This plan assumes we ship the library only once the unified action schema is complete.
The normalization layer is included primarily for weak-model robustness (handling legacy/hybrid outputs), not as a public backward-compatibility promise.

### Phase 0: Pre-work (1-2 days)
- Add unit tests covering both formats (legacy + unified) for action parsing and streaming detection.
- Snapshot test current prompt output and planner event stream to ensure parity.

### Phase 1: Normalization Layer (2-3 days)
- Introduce a `normalize_action()` helper that accepts legacy or unified JSON and emits unified `PlannerAction`.
- Update `react_step.py` and `validation_repair.py` to parse via `normalize_action()` and reduce repeated schema repair steps.

### Phase 2: Unified Schema + Streaming (3-4 days)
- Update `models.py` for unified `PlannerAction` (two-field schema).
- Update `_StreamingArgsExtractor` to support both terminal indicators during migration.
- Keep `llm_stream_chunk` event types and channels unchanged.

### Phase 3: Prompts + Response Format (2-3 days)
- Add unified prompt instructions behind `action_format` flag.
- Update response schema generation to emit the unified schema when `action_format != legacy`.
- Ensure weak-model path stays in `json_object` mode to avoid stricter schema failures.

### Phase 4: LiteLLM Reasoning (2-3 days)
- Add `reasoning_effort` config and pass `drop_params=True` when unsupported.
- Capture `reasoning_content` if available and emit in the same `thinking` channel; do not require it.

### Phase 5: Deprecation + Cleanup (1-2 weeks)
- Default `action_format` to `auto` for release (emit unified prompts, accept legacy/hybrid outputs as salvage).
- Keep the normalization layer only if it measurably reduces repair loops; otherwise remove it to simplify.
- If retained, treat it as a best-effort salvage path (no deprecation timeline required).

### Developer Escape Hatch (Optional)
- Keep `action_format="legacy"` as a short-lived development toggle while iterating, but do not rely on it as a shipped compatibility guarantee.
