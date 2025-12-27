import logging
from typing import Any

import pytest

from penguiflow import (
    FinalAnswer,
    FlowError,
    FlowErrorCode,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
    log_flow_events,
    testkit,
)
from penguiflow.testkit import _StubContext


@pytest.mark.asyncio
async def test_run_one_executes_flow_and_records_sequence() -> None:
    async def to_upper(message: Message, _ctx: Any) -> Message:
        upper = message.payload.upper()
        return message.model_copy(update={"payload": upper})

    async def finalize(message: Message, _ctx: Any) -> Message:
        answer = FinalAnswer(text=message.payload)
        return message.model_copy(update={"payload": answer})

    upper = Node(to_upper, name="upper", policy=NodePolicy(validate="none"))
    final = Node(finalize, name="final", policy=NodePolicy(validate="none"))
    flow = create(upper.to(final), final.to())

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    result = await testkit.run_one(flow, message)

    assert isinstance(result, Message)
    assert isinstance(result.payload, FinalAnswer)
    assert result.payload.text == "HELLO"

    testkit.assert_node_sequence(message.trace_id, ["upper", "final"])


@pytest.mark.asyncio
async def test_simulate_error_allows_retry_until_success() -> None:
    async def finalize(message: Message, _ctx: Any) -> Message:
        answer = FinalAnswer(text=message.payload)
        return message.model_copy(update={"payload": answer})

    simulated_worker = testkit.simulate_error(
        "retry",
        FlowErrorCode.NODE_EXCEPTION,
        fail_times=2,
        result_factory=lambda msg: msg.model_copy(
            update={"payload": f"{msg.payload}!"}
        ),
    )

    retry_node = Node(
        simulated_worker,
        name="retry",
        policy=NodePolicy(
            validate="none",
            max_retries=2,
            backoff_base=0.001,
            backoff_mult=1.0,
        ),
    )
    final_node = Node(finalize, name="final", policy=NodePolicy(validate="none"))
    flow = create(retry_node.to(final_node), final_node.to())

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    result = await testkit.run_one(flow, message)

    assert isinstance(result.payload, FinalAnswer)
    assert result.payload.text == "hello!"
    assert simulated_worker.simulation.failures == 2
    assert simulated_worker.simulation.attempts == 3

    testkit.assert_node_sequence(message.trace_id, ["retry", "final"])


def test_assert_node_sequence_without_run() -> None:
    with pytest.raises(AssertionError) as excinfo:
        testkit.assert_node_sequence("missing-trace", ["node"])

    assert "No recorded events" in str(excinfo.value)


def test_simulate_error_validation() -> None:
    with pytest.raises(ValueError):
        testkit.simulate_error("oops", FlowErrorCode.NODE_EXCEPTION, fail_times=0)


@pytest.mark.asyncio
async def test_assert_preserves_message_envelope_accepts_copy() -> None:
    async def annotate(message: Message, _ctx: Any) -> Message:
        return message.model_copy(update={"payload": f"{message.payload}!"})

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    result = await testkit.assert_preserves_message_envelope(annotate, message=message)

    assert isinstance(result, Message)
    assert result.payload == "hello!"
    assert result.headers == message.headers
    assert result.trace_id == message.trace_id


@pytest.mark.asyncio
async def test_assert_preserves_message_envelope_rejects_bare_payload() -> None:
    async def bad_node(message: Message, _ctx: Any) -> str:
        return message.payload

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    with pytest.raises(AssertionError) as excinfo:
        await testkit.assert_preserves_message_envelope(bad_node, message=message)

    assert "must return a Message" in str(excinfo.value)


@pytest.mark.asyncio
async def test_assert_preserves_message_envelope_rejects_header_mutation() -> None:
    async def mutate_headers(message: Message, _ctx: Any) -> Message:
        replacement = Headers(tenant="other")
        return message.model_copy(update={"headers": replacement})

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    with pytest.raises(AssertionError) as excinfo:
        await testkit.assert_preserves_message_envelope(
            mutate_headers, message=message
        )

    assert "headers" in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_one_with_log_flow_events_records_error_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger_name = "tests.testkit.log_flow_events"
    caplog.set_level(logging.INFO, logger=logger_name)

    latency_events: list[tuple[str, float]] = []

    def capture_latency(event_type: str, latency_ms: float, event: Any) -> None:
        latency_events.append((event_type, latency_ms))

    async def always_fails(message: Message, _ctx: Any) -> Message:
        raise ValueError("boom")

    node = Node(
        always_fails,
        name="flaky",
        policy=NodePolicy(validate="none", max_retries=0),
    )
    middleware = log_flow_events(
        logging.getLogger(logger_name), latency_callback=capture_latency
    )
    flow = create(
        node.to(),
        middlewares=[middleware],
        emit_errors_to_rookery=True,
    )

    message = Message(payload="hello", headers=Headers(tenant="acme"))

    result = await testkit.run_one(flow, message)

    assert isinstance(result, FlowError)
    assert result.code == FlowErrorCode.NODE_EXCEPTION.value
    assert "flaky" in (result.node_name or "")

    events = testkit.get_recorded_events(message.trace_id)
    event_types = [event.event_type for event in events]
    assert "node_error" in event_types
    assert "node_failed" in event_types

    failed_event = next(event for event in events if event.event_type == "node_failed")
    payload = failed_event.error_payload
    assert payload is not None
    assert payload["code"] == FlowErrorCode.NODE_EXCEPTION.value
    assert payload["node_name"] == "flaky"
    assert payload.get("exception_type") == "ValueError"

    failure_logs = [
        record
        for record in caplog.records
        if record.name == logger_name and record.getMessage() == "node_error"
    ]
    assert failure_logs, "expected node_error log from log_flow_events"
    assert not any(
        record.getMessage() == "node_success" and record.name == logger_name
        for record in caplog.records
    )

    assert latency_events and latency_events[0][0] == "node_error"
    assert latency_events[0][1] > 0.0


@pytest.mark.asyncio
async def test_stub_context_emit_methods() -> None:
    ctx = _StubContext()
    # These should not raise
    await ctx.emit("test")
    ctx.emit_nowait("test")
    # emit_chunk should raise
    with pytest.raises(RuntimeError, match="does not support emit_chunk"):
        await ctx.emit_chunk("chunk")


@pytest.mark.asyncio
async def test_simulate_error_with_result() -> None:
    simulated = testkit.simulate_error(
        "test_node",
        FlowErrorCode.NODE_EXCEPTION,
        fail_times=1,
        result="fixed_result",
    )
    message = Message(payload="input", headers=Headers(tenant="test"))

    # First call should fail
    with pytest.raises(RuntimeError):
        await simulated(message, None)

    # Second call should succeed with fixed result
    result = await simulated(message, None)
    assert result == "fixed_result"


def test_simulate_error_rejects_both_result_and_factory() -> None:
    with pytest.raises(ValueError, match="only one of"):
        testkit.simulate_error(
            "test",
            FlowErrorCode.NODE_EXCEPTION,
            result="value",
            result_factory=lambda x: x,
        )


@pytest.mark.asyncio
async def test_assert_preserves_envelope_with_node_object() -> None:
    async def simple_node(message: Message, _ctx: Any) -> Message:
        return message.model_copy(update={"payload": "modified"})

    node = Node(simple_node, name="simple", policy=NodePolicy(validate="none"))
    message = Message(payload="test", headers=Headers(tenant="acme"))

    result = await testkit.assert_preserves_message_envelope(node, message=message)
    assert result.payload == "modified"


@pytest.mark.asyncio
async def test_assert_preserves_envelope_rejects_trace_id_mutation() -> None:
    async def mutate_trace(message: Message, _ctx: Any) -> Message:
        return Message(
            payload=message.payload,
            headers=message.headers,
            trace_id="different-trace-id",
        )

    message = Message(payload="test", headers=Headers(tenant="acme"))

    with pytest.raises(AssertionError, match="trace_id"):
        await testkit.assert_preserves_message_envelope(mutate_trace, message=message)


@pytest.mark.asyncio
async def test_assert_preserves_envelope_rejects_sync_function() -> None:
    def sync_node(message: Message, _ctx: Any) -> Message:
        return message

    with pytest.raises(TypeError, match="async node"):
        await testkit.assert_preserves_message_envelope(sync_node)


@pytest.mark.asyncio
async def test_run_one_rejects_non_message() -> None:
    async def noop(message: Message, _ctx: Any) -> Message:
        return message

    node = Node(noop, name="noop", policy=NodePolicy(validate="none"))
    flow = create(node.to())

    with pytest.raises(TypeError, match="Message instance"):
        await testkit.run_one(flow, "not a message")  # type: ignore


@pytest.mark.asyncio
async def test_node_sequence_with_unknown_trace() -> None:
    """Test node_sequence returns empty list for unknown trace (lines 89-98)."""
    from penguiflow.testkit import _RecorderState

    state = _RecorderState()
    # Get sequence for a trace that doesn't exist
    sequence = state.node_sequence("nonexistent-trace")
    assert sequence == []


def _make_flow_event(
    event_type: str,
    trace_id: str | None = None,
    node_name: str | None = None,
    node_id: str | None = None,
) -> Any:
    """Create a FlowEvent with all required fields for testing."""
    import time

    from penguiflow.metrics import FlowEvent

    return FlowEvent(
        event_type=event_type,
        ts=time.time(),
        node_name=node_name,
        node_id=node_id,
        trace_id=trace_id,
        attempt=1,
        latency_ms=0.0,
        queue_depth_in=0,
        queue_depth_out=0,
        outgoing_edges=0,
        queue_maxsize=0,
        trace_pending=0,
        trace_inflight=0,
        trace_cancelled=False,
    )


@pytest.mark.asyncio
async def test_recorder_state_with_new_trace() -> None:
    """Test recorder state handles new trace IDs (lines 83-85)."""
    from penguiflow.testkit import _RecorderState

    state = _RecorderState()
    state.begin()  # Start without pre-registered traces

    # Create an event with a new trace_id not in active_traces
    event = _make_flow_event(
        event_type="node_start",
        trace_id="new-trace-123",
        node_name="test_node",
    )

    # Record the event - this should create a new bucket
    await state.record(event)

    # Verify the trace was recorded
    sequence = state.node_sequence("new-trace-123")
    assert sequence == ["test_node"]


@pytest.mark.asyncio
async def test_node_sequence_with_anonymous_node() -> None:
    """Test node_sequence handles anonymous nodes (line 96)."""
    from penguiflow.testkit import _RecorderState

    state = _RecorderState()
    state.begin(["test-trace"])

    # Create an event without node_name or node_id
    event = _make_flow_event(
        event_type="node_start",
        trace_id="test-trace",
        node_name=None,
        node_id=None,
    )

    await state.record(event)

    sequence = state.node_sequence("test-trace")
    assert sequence == ["<anonymous>"]


@pytest.mark.asyncio
async def test_recorder_state_event_without_trace() -> None:
    """Test recording event without trace_id returns early (line 80)."""
    from penguiflow.testkit import _RecorderState

    state = _RecorderState()
    state.begin()

    # Create an event without trace_id
    event = _make_flow_event(
        event_type="node_start",
        trace_id=None,
        node_name="test_node",
    )

    # Should not raise
    await state.record(event)


@pytest.mark.asyncio
async def test_node_sequence_filters_non_start_events() -> None:
    """Test node_sequence only includes node_start events (line 94-95)."""
    from penguiflow.testkit import _RecorderState

    state = _RecorderState()
    state.begin(["test-trace"])

    # Record various event types
    events = [
        _make_flow_event(event_type="node_start", trace_id="test-trace", node_name="node1"),
        _make_flow_event(event_type="node_complete", trace_id="test-trace", node_name="node1"),
        _make_flow_event(event_type="node_start", trace_id="test-trace", node_name="node2"),
        _make_flow_event(event_type="node_error", trace_id="test-trace", node_name="node2"),
    ]

    for event in events:
        await state.record(event)

    sequence = state.node_sequence("test-trace")
    # Should only include node_start events
    assert sequence == ["node1", "node2"]

