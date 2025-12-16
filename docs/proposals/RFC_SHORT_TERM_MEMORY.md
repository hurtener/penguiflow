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
6. Injects memory via `llm_context` (user prompt), and defaults to fail-closed keying (explicit `memory_key=` or tool_context-only resolution)

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
    """Optional references to hidden artifacts for later retrieval (implementation-defined)."""


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
    """Optional artifacts intentionally exposed to the LLM in future turns (often empty)."""

    artifacts_hidden_refs: list[str] = field(default_factory=list)
    """Optional references to artifacts hidden from LLM but retrievable (often empty)."""

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
    tenant_key: str = "tenant_id"
    """Path to extract tenant ID (default: from tool_context only)."""

    user_key: str = "user_id"
    """Path to extract user ID (default: from tool_context only)."""

    session_key: str = "session_id"
    """Path to extract session ID (default: from tool_context only)."""

    require_explicit_key: bool = True
    """
    Safety default: require an explicit `memory_key=` passed to ReactPlanner.run()/resume(),
    or a key resolvable from tool_context. If a key is not available, memory is treated as
    disabled for that call (fail-closed).

    Rationale:
    - `llm_context` is explicitly LLM-visible and should not contain tenant/user/session identifiers.
    - Deriving isolation keys from LLM-visible content makes cross-tenant leakage more likely.
    """


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

    # ─────────────────────────────────────────────────────────────
    # CALLBACKS (Layer 2: Observation hooks for external systems)
    # ─────────────────────────────────────────────────────────────

    on_turn_added: Callable[[ConversationTurn], Awaitable[None]] | None = None
    """
    Called after a turn is successfully added to memory.
    Use for: syncing to external databases, analytics, audit logs.

    Example:
        async def log_turn(turn: ConversationTurn) -> None:
            await my_analytics.track("conversation_turn", {
                "user_message": turn.user_message[:100],
                "tools_used": turn.trajectory_digest.tools_invoked if turn.trajectory_digest else [],
            })
    """

    on_summary_updated: Callable[[str, str], Awaitable[None]] | None = None
    """
    Called when the rolling summary is regenerated.
    Receives (old_summary, new_summary).
    Use for: debugging, quality monitoring, external sync.

    Example:
        async def track_summary(old: str, new: str) -> None:
            logger.debug(f"Summary updated: {len(old)} -> {len(new)} chars")
    """

    on_health_changed: Callable[[MemoryHealth, MemoryHealth], Awaitable[None]] | None = None
    """
    Called when memory health state transitions.
    Receives (old_health, new_health).
    Use for: alerting, metrics, circuit breaker patterns.

    Example:
        async def alert_degradation(old: MemoryHealth, new: MemoryHealth) -> None:
            if new == MemoryHealth.DEGRADED:
                await pagerduty.alert("Memory summarization degraded")
    """
```

---

### 2. ShortTermMemory Protocol

```python
from collections.abc import Mapping
from typing import Any, Protocol

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

    async def get_llm_context(self) -> Mapping[str, Any]:
        """Return a JSON-serialisable patch injected into ReactPlanner's `llm_context`.

        IMPORTANT: PenguiFlow's baseline mechanism for injecting memories and other
        context is the user prompt built from `llm_context` via `prompts.build_user_prompt`.
        This memory system MUST follow that convention (and must NOT inject untrusted
        content into the system prompt).

        Recommended structure (developer-defined, documented via system_prompt_extra):

            {
              "conversation_memory": {
                "summary": "<session_summary>...</session_summary>",
                "pending_turns": [
                  {"user": "...", "assistant": "..."},
                  ...
                ],
                "recent_turns": [
                  {"user": "...", "assistant": "..."},
                  ...
                ],
              }
            }

        Returns:
            Mapping to merge into llm_context, or empty mapping if strategy="none".
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

class ShortTermMemoryPersistence(Protocol):
    """Optional persistence extension for ShortTermMemory.

    Implementations MAY support persisting/hydrating their internal state via a
    `StateStore`-like object, but this is intentionally duck-typed to avoid changing
    PenguiFlow's core `StateStore` protocol.

    The default implementation will call these if present; otherwise memory is in-memory only.
    """

    async def persist(self, store: Any, key: str) -> None:
        """Persist memory state.

        Args:
            store: A state store object that optionally implements `save_memory_state(key, state)`.
            key: Composite key from MemoryKey.composite()
        """
        ...

    async def hydrate(self, store: Any, key: str) -> None:
        """Hydrate memory state.

        Args:
            store: A state store object that optionally implements `load_memory_state(key)`.
            key: Composite key from MemoryKey.composite()
        """
        ...


class ShortTermMemorySerializable(Protocol):
    """Optional serialization extension for custom persistence backends (Redis, DynamoDB, etc.)."""

    def to_dict(self) -> dict:
        """Serialize full memory state for external storage.

        Returns a dict containing:
        - turns: List of serialized ConversationTurn objects
        - summary: Current summary string (or None)
        - health: Current health state
        - pending: List of turns awaiting summarization
        - artifacts: Optional hidden artifact registry (implementation-defined)
        - config_snapshot: Relevant config for validation on restore

        Use this for custom persistence backends (Redis, DynamoDB, etc.)
        without implementing the full persistence extension.

        Example:
            state = memory.to_dict()
            await redis.set(f"memory:{session_id}", json.dumps(state))
        """
        ...

    def from_dict(self, state: dict) -> None:
        """Restore memory state from serialized dict.

        Validates state structure and restores:
        - All conversation turns
        - Summary zone content
        - Health state
        - Pending summarization buffer

        Args:
            state: Previously serialized state from to_dict()

        Raises:
            ValueError: If state structure is invalid or incompatible

        Example:
            state = json.loads(await redis.get(f"memory:{session_id}"))
            if state:
                memory.from_dict(state)
        """
        ...


class ShortTermMemoryArtifacts(Protocol):
    """Optional artifact access extension.

    This is intentionally separate from the core ShortTermMemory protocol because
    many deployments will not want to persist or expose artifacts via memory.
    """

    def get_artifact(self, ref: str) -> Any | None:
        """Retrieve a hidden artifact by reference."""
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
│  get_llm_context() returns empty mapping.              │
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
│    (but keep it visible to the LLM via a small pending block)       │
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
- `get_llm_context()` never has a "forget gap": it returns last good summary + pending + full zone
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

### 6. Summary Format (Prose Content, JSON-Wrapped)

The summary content is prose for broad LLM compatibility, with optional XML-style tags for structure.

However, **all summarization LLM calls remain JSON-only**: the summarizer returns a small JSON object
whose `summary` field contains the prose (optionally tagged). This preserves PenguiFlow's structured
output/repair DNA while keeping the summary itself flexible and human-readable.

Recommended `conversation_memory.summary` value:

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

Recommended summarizer output shape (JSON-only):

```json
{ "summary": "<session_summary>...</session_summary>" }
```

**Design Rationale:**
- **JSON-wrapped, prose content**: the summarizer only needs to produce `{ "summary": "..." }`, which is easier to validate/repair than fully structured summaries, while the prose stays model-friendly
- **Optional tags**: tags are hints, not strict schema - summarizer can omit them
- **Human-readable**: Facilitates debugging and log inspection
- **Consistent with codebase**: memory is injected as a separate, read-only system message; `system_prompt_extra` may document how to interpret `<read_only_conversation_memory_json>`

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
            self._memory_config = short_term_memory
            # ReactPlanner wires an LLM-backed summarizer for rolling summaries.
            self._memory = DefaultShortTermMemory(
                config=short_term_memory,
                summarizer=self._get_short_term_memory_summarizer(),
            )
        else:
            # If a custom memory instance is provided, the planner keeps conservative defaults
            # for keying behavior (fail-closed unless the caller passes memory_key explicitly).
            self._memory_config = ShortTermMemoryConfig()
            self._memory = short_term_memory  # None or custom implementation
```

#### Run Method Integration

```python
async def run(
    self,
    query: str,
    *,
    llm_context: Mapping[str, Any] | None = None,
    tool_context: Mapping[str, Any] | None = None,
    # NEW: Optional explicit memory key
    memory_key: MemoryKey | None = None,
) -> PlannerFinish | PlannerPause:
    # 0. Normalise inputs first
    normalised_llm_context = _validate_llm_context(llm_context)
    normalised_tool_context = _coerce_tool_context(tool_context)

    resolved_key: MemoryKey | None = None

    # 1. Resolve a safe memory key (explicit param preferred)
    if self._memory is not None:
        resolved_key = memory_key or self._extract_memory_key(tool_context=normalised_tool_context)

        # Fail-closed by default: do NOT maintain or persist memory without an isolation key.
        if resolved_key is None and self._memory_config.isolation.require_explicit_key:
            logger.warning("memory_key_missing_disabling_memory", extra={"query": query[:80]})
        else:
            # 2. Hydrate memory state before building messages (optional)
            if (
                resolved_key is not None
                and self._state_store is not None
                and hasattr(self._memory, "hydrate")
            ):
                await self._memory.hydrate(self._state_store, resolved_key.composite())

            # 3. Inject memory via llm_context (user prompt), NOT via system prompt
            memory_patch = await self._memory.get_llm_context() if resolved_key is not None else {}
            if memory_patch:
                normalised_llm_context = {**normalised_llm_context, **dict(memory_patch)}

    # 4. Proceed with normal planner loop
    trajectory = Trajectory(
        query=query,
        llm_context=normalised_llm_context,
        tool_context=normalised_tool_context,
    )
    result = await self._run_loop(trajectory, tracker=None)

    # 5. Update memory only after a completed turn (PlannerFinish).
    if self._memory is not None and resolved_key is not None and isinstance(result, PlannerFinish):
        turn = self._build_conversation_turn(
            user_message=query,
            assistant_response=result.payload.get("raw_answer", "") if isinstance(result.payload, dict) else str(result),
            trajectory=trajectory,
            finish_thought=result.thought,
        )
        await self._memory.add_turn(turn)

        # 6. Persist if configured and supported
        if self._state_store is not None and hasattr(self._memory, "persist"):
            await self._memory.persist(self._state_store, resolved_key.composite())

    return result
```

#### Memory Key Resolution (Fail-Closed, Tool-Context-Based)

```python
def _extract_memory_key(
    self,
    *,
    tool_context: Mapping[str, Any] | None,
) -> MemoryKey | None:
    """Extract memory key from tool_context only (safe by default).

    Note: `llm_context` is LLM-visible and should not contain tenant/user/session identifiers.
    """
    if tool_context is None:
        return None

    isolation = self._memory_config.isolation

    tenant_id = self._extract_value(isolation.tenant_key, tool_context) or "default"
    user_id = self._extract_value(isolation.user_key, tool_context) or "anonymous"
    session_id = self._extract_value(isolation.session_key, tool_context) or None

    if session_id is None:
        return None

    return MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
```

#### Turn Building from Trajectory

```python
def _build_conversation_turn(
    self,
    user_message: str,
    assistant_response: str,
    trajectory: Trajectory,
    *,
    finish_thought: str | None = None,
) -> ConversationTurn:
    """Build ConversationTurn from completed trajectory."""

    # Collect tool observations (all successful)
    observations = []
    tools = []

    for step in trajectory.steps:
        tool_name = step.action.next_node
        if tool_name and step.observation is not None and not step.error:
            tools.append(tool_name)

            # Prose summary of observation (truncated if huge)
            obs_summary = self._summarize_observation(
                tool_name,
                step.serialise_for_llm(),
            )
            observations.append(f"- {tool_name}: {obs_summary}")

    # Build trajectory digest
    digest = None
    if self._memory_config.include_trajectory_digest and tools:
        digest = TrajectoryDigest(
            tools_invoked=tools,
            observations_summary="\n".join(observations) if observations else "",
            reasoning_summary=finish_thought,
        )

    return ConversationTurn(
        user_message=user_message,
        assistant_response=assistant_response,
        trajectory_digest=digest,
        ts=time.time(),
    )
```

---

### 8. StateStore Extension

To support memory persistence, the configured `state_store` object MAY provide these optional methods
(duck-typed, checked via `hasattr`). This does **not** change PenguiFlow's core `StateStore` protocol;
it is an opt-in capability on the adapter instance.

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
  2. get_llm_context() returns ONLY data for current session
  3. Key mismatch → empty memory (fail-safe), log warning
  4. No cross-tenant, cross-user, or cross-session access
```

**Key Extraction (Safe Defaults):**

By default, callers should pass an explicit `memory_key=` to `ReactPlanner.run()` and `ReactPlanner.resume()`.
If that is not provided, PenguiFlow MAY attempt to resolve a key from `tool_context` only.

Rationale: `llm_context` is LLM-visible and is explicitly documented as not containing internal metadata
like tenant IDs or trace IDs.

```python
def _extract_memory_key(
    self,
    *,
    tool_context: Mapping[str, Any] | None,
) -> MemoryKey | None:
    """Extract memory key from tool_context using configured paths (fail-closed)."""

    if tool_context is None:
        return None

    isolation = self._config.isolation
    tenant_id = self._extract_value(isolation.tenant_key, tool_context) or "default"
    user_id = self._extract_value(isolation.user_key, tool_context) or "anonymous"
    session_id = self._extract_value(isolation.session_key, tool_context)

    if not session_id:
        return None

    return MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=str(session_id))
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

### 12. Memory Access Layers

To support diverse downstream team needs, memory provides multiple access layers:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Memory Access Layers                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Layer 1: StateStore (Primary - established pattern)                │
│  ├─ Use existing StateStore with optional memory methods            │
│  ├─ Zero new concepts for teams already using StateStore            │
│  └─ Automatic persistence/hydration in ReactPlanner.run()           │
│                                                                     │
│  Layer 2: Callbacks (Observation)                                   │
│  ├─ on_turn_added: React when turns are added                       │
│  ├─ on_summary_updated: React when summary changes                  │
│  ├─ on_health_changed: React to degradation/recovery                │
│  └─ Use for: analytics, external sync, alerting                     │
│                                                                     │
│  Layer 3: Serialization (Export/Import)                             │
│  ├─ to_dict(): Export full state to JSON-serializable dict          │
│  ├─ from_dict(): Import state from dict                             │
│  └─ Use for: Redis, DynamoDB, custom databases                      │
│                                                                     │
│  Layer 4: Full Custom Implementation (Escape hatch)                 │
│  ├─ Implement ShortTermMemory protocol entirely                     │
│  └─ Use for: distributed memory, custom eviction, special backends  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Layer Selection Guide:**

| Need | Recommended Layer |
|------|-------------------|
| "Just make it work with my existing StateStore" | Layer 1 |
| "I need to sync turns to my analytics DB" | Layer 2 (on_turn_added) |
| "I want to alert when summarization fails" | Layer 2 (on_health_changed) |
| "I use Redis for session storage" | Layer 3 (to_dict/from_dict) |
| "I need distributed memory across pods" | Layer 4 (custom impl) |
| "I want memory backed by vector DB" | Layer 4 (custom impl) |

**Example: Layer 2 + Layer 3 Combined**

```python
# Use callbacks for real-time sync, serialization for persistence
config = ShortTermMemoryConfig(
    strategy="rolling_summary",

    # Layer 2: Real-time analytics
    on_turn_added=lambda turn: analytics.track_turn(turn),
    on_health_changed=lambda old, new: alert_if_degraded(old, new),
)

planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    short_term_memory=config,
)

# After each conversation session ends:
async def save_session(memory: ShortTermMemory, session_id: str):
    # Layer 3: Custom persistence
    state = memory.to_dict()
    await my_redis.setex(
        f"memory:{session_id}",
        ttl=86400,  # 24 hours
        value=json.dumps(state)
    )
```

---

## Usage Examples

### Basic Opt-In (Rolling Summary)

```python
from penguiflow.planner import ReactPlanner, ShortTermMemoryConfig, MemoryBudget, MemoryKey

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

# Recommended: explicit memory_key (fail-closed isolation)
key = MemoryKey(tenant_id="acme_corp", user_id="user_123", session_id="session_abc")

# First turn
result1 = await planner.run(
    "What pricing tiers do you offer?",
    memory_key=key,
    tool_context={"db": database},
)

# Second turn (memory preserved)
result2 = await planner.run(
    "I'll take the Pro tier",
    memory_key=key,
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
from penguiflow.planner import MemoryKey
from penguiflow.state import PostgresStateStore

state_store = PostgresStateStore(connection_string="...")

planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    state_store=state_store,  # Enables persistence
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        isolation=MemoryIsolation(
            tenant_key="tenant_id",
            user_key="user_id",
            session_key="session_id",
        ),
    ),
)

# Memory survives across planner instances (session resumption), if the configured
# state_store implements the optional `save_memory_state` / `load_memory_state` methods.

key = MemoryKey(tenant_id="acme_corp", user_id="user_123", session_id="session_abc")
result = await planner.run("Resume my previous setup", memory_key=key)
```

### With Callbacks (Layer 2)

```python
async def on_turn(turn: ConversationTurn) -> None:
    """Sync turns to analytics database."""
    await analytics_db.insert({
        "timestamp": turn.ts,
        "user_message": turn.user_message,
        "assistant_response": turn.assistant_response[:500],
        "tools_used": turn.trajectory_digest.tools_invoked if turn.trajectory_digest else [],
    })

async def on_health(old: MemoryHealth, new: MemoryHealth) -> None:
    """Alert on degradation."""
    if new == MemoryHealth.DEGRADED:
        await slack.post("#alerts", "⚠️ Memory summarization degraded!")
    elif old == MemoryHealth.DEGRADED and new == MemoryHealth.HEALTHY:
        await slack.post("#alerts", "✅ Memory summarization recovered")

planner = ReactPlanner(
    llm="claude-sonnet-4-20250514",
    nodes=[...],
    short_term_memory=ShortTermMemoryConfig(
        strategy="rolling_summary",
        on_turn_added=on_turn,
        on_health_changed=on_health,
    ),
)
```

### With Custom Persistence (Layer 3)

```python
import json
from redis.asyncio import Redis
from penguiflow.planner import MemoryKey

redis = Redis.from_url("redis://localhost:6379")

async def handle_conversation(session_id: str, user_message: str):
    planner = ReactPlanner(
        llm="claude-sonnet-4-20250514",
        nodes=[...],
        short_term_memory=ShortTermMemoryConfig(strategy="rolling_summary"),
    )

    # Restore memory from Redis (Layer 3)
    saved_state = await redis.get(f"memory:{session_id}")
    if saved_state:
        planner._memory.from_dict(json.loads(saved_state))

    # Run conversation turn
    key = MemoryKey(tenant_id="default", user_id="anonymous", session_id=session_id)
    result = await planner.run(user_message, memory_key=key)

    # Persist memory to Redis (Layer 3)
    state = planner._memory.to_dict()
    await redis.setex(
        f"memory:{session_id}",
        3600,  # 1 hour TTL
        json.dumps(state),
    )

    return result
```

### Custom Memory Implementation (Layer 4)

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

    async def get_llm_context(self) -> dict[str, Any]:
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
- [x] Add `ConversationTurn`, `TrajectoryDigest` to `penguiflow/planner/memory.py`
- [x] Add `MemoryBudget`, `MemoryIsolation`, `MemoryKey` dataclasses
- [x] Add `ShortTermMemoryConfig` with all configuration options
- [x] Add `MemoryHealth` enum
- [x] Add `ShortTermMemory` protocol (minimal required surface)
- [x] Add optional extension protocols (`ShortTermMemoryPersistence`, `ShortTermMemorySerializable`)

### Phase 2: Default Implementation
- [x] Implement `DefaultShortTermMemory` class
- [x] Implement truncation strategy
- [x] Implement rolling summary strategy with watermark pattern
- [x] Implement background summarization with asyncio.Task
- [x] Implement graceful degradation state machine
- [x] Add token estimation (default heuristic)

### Phase 3: ReactPlanner Integration
- [x] Add `short_term_memory` parameter to `ReactPlanner.__init__`
- [x] Implement `_extract_memory_key()` for session isolation (tool_context-only, fail-closed)
- [x] Implement `_build_conversation_turn()` from trajectory
- [x] Implement memory injection via `llm_context` (user prompt), not system prompt
- [x] Integrate memory hydration/persistence in `run()`

### Phase 4: StateStore Extension
- [x] Document optional `save_memory_state`/`load_memory_state` methods
- [x] Add fallback behavior for StateStores without support
- [x] Update existing StateStore implementations (optional)

### Phase 5: Testing
- [x] Unit tests for truncation strategy
- [x] Unit tests for rolling summary strategy
- [x] Unit tests for graceful degradation
- [x] Unit tests for session isolation
- [x] Integration tests with ReactPlanner
- [x] Integration tests with StateStore persistence

### Phase 6: Documentation (Comprehensive)

**Goal:** Create a clear, complete path for downstream developers to adopt penguiflow memory.

#### 6.1 Core Documentation
- [x] Create `docs/MEMORY_GUIDE.md` - Comprehensive memory usage guide
  - Quick start (5-minute setup)
  - Strategy selection guide (when to use each)
  - Configuration reference (all options explained)
  - Troubleshooting section
- [x] Update `REACT_PLANNER_INTEGRATION_GUIDE.md` with memory section
- [x] Add short-term memory section to `manual.md`
- [x] Update `README.md` with memory feature highlight

#### 6.2 Examples
- [x] `examples/memory_basic/` - Minimal rolling summary setup
- [x] `examples/memory_truncation/` - Cost-effective truncation approach
- [x] `examples/memory_persistence/` - StateStore integration
- [x] `examples/memory_redis/` - Custom Redis persistence with Layer 3
- [x] `examples/memory_callbacks/` - Analytics and alerting with Layer 2
- [x] `examples/memory_custom/` - Full custom implementation (Layer 4)

#### 6.3 Spec Generation
- [x] Add `planner.short_term_memory` to YAML spec schema (`penguiflow/cli/spec.py`)
- [x] Wire `planner.short_term_memory` into generated `planner.py` + `config.py` (`penguiflow generate`)
- [x] Document `planner.short_term_memory` in `penguiflow generate --init` templates

#### 6.4 Migration & Adoption
- [x] Create `docs/migration/MEMORY_ADOPTION.md`
  - Step-by-step migration from stateless to memory-enabled
  - Migration from iceberg memory templates to built-in memory
  - Common pitfalls and solutions
  - Performance tuning guide

#### 6.5 API Reference
- [x] Docstrings for all public classes and methods
- [x] Type hints complete and verified with mypy
- [x] API reference documentation (`docs/api/short-term-memory.md`)

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
| Summary format | **Prose content (optional tags), JSON-wrapped** (`{"summary": "..."}` for summarizer calls) |
| Token estimation | **Simple heuristic** (len/4+1), custom override available |
| Turn completion signal | **PlannerFinish only** (no memory write on PlannerPause) |
| Memory injection location | **Via `llm_context` (user prompt)**, never via system prompt |
| Key sourcing | **Explicit `memory_key=` preferred**, otherwise resolve from `tool_context` only (fail-closed) |
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
