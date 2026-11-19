"""Message envelope used when forwarding work to remote agents."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class Headers(BaseModel):
    """Routing and provenance envelope attached to every ``Message``.

    Attributes:
        tenant: Identifier of the tenant/customer the message belongs to. Used, for
            example, by remote/A2A call sites to resolve which tenant a downstream
            request should be attributed to.
        topic: Optional free-form routing topic (e.g. a queue or subject name) that
            application code can use to steer messages without changing payload types.
        priority: Optional priority hint for consumers that implement priority-aware
            scheduling. Higher values are conventionally treated as higher priority.
    """

    tenant: str
    topic: str | None = None
    priority: int = 0


class Message(BaseModel):
    """Envelope wrapping a payload forwarded to a remote agent.

    It couples the data (``payload``) with tracing, deadline and metadata fields that
    remote transports use to correlate and budget the call.

    Attributes:
        payload: The data being sent.
        headers: Routing/provenance envelope (tenant, topic, priority).
        trace_id: Identifier correlating all work that belongs to the same logical run.
            Generated automatically when not provided.
        ts: Unix timestamp (seconds) recording when this message instance was created.
        deadline_s: Optional absolute wall-clock deadline (Unix timestamp, seconds).
            ``None`` means no deadline.
        meta: Free-form metadata dictionary (e.g. debug info, cost tracking, context).
    """

    payload: Any
    headers: Headers
    trace_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description=(
            "Identifier shared by every message belonging to the same logical run."
        ),
    )
    ts: float = Field(
        default_factory=time.time,
        description="Unix timestamp (seconds) when this message instance was created.",
    )
    deadline_s: float | None = Field(
        default=None,
        description=(
            "Optional absolute wall-clock deadline (Unix timestamp, seconds)."
        ),
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form metadata (debug/cost/context)."
        ),
    )


__all__ = ["Headers", "Message"]
