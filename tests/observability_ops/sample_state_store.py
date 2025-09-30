"""In-memory StateStore used by the observability CLI tests."""

from __future__ import annotations

from collections import defaultdict

from penguiflow.state import RemoteBinding, StateStore, StoredEvent

_EVENTS: defaultdict[str, list[StoredEvent]] = defaultdict(list)
_BINDINGS: list[RemoteBinding] = []


class MemoryStateStore(StateStore):
    async def save_event(self, event: StoredEvent) -> None:
        key = event.trace_id or "_"
        _EVENTS[key].append(event)

    async def load_history(self, trace_id: str) -> list[StoredEvent]:
        return list(_EVENTS.get(trace_id, []))

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        _BINDINGS.append(binding)


def create_store() -> MemoryStateStore:
    """Factory referenced by the penguiflow-admin CLI tests."""

    return MemoryStateStore()


def reset_state() -> None:
    """Helper for tests to clear recorded events and bindings."""

    _EVENTS.clear()
    _BINDINGS.clear()
