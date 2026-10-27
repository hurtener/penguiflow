"""Typed message and controller models for PenguiFlow."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class Headers(BaseModel):
    """Routing and provenance envelope attached to every ``Message``.

    ``Headers`` travels alongside a ``Message`` payload and is preserved as messages move
    between nodes, fan out, and fan back in. Nodes and middlewares can read it to make
    tenant-aware or priority-aware decisions without inspecting the payload itself.

    Attributes:
        tenant: Identifier of the tenant/customer the message belongs to. Used, for
            example, by remote/A2A call sites to resolve which tenant a downstream
            request should be attributed to.
        topic: Optional free-form routing topic (e.g. a queue or subject name) that
            application code can use to steer messages without changing payload types.
        priority: Optional priority hint for consumers that implement priority-aware
            scheduling. Higher values are conventionally treated as higher priority;
            the core runtime does not itself reorder work based on this field.
    """

    tenant: str
    topic: str | None = None
    priority: int = 0


class Message(BaseModel):
    """Envelope wrapping a payload as it flows through a PenguiFlow graph.

    Every value passed between nodes is carried inside a ``Message`` (or is a bare
    payload wrapped in one by the runtime). It couples the actual data (``payload``)
    with tracing, deadline, and metadata fields that the runtime and middlewares use to
    observe, budget, and correlate work across an entire run.

    Attributes:
        payload: The actual data being routed to a node. Its type is validated
            per-node against the ``ModelRegistry`` when validation is enabled.
        headers: Routing/provenance envelope (tenant, topic, priority) associated with
            this message.
        trace_id: Identifier shared by every message that belongs to the same logical
            run. Generated automatically per top-level message; propagated unchanged to
            child messages (streams, subflows, controller hops) so all related work can
            be correlated in logs, metrics, and cancellation.
        ts: Unix timestamp (seconds) recording when this message instance was created.
        deadline_s: Optional absolute wall-clock deadline (Unix timestamp, seconds).
            When set, the runtime treats messages observed after this time as expired:
            pending work for the trace is skipped and controller loops emit a
            deadline-exceeded ``FinalAnswer`` instead of continuing. ``None`` means no
            deadline is enforced.
        meta: Free-form metadata dictionary (e.g. debug info, cost tracking, custom
            context) that is copied forward to derived messages (streamed chunks,
            controller hops) so it survives fan-out/fan-in and subflow boundaries.
    """

    payload: Any
    headers: Headers
    trace_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description=(
            "Identifier shared by every message belonging to the same logical run; "
            "used to correlate work across nodes, streams, and subflows."
        ),
    )
    ts: float = Field(
        default_factory=time.time,
        description="Unix timestamp (seconds) when this message instance was created.",
    )
    deadline_s: float | None = Field(
        default=None,
        description=(
            "Optional absolute wall-clock deadline (Unix timestamp, seconds) after "
            "which the runtime treats this trace's work as expired."
        ),
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form metadata (debug/cost/context) carried forward to derived "
            "messages across fan-out/fan-in and subflow boundaries."
        ),
    )


class StreamChunk(BaseModel):
    """A single piece of incrementally streamed output (e.g. LLM tokens).

    Nodes that stream partial results (via ``Context.emit_chunk``) produce a sequence of
    ``StreamChunk`` payloads sharing the same ``stream_id``. Consumers reassemble the
    full output by ordering chunks on ``seq`` and stopping once a chunk with
    ``done=True`` is received.

    Attributes:
        stream_id: Identifier grouping all chunks belonging to the same stream.
            Typically defaults to the originating message's ``trace_id``.
        seq: Monotonically increasing sequence number within ``stream_id``, starting at
            0, used to preserve/verify chunk order at the consumer.
        text: The partial text content carried by this chunk.
        done: Whether this is the final chunk of the stream. When ``True``, no further
            chunks are expected for this ``stream_id``.
        meta: Free-form metadata attached to this specific chunk (distinct from the
            wrapping ``Message.meta``).
    """

    stream_id: str = Field(description="Identifier grouping all chunks belonging to the same stream.")
    seq: int = Field(description="Monotonically increasing sequence number within stream_id, starting at 0.")
    text: str = Field(description="The partial text content carried by this chunk.")
    done: bool = Field(default=False, description="Whether this is the final chunk of the stream.")
    meta: dict[str, Any] = Field(
        default_factory=dict, description="Free-form metadata attached to this specific chunk."
    )


class PlanStep(BaseModel):
    """A single unit of work chosen by a controller/planner for one hop.

    A ``Thought`` bundles one or more ``PlanStep`` instances describing what a
    controller loop wants to execute next (e.g. run a retrieval tool, issue a web
    search, run SQL, summarize, route, or stop).

    Attributes:
        kind: The category of action this step performs. ``"stop"`` signals the
            controller loop should terminate rather than dispatch more work.
        args: Arguments for the step, interpreted by whatever executes it (e.g. a tool
            or node); shape is application-defined.
        max_concurrency: Maximum number of concurrent executions allowed for this step
            when it fans out to multiple parallel invocations.
    """

    kind: Literal["retrieve", "web", "sql", "summarize", "route", "stop"] = Field(
        description=(
            "Category of action this step performs; 'stop' signals the controller "
            "loop should terminate."
        )
    )
    args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the step, interpreted by whatever executes it."
    )
    max_concurrency: int = Field(
        default=1, description="Maximum number of concurrent executions allowed for this step."
    )


class Thought(BaseModel):
    """A planner's decision for the current controller hop.

    Produced by a planning/reasoning node (e.g. an LLM-backed controller) to describe
    what should happen next: which ``PlanStep`` instances to run, why, and whether the
    loop is finished.

    Attributes:
        steps: Ordered list of ``PlanStep`` instances to execute for this hop. An empty
            list combined with ``done=True`` typically means no further work is needed.
        rationale: Human-readable explanation of why these steps (or none) were chosen;
            useful for logging/debugging planner behavior.
        done: Whether the controller loop should stop after this hop (e.g. because a
            final answer is ready) rather than continue iterating.
    """

    steps: list[PlanStep] = Field(description="Ordered list of PlanStep instances to execute for this hop.")
    rationale: str = Field(description="Human-readable explanation of why these steps (or none) were chosen.")
    done: bool = Field(default=False, description="Whether the controller loop should stop after this hop.")


class WM(BaseModel):
    """Working memory carried through a controller loop's hops.

    ``WM`` (working memory) is the payload a controller-style flow passes to itself
    across hops (see ``PenguiFlow``'s controller postprocessing). It tracks accumulated
    facts and enforces hop/token budgets and a wall-clock deadline (via the wrapping
    ``Message.deadline_s``): once a budget or the deadline is exceeded, the runtime
    replaces the payload with a ``FinalAnswer`` and routes it to the Rookery instead of
    continuing the loop.

    Attributes:
        query: The original user query or task description driving this run.
        facts: Accumulated evidence/results gathered across hops so far.
        hops: Number of hops completed so far in this controller loop.
        budget_hops: Maximum number of hops allowed before the runtime forces
            termination with a budget-exceeded ``FinalAnswer``. ``None`` disables the
            hop budget check.
        tokens_used: Running count of tokens consumed so far across hops.
        budget_tokens: Maximum tokens allowed before the runtime forces termination
            with a budget-exceeded ``FinalAnswer``. ``None`` disables the token budget
            check.
        confidence: Planner-reported confidence score (application-defined scale,
            typically 0.0-1.0) for the current working state.
    """

    query: str = Field(description="The original user query or task description driving this run.")
    facts: list[Any] = Field(
        default_factory=list, description="Accumulated evidence/results gathered across hops so far."
    )
    hops: int = Field(default=0, description="Number of hops completed so far in this controller loop.")
    budget_hops: int | None = Field(
        default=8,
        description="Maximum number of hops allowed before the runtime forces termination; None disables it.",
    )
    tokens_used: int = Field(default=0, description="Running count of tokens consumed so far across hops.")
    budget_tokens: int | None = Field(
        default=None,
        description="Maximum tokens allowed before the runtime forces termination; None disables it.",
    )
    confidence: float = Field(
        default=0.0, description="Planner-reported confidence score for the current working state."
    )


class FinalAnswer(BaseModel):
    """Terminal payload signaling a controller loop (or flow) has produced its result.

    When a node returns a ``Message`` whose payload is a ``FinalAnswer``, the runtime
    routes it directly to the Rookery instead of continuing to iterate, ending the
    controller loop. It is also the payload the runtime synthesizes itself when a
    deadline or hop/token budget is exceeded (see ``WM``).

    Attributes:
        text: The final answer text to return to the caller.
        citations: Optional list of citation identifiers/sources supporting the answer.
    """

    text: str = Field(description="The final answer text to return to the caller.")
    citations: list[str] = Field(
        default_factory=list, description="Optional list of citation identifiers/sources supporting the answer."
    )


__all__ = [
    "Headers",
    "Message",
    "StreamChunk",
    "PlanStep",
    "Thought",
    "WM",
    "FinalAnswer",
]
