"""A minimal ``StateStore`` for tests to extend with capabilities.

``StateStore`` requires the audit-log methods. ``RecordingStateStore``
implements them in memory, so a test store can subclass it and add only
the optional capability it exercises (memory state, planner state, ...).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from penguiflow.state import StateStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from penguiflow.state import RemoteBinding, StoredEvent


class RecordingStateStore(StateStore):
    """State store that records events and bindings in lists."""

    def __init__(self) -> None:
        """Start with no recorded events or bindings."""
        self.events: list[StoredEvent] = []
        self.bindings: list[RemoteBinding] = []

    @override
    async def save_event(self, event: StoredEvent) -> None:
        self.events.append(event)

    @override
    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        return [event for event in self.events if event.trace_id == trace_id]

    @override
    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        self.bindings.append(binding)
