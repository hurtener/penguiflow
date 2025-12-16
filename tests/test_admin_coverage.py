"""Tests for penguiflow/admin.py edge cases."""


import pytest

from penguiflow.admin import _resolve_factory, _trim_events, load_state_store, render_events
from penguiflow.state import StoredEvent

# ─── _resolve_factory tests ──────────────────────────────────────────────────


def test_resolve_factory_invalid_format():
    """_resolve_factory should reject invalid spec format."""
    with pytest.raises(ValueError, match="must be in the form"):
        _resolve_factory("invalid_format_no_colon")


def test_resolve_factory_missing_module():
    """_resolve_factory should reject spec with empty module."""
    with pytest.raises(ValueError, match="must be in the form"):
        _resolve_factory(":callable")


def test_resolve_factory_missing_attr():
    """_resolve_factory should reject spec with empty attr."""
    with pytest.raises(ValueError, match="must be in the form"):
        _resolve_factory("module:")


def test_resolve_factory_not_callable():
    """_resolve_factory should reject non-callable attribute."""
    # json.version is a string, not callable
    with pytest.raises(TypeError, match="not a callable"):
        _resolve_factory("json:__version__")


def test_resolve_factory_valid():
    """_resolve_factory should return callable for valid spec."""
    factory = _resolve_factory("json:loads")
    assert callable(factory)


# ─── _trim_events tests ──────────────────────────────────────────────────────


def test_trim_events_none_tail():
    """_trim_events should return all events when tail is None."""
    events = [1, 2, 3, 4, 5]
    result = _trim_events(events, None)
    assert result == [1, 2, 3, 4, 5]


def test_trim_events_zero_tail():
    """_trim_events should return empty list when tail is 0."""
    events = [1, 2, 3, 4, 5]
    result = _trim_events(events, 0)
    assert result == []


def test_trim_events_negative_tail():
    """_trim_events should return empty list when tail is negative."""
    events = [1, 2, 3, 4, 5]
    result = _trim_events(events, -1)
    assert result == []


def test_trim_events_partial_tail():
    """_trim_events should return last N events."""
    events = [1, 2, 3, 4, 5]
    result = _trim_events(events, 2)
    assert result == [4, 5]


def test_trim_events_tail_larger_than_list():
    """_trim_events should return all events when tail > len."""
    events = [1, 2, 3]
    result = _trim_events(events, 10)
    assert result == [1, 2, 3]


# ─── render_events tests ─────────────────────────────────────────────────────


def test_render_events_basic():
    """render_events should serialize StoredEvent to JSON."""
    event = StoredEvent(
        kind="test_event",
        trace_id="trace1",
        node_name="node1",
        node_id="id1",
        ts=1234567890.0,
        payload={"key": "value"},
    )
    result = render_events([event])

    assert len(result) == 1
    assert '"trace_id": "trace1"' in result[0]
    assert '"key": "value"' in result[0]


def test_render_events_with_tail():
    """render_events should respect tail parameter."""
    events = [
        StoredEvent(kind="e1", trace_id="t1", node_name="n1", node_id="i1", ts=1.0, payload={}),
        StoredEvent(kind="e2", trace_id="t2", node_name="n2", node_id="i2", ts=2.0, payload={}),
        StoredEvent(kind="e3", trace_id="t3", node_name="n3", node_id="i3", ts=3.0, payload={}),
    ]
    result = render_events(events, tail=1)

    assert len(result) == 1
    assert '"trace_id": "t3"' in result[0]


# ─── load_state_store tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_state_store_async_factory():
    """load_state_store should await async factory results."""
    from unittest.mock import MagicMock

    # Create a mock state store
    mock_store = MagicMock()
    mock_store.save_event = MagicMock()
    mock_store.load_history = MagicMock()
    mock_store.save_remote_binding = MagicMock()

    # Create a module with async factory
    import types

    async def async_factory():
        return mock_store

    mock_module = types.ModuleType("mock_state_module")
    mock_module.create_store = async_factory

    import sys

    sys.modules["mock_state_module"] = mock_module

    try:
        store = await load_state_store("mock_state_module:create_store")
        assert store is mock_store
    finally:
        del sys.modules["mock_state_module"]


@pytest.mark.asyncio
async def test_load_state_store_sync_factory():
    """load_state_store should work with sync factory."""
    from unittest.mock import MagicMock

    # Create a mock state store
    mock_store = MagicMock()
    mock_store.save_event = MagicMock()
    mock_store.load_history = MagicMock()
    mock_store.save_remote_binding = MagicMock()

    def sync_factory():
        return mock_store

    import types

    mock_module = types.ModuleType("mock_state_sync")
    mock_module.create_store = sync_factory

    import sys

    sys.modules["mock_state_sync"] = mock_module

    try:
        store = await load_state_store("mock_state_sync:create_store")
        assert store is mock_store
    finally:
        del sys.modules["mock_state_sync"]
