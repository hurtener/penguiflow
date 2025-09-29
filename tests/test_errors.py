from __future__ import annotations

import asyncio

import pytest

from penguiflow import (
    FlowError,
    FlowErrorCode,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
)


def test_flow_error_to_payload_roundtrip() -> None:
    original = ValueError("boom")
    err = FlowError.from_exception(
        trace_id="trace-1",
        node_name="demo",
        node_id="node-123",
        exc=original,
        code=FlowErrorCode.NODE_EXCEPTION,
        metadata={"attempt": 2, "latency_ms": 12.5},
    )

    payload = err.to_payload()

    assert payload["code"] == FlowErrorCode.NODE_EXCEPTION.value
    assert payload["trace_id"] == "trace-1"
    assert payload["node_name"] == "demo"
    assert payload["node_id"] == "node-123"
    assert payload["metadata"]["attempt"] == 2
    assert payload["metadata"]["latency_ms"] == 12.5
    assert err.unwrap() is original
    assert err.exception_type == "ValueError"


@pytest.mark.asyncio
async def test_flow_emits_flow_error_on_final_retry() -> None:
    attempts = 0
    shared_exc = RuntimeError("pop")

    async def failing_node(message: str, _ctx) -> str:
        nonlocal attempts
        attempts += 1
        raise shared_exc

    node = Node(
        failing_node,
        name="failing",
        policy=NodePolicy(
            validate="none",
            max_retries=1,
            backoff_base=0.01,
            backoff_mult=1.0,
        ),
    )
    flow = create(node.to(), emit_errors_to_rookery=True)
    flow.run()

    msg = Message(payload="payload", headers=Headers(tenant="demo"))
    await flow.emit(msg)

    result = await asyncio.wait_for(flow.fetch(), timeout=0.5)

    assert isinstance(result, FlowError)
    assert result.code == FlowErrorCode.NODE_EXCEPTION.value
    assert result.trace_id == msg.trace_id
    assert result.node_name == "failing"
    assert result.unwrap() is shared_exc
    assert result.metadata["attempt"] == 1
    assert attempts == 2

    await flow.stop()


@pytest.mark.asyncio
async def test_flow_error_metadata_for_timeouts() -> None:
    async def sleepy(message: str, _ctx) -> str:
        await asyncio.sleep(0.05)
        return message

    node = Node(
        sleepy,
        name="sleepy",
        policy=NodePolicy(validate="none", timeout_s=0.01, max_retries=0),
    )
    flow = create(node.to(), emit_errors_to_rookery=True)
    flow.run()

    msg = Message(payload="payload", headers=Headers(tenant="demo"))
    await flow.emit(msg)

    result = await asyncio.wait_for(flow.fetch(), timeout=0.5)

    assert isinstance(result, FlowError)
    assert result.code == FlowErrorCode.NODE_TIMEOUT.value
    assert result.trace_id == msg.trace_id
    assert result.node_name == "sleepy"
    assert result.metadata["timeout_s"] == pytest.approx(0.01, rel=1e-6)
    assert result.metadata["attempt"] == 0

    await flow.stop()
