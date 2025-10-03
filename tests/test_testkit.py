from typing import Any

import pytest

from penguiflow import (
    FinalAnswer,
    FlowErrorCode,
    Headers,
    Message,
    Node,
    NodePolicy,
    create,
    testkit,
)


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

