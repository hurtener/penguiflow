"""Message bus protocol for distributed PenguiFlow edges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class BusEnvelope:
    """Structured payload published to a :class:`MessageBus`."""

    edge: str
    source: str | None
    target: str | None
    trace_id: str | None
    payload: Any
    headers: Mapping[str, Any] | None
    meta: Mapping[str, Any] | None


class MessageBus(Protocol):
    """Protocol for pluggable message bus adapters."""

    async def publish(self, envelope: BusEnvelope) -> None:
        """Publish an envelope for downstream workers."""


__all__ = ["BusEnvelope", "MessageBus"]
