# RFC: Short-Term Memory for ReactPlanner

**Status:** Draft
**Author:** Santiago Benvenuto + Claude
**Created:** 2025-12-14
**Target Version:** v2.7+

---

## Summary

This RFC proposes an opt-in short-term memory system for ReactPlanner that:
1. Maintains conversation context across turns within a single session
2. Supports multiple memory strategies (truncation, rolling summary, none)
3. Provides session isolation (tenant/user/session) to prevent context leakage
4. Integrates with existing StateStore for persistence
5. Handles summarization failures gracefully without blocking agent responses

---

## Motivation

### Current State

ReactPlanner currently operates in a stateless manner per invocation. While the existing `Trajectory` tracks tool calls within a single planning loop, there's no built-in mechanism to:

- Remember previous user interactions in a conversation
- Maintain context across multiple `run()` calls
- Summarize older context to fit within token budgets

The iceberg memory server templates (`templates/new/*/memory.py.jinja`) provide a company-specific integration point, but downstream teams need a generic, opt-in solution that works out of the box.

### LangGraph Comparison

LangGraph manages short-term memory via thread-scoped checkpoints. Their documentation identifies key challenges:

> "A full history may not fit inside an LLM's context window, resulting in an irrecoverable error. Even if your LLM supports the full context length, most LLMs still perform poorly over long contexts."

Penguiflow needs a similar capability while maintaining its core design principles:
- **Protocol-based extensibility** (duck-typed interfaces)
- **Opt-in behavior** (nothing breaks if you don't enable it)
- **Async-first** (non-blocking operations)
- **Separation of concerns** (LLM context vs tool context)

### Goals

- **Simplicity**: Easy opt-in with sensible defaults
- **Flexibility**: Multiple strategies for different use cases
- **Resilience**: Graceful degradation on failures
- **Isolation**: Multi-tenant safety by design
- **Efficiency**: Background summarization without latency impact
- **Extensibility**: Hooks for custom persistence and callbacks

---

## Design

### 1. Core Data Structures

#### ConversationTurn (Turn Pair Granularity)

A turn represents a complete user-assistant exchange, including compressed trajectory data:

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class TrajectoryDigest:
    """Compressed trajectory for memory persistence.

    Preserves tool usage history without full observation payloads.
    """
    tools_invoked: list[str]
    """Tool names called during this turn, e.g., ["search_docs", "calculate"]"""

    observations_summary: str
    """Prose summary of ALL successful tool outputs"""

    reasoning_summary: str | None = None
    """One-liner summary of the thought chain (optional)"""

    artifacts_refs: list[str] = field(default_factory=list)
    """References to hidden artifacts for later retrieval"""


@dataclass
class ConversationTurn:
    """Single user-assistant exchange.

    This is the atomic unit of short-term memory. Turn pairs provide
    natural conversation boundaries while trajectory_digest preserves
    tool usage context.
    """
    user_message: str
    """The user's input for this turn"""

    assistant_response: str
    """The final answer delivered to the user"""

    trajectory_digest: TrajectoryDigest | None = None
    """Compressed record of tools used and observations"""

    artifacts_shown: dict[str, Any] = field(default_factory=dict)
    """Artifacts visible in LLM context for future turns"""

    artifacts_hidden_refs: list[str] = field(default_factory=list)
    """References to artifacts hidden from LLM but retrievable"""

    ts: float = 0.0
    """Timestamp of turn completion (epoch seconds)"""
```

#### Memory Budget Configuration

```python
from dataclasses import dataclass
from typing import Literal, Callable

@dataclass
class MemoryBudget:
    """Token economy configuration for memory management.

    Controls how much context is preserved and what happens on overflow.
    """
    full_zone_turns: int = 5
    """Number of recent turns kept with complete messages"""

    summary_max_tokens: int = 1000
    """Maximum tokens for the compressed summary zone"""

    total_max_tokens: int = 10000
    """Overall budget for memory context (summary + full zone)"""

    overflow_policy: Literal["truncate_summary", "truncate_oldest", "error"] = "truncate_oldest"
    """
    How to handle budget overflow:
    - truncate_summary: Compress summary further (lossy)
    - truncate_oldest: Move oldest full turn to summary (triggers re-summarization)
    - error: Raise MemoryBudgetExceeded
    """
```

#### Session Isolation

```python
@dataclass
class MemoryIsolation:
    """Session isolation keys to prevent context leakage.

    CRITICAL: In multi-tenant deployments, memory MUST be isolated
    by tenant, user, and session to prevent cross-contamination.
    """
    tenant_key: str = "headers.tenant"
    """Path to extract tenant ID from Message/context"""

    user_key: str = "llm_context.user_id"
    """Path to extract user ID from context"""

    session_key: str = "trace_id"
    """Path to extract session ID (default: use trace_id)"""


@dataclass
class MemoryKey:
    """Composite key for memory isolation."""
    tenant_id: str
    user_id: str
    session_id: str

    def composite(self) -> str:
        """Return composite key for storage."""
        return f"{self.tenant_id}:{self.user_id}:{self.session_id}"
```

#### Full Configuration

```python
from enum import Enum

class MemoryHealth(Enum):
    """Health states for summarization subsystem."""
    HEALTHY = "healthy"
    """Normal operation, summarization working"""

    RETRY = "retry"
    """Summarization failed, retrying with backoff"""

    DEGRADED = "degraded"
    """Summarization unavailable, using truncation fallback"""

    RECOVERING = "recovering"
    """Attempting to rebuild summary after recovery"""


@dataclass
class ShortTermMemoryConfig:
    """Full configuration for opt-in short-term memory.

    Example usage:
        planner = ReactPlanner(
            llm="claude-sonnet-4-20250514",
            nodes=[...],
            short_term_memory=ShortTermMemoryConfig(
                strategy="rolling_summary",
                budget=MemoryBudget(full_zone_turns=5, total_max_tokens=8000),
                summarizer_model="claude-3-haiku",
            ),
        )
    """
    strategy: Literal["truncation", "rolling_summary", "none"] = "none"
    """
    Memory strategy:
    - none: No memory (stateless, current behavior)
    - truncation: Keep last N turns, discard older
    - rolling_summary: Keep recent turns complete, summarize older
    """

    budget: MemoryBudget = field(default_factory=MemoryBudget)
    """Token budget configuration"""

    isolation: MemoryIsolation = field(default_factory=MemoryIsolation)
    """Session isolation configuration"""

    summarizer_model: str | None = None
    """
    Model for generating summaries. If None, uses planner's model.
    Recommend using a fast, cheap model (e.g., claude-3-haiku, gpt-4o-mini).
    """

    include_trajectory_digest: bool = True
    """Whether to include tool usage in memory context"""

    recovery_backlog_limit: int = 20
    """Maximum unsummarized turns to buffer in DEGRADED state"""

    retry_attempts: int = 3
    """Summarization retries before entering DEGRADED state"""

    token_estimator: Callable[[str], int] | None = None
    """
    Custom token estimator. If None, uses default heuristic:
    len(text) // 4 + 1 (conservative for English text)
    """
```

---

### 2. ShortTermMemory Protocol

```python
from typing import Protocol, Any
from penguiflow.state import StateStore

class ShortTermMemory(Protocol):
    """Protocol for pluggable short-term memory implementations.

    This protocol enables custom memory backends while providing
    a default implementation that covers common use cases.

    All methods are async to support distributed storage backends.
    """

    @property
    def health(self) -> MemoryHealth:
        """Current health state of the memory subsystem."""
        ...

    async def add_turn(self, turn: ConversationTurn) -> None:
        """Add a completed turn to memory.

        This method MUST be non-blocking. Background summarization
        is triggered as needed but does not block the caller.

        Args:
            turn: The completed conversation turn
        """
        ...

    async def get_context(self) -> str:
        """Get memory context for LLM injection.

        Returns prose suitable for system prompt or message prefix.
        Format uses optional XML-style tags for structure:

            <session_summary>
            Summary of older conversation...

            <key_facts>
            - User prefers dark mode
            - Budget is $50/month
            </key_facts>
            </session_summary>

            User: Recent message 1
            Assistant: Recent response 1

            User: Recent message 2
            Assistant: Recent response 2

        Returns:
            Memory context as prose, or empty string if strategy="none"
        """
        ...

    def estimate_tokens(self) -> int:
        """Estimate current memory token usage.

        Uses configured token_estimator or default heuristic.
        """
        ...

    async def flush(self) -> None:
        """Force pending summarization to complete.

        Blocks until all pending turns are summarized.
        Use sparingly, primarily for graceful shutdown.
        """
        ...

    async def persist(self, store: StateStore, key: str) -> None:
        """Save memory state to StateStore.

        Args:
            store: StateStore implementation
            key: Composite key from MemoryKey.composite()
        """
        ...

    async def hydrate(self, store: StateStore, key: str) -> None:
        """Load memory state from StateStore.

        Args:
            store: StateStore implementation
            key: Composite key from MemoryKey.composite()
        """
        ...

    def get_artifact(self, ref: str) -> Any | None:
        """Retrieve a hidden artifact by reference.

        Args:
            ref: Artifact reference from artifacts_hidden_refs

        Returns:
            The artifact value, or None if not found
        """
        ...
```

---

### 3. Memory Strategies

#### Strategy: None (Default - Current Behavior)

```
┌─────────────────────────────────────────────────────────┐
│  strategy="none"                                        │
│                                                         │
│  Each run() call is stateless.                         │
│  get_context() returns empty string.                   │
│  No memory overhead.                                    │
└─────────────────────────────────────────────────────────┘
```

**Use case:** Single-shot interactions, testing, external memory management.

#### Strategy: Truncation (Naive)

```
┌─────────────────────────────────────────────────────────┐
│  strategy="truncation"                                  │
│  budget.full_zone_turns=5                               │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Turn 6  │ Turn 7  │ Turn 8  │ Turn 9  │ Turn 10 │   │
│  │ (full)  │ (full)  │ (full)  │ (full)  │ (full)  │   │
│  └─────────────────────────────────────────────────┘   │
│  │        Turns 1-5 discarded (information loss)    │   │
└─────────────────────────────────────────────────────────┘
```

**Characteristics:**
- Zero latency overhead (no summarization)
- No external model calls
- Abrupt information loss at window boundary
- Deterministic behavior

**Use case:** Cost-sensitive deployments, short conversations, testing.

#### Strategy: Rolling Summary (Recommended)

```
┌─────────────────────────────────────────────────────────────────────┐
│  strategy="rolling_summary"                                         │
│  budget.full_zone_turns=5                                           │
│  budget.summary_max_tokens=1000                                     │
│                                                                     │
│  ┌───────────────────────────┬─────────────────────────────────┐   │
│  │     SUMMARY ZONE          │        FULL ZONE                │   │
│  │   (max 1000 tokens)       │    (last 5 turns complete)      │   │
│  ├───────────────────────────┼─────────────────────────────────┤   │
│  │ <session_summary>         │ Turn 6: user + assistant        │   │
│  │ User discussed pricing,   │ Turn 7: user + assistant        │   │
│  │ selected Pro tier...      │ Turn 8: user + assistant        │   │
│  │                           │ Turn 9: user + assistant        │   │
│  │ <key_facts>               │ Turn 10: user + assistant       │   │
│  │ - Budget: $50/month       │                                 │   │
│  │ - Plan: Pro tier          │                                 │   │
│  │ </key_facts>              │                                 │   │
│  │ </session_summary>        │                                 │   │
│  └───────────────────────────┴─────────────────────────────────┘   │
│                                                                     │
│  Turns 1-5 compressed into summary (information preserved)          │
└─────────────────────────────────────────────────────────────────────┘
```

**Characteristics:**
- Preserves context from entire conversation
- Requires summarizer model (can be cheap/fast)
- Background summarization (no latency impact)
- Graceful degradation on failure

**Use case:** Multi-turn conversations, complex workflows, production deployments.

---

### 4. Background Summarization (Watermark Pattern)

To avoid impacting agent response latency, summarization runs in the background using a watermark pattern:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Watermark Pattern Flow                          │
└─────────────────────────────────────────────────────────────────────┘

Initial state (5 turns in full zone):
┌─────────────────────────────────────────────────────────────────────┐
│ summary=""  │ [Turn1] [Turn2] [Turn3] [Turn4] [Turn5]               │
└─────────────────────────────────────────────────────────────────────┘

Turn 6 arrives:
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Move Turn1 to pending_summarization buffer                       │
│ 2. Add Turn6 to full zone (non-blocking, immediate return)          │
│ 3. Trigger background summarization task                            │
└─────────────────────────────────────────────────────────────────────┘

After add_turn() returns (immediate):
┌─────────────────────────────────────────────────────────────────────┐
│ pending=[Turn1]  │ [Turn2] [Turn3] [Turn4] [Turn5] [Turn6]          │
└─────────────────────────────────────────────────────────────────────┘

Background task completes (async):
┌─────────────────────────────────────────────────────────────────────┐
│ summary="Turn1 summary..."  │ [Turn2] [Turn3] [Turn4] [Turn5] [Turn6]│
│ pending=[]                                                          │
└─────────────────────────────────────────────────────────────────────┘
```

**Guarantees:**
- `add_turn()` never blocks waiting for summarization
- `get_context()` always returns consistent state (last good summary + full zone)
- Background task can batch multiple pending turns before summarizing

---

### 5. Summarization Failure Handling

Summarization failures MUST NOT block agent responses. The system uses graceful degradation:

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Summarization State Machine                        │
└─────────────────────────────────────────────────────────────────────┘

                    ┌──────────────┐
                    │   HEALTHY    │
                    │  (rolling    │
                    │   summary)   │
                    └──────┬───────┘
                           │ summarization fails
                           ▼
                    ┌──────────────┐
            ┌──────►│   RETRY      │◄─────┐
            │       │  (attempt    │      │
            │       │   1,2,3)     │      │ retry fails (< max_retries)
            │       └──────┬───────┘      │
            │              │              │
            │   success    │ max retries  │
            │              │ exceeded     │
            │              ▼              │
            │       ┌──────────────┐      │
            │       │  DEGRADED    │──────┘
            │       │  (naive      │
            │       │  truncation) │
            │       └──────┬───────┘
            │              │
            │   recovery   │ background keeps trying
            │   succeeds   │ (30s interval)
            │              ▼
            └───────┬──────────────┐
                    │  RECOVERING  │
                    │  (rebuild    │
                    │   summary)   │
                    └──────────────┘
```

**Behavior by State:**

| State | LLM Receives | Background Action | Console Output |
|-------|--------------|-------------------|----------------|
| HEALTHY | Summary + recent turns | Normal summarization | (none) |
| RETRY | Last good summary + recent turns | Retry with exponential backoff (2s, 4s, 8s) | `WARNING: Summarization failed, retrying (attempt N)...` |
| DEGRADED | Truncated recent turns only | Keep trying every 30s | `WARNING: Summarization unavailable, using truncation fallback` |
| RECOVERING | Rebuilding summary from backlog | Process pending buffer | `INFO: Summarization recovered` |

**Backlog Management in DEGRADED State:**
- Unsummarized turns accumulate in buffer (max: `recovery_backlog_limit`, default 20)
- When buffer exceeds limit, oldest entries are dropped
- On recovery, entire backlog is batch-summarized

---

### 6. Summary Format (Prose with Optional Tags)

The summary format uses prose for broad LLM compatibility, with optional XML-style tags for structure:

```xml
<session_summary>
The user is setting up a pricing calculator for their e-commerce platform.
They initially asked about pricing tiers and compared three options before
selecting the Pro tier ($49/month) with annual billing discount.

<key_facts>
- User's business: online clothing store (Shopify-based)
- Selected plan: Pro tier with annual billing
- Budget constraint: under $50/month
- Integration requirement: Shopify API
- Email: provided for account setup
</key_facts>

<tools_used>
- search_pricing: Retrieved 3 pricing tier options
- calculate_discount: Applied 10% annual discount ($529/year)
- check_compatibility: Confirmed Shopify integration available
</tools_used>

<pending>
- User needs to provide Shopify API key for integration setup
- Payment method not yet configured
</pending>
</session_summary>
```

**Design Rationale:**
- **Prose over JSON**: Cheaper LLMs (Haiku, GPT-4o-mini) struggle with consistent JSON output during summarization
- **Optional tags**: Tags are hints, not strict schema - summarizer can omit them
- **Human-readable**: Facilitates debugging and log inspection
- **Consistent with codebase**: Matches existing system prompt patterns (e.g., `<tool_catalog>`, `<planning_hints>`)

---

### 7. ReactPlanner Integration

#### Constructor Extension

```python
class ReactPlanner:
    def __init__(
        self,
        # ... existing parameters ...
        llm: str | Mapping[str, Any] | None = None,
        nodes: Sequence[Node] | None = None,
        catalog: Sequence[NodeSpec] | None = None,
        max_iters: int = 8,
        token_budget: int | None = None,
        state_store: Any | None = None,
        summarizer_llm: str | Mapping[str, Any] | None = None,
        # ... other existing parameters ...

        # NEW: Short-term memory (opt-in)
        short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None = None,
    ):
        # ... existing initialization ...

        # Initialize short-term memory
        if isinstance(short_term_memory, ShortTermMemoryConfig):
            self._memory = DefaultShortTermMemory(
                config=short_term_memory,
                summarizer_client=self._build_summarizer_client(short_term_memory),
            )
        else:
            self._memory = short_term_memory  # None or custom implementation
```

#### Run Method Integration

```python
async def run(
    self,
    llm_context: Mapping[str, Any],
    tool_context: Mapping[str, Any] | None = None,
    *,
    # NEW: Optional explicit memory key
    memory_key: MemoryKey | None = None,
) -> PlannerFinish:

    # 1. Build memory key from context if not provided
    if self._memory and not memory_key:
        memory_key = self._extract_memory_key(llm_context, tool_context)

    # 2. Hydrate memory from StateStore (if resuming session)
    if self._memory and self._state_store and memory_key:
        await self._memory.hydrate(
            self._state_store,
            memory_key.composite()
        )

    # 3. Get memory context for LLM
    memory_context = ""
    if self._memory:
        memory_context = await self._memory.get_context()

    # 4. Build effective system prompt with memory
    effective_system_prompt = self._build_system_prompt_with_memory(
        base_prompt=self._system_prompt,
        memory_context=memory_context,
        planning_hints=self._planning_hints,
    )

    # ... existing planning loop ...

    # 5. After completion, add turn to memory
    if self._memory:
        turn = self._build_conversation_turn(
            user_query=llm_context.get("query", ""),
            final_response=result.payload.raw_answer,
            trajectory=trajectory,
        )
        await self._memory.add_turn(turn)

        # 6. Persist if StateStore configured
        if self._state_store and memory_key:
            await self._memory.persist(
                self._state_store,
                memory_key.composite()
            )

    return result
```

#### Memory Context Injection

```python
def _build_system_prompt_with_memory(
    self,
    base_prompt: str,
    memory_context: str,
    planning_hints: Mapping[str, Any] | None,
) -> str:
    """Build system prompt with memory context injected."""

    parts = [base_prompt]

    if memory_context:
        parts.append(f"""
<conversation_memory>
The following is a summary of your conversation history with this user.
Use this context to maintain continuity, avoid repeating questions,
and reference previous decisions when relevant.

{memory_context}
</conversation_memory>
""")

    if planning_hints:
        parts.append(self._format_hints(planning_hints))

    return "\n\n".join(parts)
```

#### Turn Building from Trajectory

```python
def _build_conversation_turn(
    self,
    user_query: str,
    final_response: str,
    trajectory: Trajectory
) -> ConversationTurn:
    """Build ConversationTurn from completed trajectory."""

    # Collect tool observations (all successful)
    observations = []
    tools = []
    artifact_refs = []

    for step in trajectory.steps:
        if step.observation and not step.error:
            tools.append(step.tool_name)

            # Prose summary of observation (truncated if huge)
            obs_summary = self._summarize_observation(
                step.tool_name,
                step.observation
            )
            observations.append(f"- {step.tool_name}: {obs_summary}")

            # Collect artifact references
            if step.artifact_refs:
                artifact_refs.extend(step.artifact_refs)

    # Build trajectory digest
    digest = None
    if self._config.include_trajectory_digest and tools:
        digest = TrajectoryDigest(
            tools_invoked=tools,
            observations_summary="\n".join(observations) if observations else "",
            reasoning_summary=trajectory.last_thought,
            artifacts_refs=artifact_refs,
        )

    return ConversationTurn(
        user_message=user_query,
        assistant_response=final_response,
        trajectory_digest=digest,
        artifacts_shown=trajectory.artifacts_shown or {},
        artifacts_hidden_refs=artifact_refs,
        ts=time.time(),
    )
```

---

### 8. StateStore Extension

To support memory persistence, StateStore implementations should provide these optional methods (duck-typed, checked via `hasattr`):

```python
class StateStore(Protocol):
    # ... existing methods ...

    # Optional: Short-term memory persistence
    async def save_memory_state(self, key: str, state: dict) -> None:
        """Persist short-term memory state.

        Args:
            key: Composite key (tenant:user:session)
            state: Serialized memory state
        """
        ...

    async def load_memory_state(self, key: str) -> dict | None:
        """Load short-term memory state.

        Args:
            key: Composite key (tenant:user:session)

        Returns:
            Serialized memory state, or None if not found
        """
        ...
```

**Fallback Behavior:**
- If StateStore doesn't implement these methods, memory is in-memory only
- Session resumption requires StateStore support
- Warning logged if persistence attempted without support

---

### 9. Token Estimation

Default heuristic (conservative for English text):

```python
def default_token_estimator(text: str) -> int:
    """Estimate token count using character-based heuristic.

    Uses ~4 characters per token, which is conservative for English.
    Different languages may need custom estimators.
    """
    return len(text) // 4 + 1
```

Developers can provide custom estimators for precision:

```python
import tiktoken

def tiktoken_estimator(text: str) -> int:
    """Precise token count using tiktoken (OpenAI tokenizer)."""
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))

config = ShortTermMemoryConfig(
    strategy="rolling_summary",
    token_estimator=tiktoken_estimator,
)
```

---

### 10. Session Isolation (Security)

**Critical requirement:** In multi-tenant deployments, memory MUST be isolated to prevent context leakage between users/sessions.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Memory Isolation                             │
└─────────────────────────────────────────────────────────────────────┘

Storage key structure:
  ┌────────────────────────────────────────────────────────────────┐
  │  {tenant_id}:{user_id}:{session_id}                            │
  │                                                                │
  │  Example: "acme_corp:user_123:session_abc"                     │
  └────────────────────────────────────────────────────────────────┘

Isolation guarantees:
  1. Memory operations ALWAYS include full composite key
  2. get_context() returns ONLY data for current session
  3. Key mismatch → empty memory (fail-safe), log warning
  4. No cross-tenant, cross-user, or cross-session access
```

**Key Extraction:**

```python
def _extract_memory_key(
    self,
    llm_context: Mapping[str, Any],
    tool_context: Mapping[str, Any] | None,
) -> MemoryKey:
    """Extract memory key from context using configured paths."""

    isolation = self._config.isolation

    # Extract tenant (from Message.headers or context)
    tenant_id = self._extract_value(
        isolation.tenant_key,
        llm_context,
        tool_context
    ) or "default"

    # Extract user
    user_id = self._extract_value(
        isolation.user_key,
        llm_context,
        tool_context
    ) or "anonymous"

    # Extract session (default: trace_id)
    session_id = self._extract_value(
        isolation.session_key,
        llm_context,
        tool_context
    ) or uuid.uuid4().hex

    return MemoryKey(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
    )
```

---

### 11. WM (Working Memory) Relationship

**Current state:** `WM` (defined in `types.py`) is used for controller loops in DAG-based flows, NOT ReactPlanner. They serve different patterns:

| Pattern | Use Case | State Management |
|---------|----------|------------------|
| **WM + Controller Loop** | Programmatic iteration in DAG nodes | `WM.facts`, `WM.hops`, `WM.budget_hops` |
| **ReactPlanner + ShortTermMemory** | LLM-driven agent conversations | `Trajectory`, `ConversationTurn`, summary |

**Design decision:** Keep these systems separate. ReactPlanner gets ShortTermMemory; WM remains for controller loops. A unified abstraction could be considered in future versions if controller loops need conversation memory.

---

## Usage Examples

### Basic Opt-In (Rolling Summary)

```python
from penguiflow.planner import ReactPlanner, ShortTermMemoryConfig, MemoryBudget

planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[search_tool, calculate_tool, answer_tool],
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        budget=MemoryBudget(
            full_zone_turns=5,
            summary_max_tokens=1000,
            total_max_tokens=8000,
        ),
        summarizer_model="claude-3-haiku",  # Cheap and fast
    ),
)

# First turn
result1 = await planner.run(
    llm_context={"query": "What pricing tiers do you offer?", "user_id": "user_123"},
    tool_context={"db": database},
)

# Second turn (memory preserved)
result2 = await planner.run(
    llm_context={"query": "I'll take the Pro tier", "user_id": "user_123"},
    tool_context={"db": database},
)
# LLM receives context about the previous pricing discussion
```

### Simple Truncation (No Summarizer)

```python
planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    short_term_memory=ShortTermMemoryConfig(
        strategy="truncation",
        budget=MemoryBudget(full_zone_turns=3),  # Keep last 3 turns
    ),
)
```

### With StateStore Persistence

```python
from penguiflow.state import PostgresStateStore

state_store = PostgresStateStore(connection_string="...")

planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    state_store=state_store,  # Enables persistence
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        isolation=MemoryIsolation(
            tenant_key="headers.tenant",
            user_key="llm_context.user_id",
            session_key="llm_context.session_id",
        ),
    ),
)

# Memory survives across planner instances (session resumption)
```

### Custom Memory Implementation

```python
class RedisShortTermMemory:
    """Custom implementation using Redis for distributed memory."""

    def __init__(self, redis_client, config: ShortTermMemoryConfig):
        self._redis = redis_client
        self._config = config
        self._health = MemoryHealth.HEALTHY

    @property
    def health(self) -> MemoryHealth:
        return self._health

    async def add_turn(self, turn: ConversationTurn) -> None:
        # Custom Redis-based implementation
        ...

    async def get_context(self) -> str:
        # Custom Redis-based implementation
        ...

    # ... implement remaining protocol methods ...

# Use custom implementation
planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    short_term_memory=RedisShortTermMemory(redis_client, config),
)
```

---

## Implementation Plan

### Phase 1: Core Data Structures
- [ ] Add `ConversationTurn`, `TrajectoryDigest` to `penguiflow/planner/memory.py`
- [ ] Add `MemoryBudget`, `MemoryIsolation`, `MemoryKey` dataclasses
- [ ] Add `ShortTermMemoryConfig` with all configuration options
- [ ] Add `MemoryHealth` enum
- [ ] Add `ShortTermMemory` protocol

### Phase 2: Default Implementation
- [ ] Implement `DefaultShortTermMemory` class
- [ ] Implement truncation strategy
- [ ] Implement rolling summary strategy with watermark pattern
- [ ] Implement background summarization with asyncio.Task
- [ ] Implement graceful degradation state machine
- [ ] Add token estimation (default heuristic)

### Phase 3: ReactPlanner Integration
- [ ] Add `short_term_memory` parameter to `ReactPlanner.__init__`
- [ ] Implement `_extract_memory_key()` for session isolation
- [ ] Implement `_build_conversation_turn()` from trajectory
- [ ] Implement `_build_system_prompt_with_memory()`
- [ ] Integrate memory hydration/persistence in `run()`

### Phase 4: StateStore Extension
- [ ] Document optional `save_memory_state`/`load_memory_state` methods
- [ ] Add fallback behavior for StateStores without support
- [ ] Update existing StateStore implementations (optional)

### Phase 5: Testing
- [ ] Unit tests for truncation strategy
- [ ] Unit tests for rolling summary strategy
- [ ] Unit tests for graceful degradation
- [ ] Unit tests for session isolation
- [ ] Integration tests with ReactPlanner
- [ ] Integration tests with StateStore persistence

### Phase 6: Documentation
- [ ] Update `REACT_PLANNER_INTEGRATION_GUIDE.md`
- [ ] Add short-term memory section to manual
- [ ] Add examples in `examples/` directory
- [ ] Update generated orchestrator templates

---

## Backward Compatibility

**This is a non-breaking, opt-in feature.**

- Default: `short_term_memory=None` (current stateless behavior)
- No changes to existing APIs or behavior
- StateStore extension methods are optional (duck-typed)

---

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Turn granularity | **Turn pair** (user-assistant exchange) |
| Trajectory preservation | **All successful tool outputs** (summarized in prose) |
| Summary format | **Prose with optional XML tags** (better LLM compatibility) |
| Token estimation | **Simple heuristic** (len/4+1), custom override available |
| Turn completion signal | **StreamChunk.done == True** (matches playground UI) |
| WM integration | **Separate systems** (different patterns) |
| Recovery backlog limit | **20 turns default** |
| Cross-session memory | **Out of scope** (short-term only) |

---

## Future Considerations (Out of Scope)

1. **Long-term memory**: Cross-session recall, user profiles, learned preferences
2. **Semantic retrieval**: Vector-based memory search for relevant past context
3. **Memory sharing**: Cross-agent memory for multi-agent systems
4. **Memory analytics**: Dashboards for memory usage, summarization quality
5. **WM unification**: Shared abstraction for controller loops and ReactPlanner

---

## References

- [RFC: Structured Planner Output](./RFC_STRUCTURED_PLANNER_OUTPUT.md) - Artifact handling patterns
- [StateStore Guide](../tools/statestore-guide.md) - Persistence patterns
- [LangGraph Short-term Memory](https://langchain-ai.github.io/langgraph/concepts/memory/) - Inspiration
- [React Planner Integration Guide](../REACT_PLANNER_INTEGRATION_GUIDE.md) - Current architecture
