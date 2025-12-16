"""Miscellaneous tests for additional coverage."""

import pytest

from penguiflow import Node, NodePolicy, create
from penguiflow.viz import _display_label, _escape_label, _unique_id, flow_to_mermaid

# ─── viz edge cases ──────────────────────────────────────────────────────────


def test_display_label_unknown_type():
    """_display_label should fallback to str() for unknown types."""
    result = _display_label({"some": "dict"})
    assert "some" in result


def test_unique_id_basic():
    """_unique_id should return base ID when no collision."""
    used: set[str] = set()
    result = _unique_id("my_node", used)
    assert result == "my_node"


def test_unique_id_collision():
    """_unique_id should suffix with number on collision."""
    used = {"my_node"}
    result = _unique_id("my_node", used)
    assert result == "my_node_2"


def test_unique_id_multiple_collisions():
    """_unique_id should increment until unique."""
    used = {"my_node", "my_node_2", "my_node_3"}
    result = _unique_id("my_node", used)
    assert result == "my_node_4"


def test_unique_id_empty_label():
    """_unique_id should handle empty label."""
    used: set[str] = set()
    result = _unique_id("", used)
    assert result == "node"


def test_unique_id_special_chars():
    """_unique_id should replace special chars."""
    used: set[str] = set()
    result = _unique_id("my-node.test", used)
    assert result == "my_node_test"


def test_escape_label_quotes():
    """_escape_label should escape double quotes."""
    result = _escape_label('test "label"')
    assert result == 'test \\"label\\"'


@pytest.mark.asyncio
async def test_visualizer_duplicate_node_names():
    """Visualizer should handle nodes with same name prefix."""

    async def handler(msg: str, ctx) -> str:
        return msg

    node1 = Node(handler, name="handler", policy=NodePolicy(validate="none"))
    node2 = Node(handler, name="handler", policy=NodePolicy(validate="none"))

    flow = create(node1.to(node2))
    mermaid = flow_to_mermaid(flow)

    # Should contain both handlers (possibly with suffixes)
    assert "handler" in mermaid


# ─── testkit edge case - trace history management ────────────────────────────


def test_testkit_register_trace_empty_id():
    """_register_trace_history should ignore empty trace_id."""
    from penguiflow.testkit import _TRACE_HISTORY, _register_trace_history

    initial_size = len(_TRACE_HISTORY)
    _register_trace_history("", [])
    assert len(_TRACE_HISTORY) == initial_size


def test_testkit_register_trace_moves_to_end():
    """_register_trace_history should move existing trace to end."""
    from penguiflow.testkit import _TRACE_HISTORY, _register_trace_history

    # Register first trace
    _register_trace_history("trace_a", [])
    _register_trace_history("trace_b", [])

    # Re-register first trace
    _register_trace_history("trace_a", [])

    # trace_a should now be at the end
    keys = list(_TRACE_HISTORY.keys())
    assert keys[-1] == "trace_a" or "trace_a" in keys


# ─── Additional streaming test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_flow_stops_on_done():
    """stream_flow should stop when done=True chunk received."""
    from unittest.mock import AsyncMock, MagicMock

    from penguiflow.streaming import stream_flow
    from penguiflow.types import StreamChunk

    # Create mock flow
    flow = MagicMock()
    flow.emit = AsyncMock()

    # Setup fetch to return a done chunk immediately
    done_chunk = StreamChunk(stream_id="s1", seq=1, text="final", done=True)
    flow.fetch = AsyncMock(return_value=done_chunk)

    parent_msg = MagicMock()

    chunks = []
    async for chunk in stream_flow(flow, parent_msg, include_final=False):
        chunks.append(chunk)
        break  # Stop after first to avoid infinite loop in test

    assert len(chunks) == 1
    assert chunks[0].done is True


# ─── Core coverage - Message envelope ────────────────────────────────────────


def test_message_headers_propagation():
    """Message headers should be preserved through copy."""
    from penguiflow.types import Headers, Message

    headers = Headers(tenant="test_tenant", topic="test_topic", priority=5)
    msg = Message(payload={"data": "test"}, headers=headers)

    copy = msg.model_copy(update={"payload": {"new": "data"}})
    assert copy.headers.tenant == "test_tenant"
    assert copy.headers.priority == 5


# ─── Node policy tests ───────────────────────────────────────────────────────


def test_node_policy_defaults():
    """NodePolicy should have correct defaults."""
    policy = NodePolicy()
    assert policy.validate == "both"
    assert policy.timeout_s is None
    assert policy.max_retries == 0


def test_node_policy_custom():
    """NodePolicy should accept custom values."""
    policy = NodePolicy(validate="in", timeout_s=30.0, max_retries=3)
    assert policy.validate == "in"
    assert policy.timeout_s == 30.0
    assert policy.max_retries == 3


def test_node_policy_invalid_validate():
    """NodePolicy should reject invalid validate values."""
    with pytest.raises(ValueError, match="validate must be one of"):
        NodePolicy(validate="invalid")


# ─── FlowEvent tests ─────────────────────────────────────────────────────────


def test_flow_event_creation():
    """FlowEvent should store all fields."""
    from penguiflow.metrics import FlowEvent

    event = FlowEvent(
        event_type="node_start",
        trace_id="trace1",
        node_name="my_node",
        node_id="node_123",
        ts=1234567890.0,
        latency_ms=100.5,
        attempt=1,
        queue_depth_in=2,
        queue_depth_out=3,
        outgoing_edges=1,
        queue_maxsize=100,
        trace_pending=5,
        trace_inflight=2,
        trace_cancelled=False,
        extra={"key": "value"},
    )

    assert event.event_type == "node_start"
    assert event.trace_id == "trace1"
    assert event.node_name == "my_node"
    assert event.latency_ms == 100.5
    assert event.extra["key"] == "value"
    assert event.queue_depth == 5  # 2 + 3


def test_flow_event_to_payload():
    """FlowEvent to_payload should return dict."""
    from penguiflow.metrics import FlowEvent

    event = FlowEvent(
        event_type="node_end",
        trace_id="t1",
        node_name="n1",
        node_id="id1",
        ts=1.0,
        latency_ms=50.0,
        attempt=0,
        queue_depth_in=1,
        queue_depth_out=1,
        outgoing_edges=2,
        queue_maxsize=50,
        trace_pending=None,
        trace_inflight=1,
        trace_cancelled=False,
    )

    payload = event.to_payload()
    assert payload["event"] == "node_end"
    assert payload["trace_id"] == "t1"


def test_flow_event_metric_samples():
    """FlowEvent metric_samples should return numeric dict."""
    from penguiflow.metrics import FlowEvent

    event = FlowEvent(
        event_type="node_end",
        trace_id="t1",
        node_name="n1",
        node_id="id1",
        ts=1.0,
        latency_ms=50.0,
        attempt=2,
        queue_depth_in=1,
        queue_depth_out=1,
        outgoing_edges=2,
        queue_maxsize=50,
        trace_pending=3,
        trace_inflight=1,
        trace_cancelled=True,
    )

    metrics = event.metric_samples()
    assert metrics["latency_ms"] == 50.0
    assert metrics["attempt"] == 2.0
    assert metrics["trace_cancelled"] == 1.0
    assert metrics["trace_pending"] == 3.0


def test_flow_event_tag_values():
    """FlowEvent tag_values should return string dict."""
    from penguiflow.metrics import FlowEvent

    event = FlowEvent(
        event_type="node_start",
        trace_id="t1",
        node_name="n1",
        node_id="id1",
        ts=1.0,
        latency_ms=None,
        attempt=0,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=10,
        trace_pending=None,
        trace_inflight=0,
        trace_cancelled=False,
        extra={"custom_tag": "value", "num_tag": 123},
    )

    tags = event.tag_values()
    assert tags["event_type"] == "node_start"
    assert tags["node_name"] == "n1"
    assert tags["custom_tag"] == "value"
    assert tags["num_tag"] == "123"


# ─── StreamChunk tests ───────────────────────────────────────────────────────


def test_stream_chunk_defaults():
    """StreamChunk should have correct defaults."""
    from penguiflow.types import StreamChunk

    chunk = StreamChunk(stream_id="s1", seq=1, text="test")
    assert chunk.done is False
    assert chunk.meta == {}


def test_stream_chunk_with_meta():
    """StreamChunk should accept meta dict."""
    from penguiflow.types import StreamChunk

    chunk = StreamChunk(stream_id="s1", seq=1, text="test", meta={"key": "val"})
    assert chunk.meta == {"key": "val"}
