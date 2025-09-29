"""State store protocol and helpers for PenguiFlow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .metrics import FlowEvent


@dataclass(slots=True)
class StoredEvent:
    """Representation of a runtime event persisted by a state store."""

    trace_id: str | None
    ts: float
    kind: str
    node_name: str | None
    node_id: str | None
    payload: Mapping[str, Any]

    @classmethod
    def from_flow_event(cls, event: FlowEvent) -> StoredEvent:
        """Create a stored representation from a :class:`FlowEvent`."""

        return cls(
            trace_id=event.trace_id,
            ts=event.ts,
            kind=event.event_type,
            node_name=event.node_name,
            node_id=event.node_id,
            payload=event.to_payload(),
        )


@dataclass(slots=True)
class RemoteBinding:
    """Association between a trace and a remote worker/agent."""

    trace_id: str
    context_id: str
    task_id: str
    agent_url: str


class StateStore(Protocol):
    """Protocol for durable state adapters used by PenguiFlow."""

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event.

        Implementations may choose any storage backend (Postgres, Redis, etc.).
        The method must be idempotent since retries can emit duplicate events.
        """

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Return the ordered history for a trace id."""

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist the mapping between a trace and an external worker."""


__all__ = ["StateStore", "StoredEvent", "RemoteBinding"]
