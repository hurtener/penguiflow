"""Property-based regression tests for high-leverage invariants."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable

from hypothesis import given
from hypothesis import strategies as st

from penguiflow import Headers, Message
from penguiflow.core import create
from penguiflow.node import Node, NodePolicy
from penguiflow.patterns import join_k
from penguiflow.types import StreamChunk


@st.composite
def fanout_patterns(draw: st.DrawFn) -> tuple[int, list[list[int]]]:
    """Randomized arrival orders for join_k fan-in buckets."""

    branch_count = draw(st.integers(min_value=2, max_value=4))
    batches = draw(st.integers(min_value=1, max_value=4))
    orders: list[list[int]] = []
    for _ in range(batches):
        order = draw(st.permutations(range(branch_count)))
        orders.append(list(order))
    return branch_count, orders


async def _run_join_k_handles_randomized_fanout(
    branch_count: int, orders: Iterable[Iterable[int]]
) -> None:
    """Emit batches in arbitrary orders and assert deterministic fan-in."""

    collected: list[list[str]] = []
    expected_batches: list[list[str]] = []

    async def sink(msg: Message, _ctx) -> Message:
        collected.append(list(msg.payload))
        return msg

    join_node = join_k("join", branch_count)
    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))
    flow = create(join_node.to(sink_node), sink_node.to(), queue_maxsize=1)
    flow.run()

    headers = Headers(tenant="prop")

    try:
        for batch_index, order in enumerate(orders):
            trace = f"trace-{batch_index}"
            arrivals: list[str] = []
            for branch_idx in order:
                payload = f"{trace}:{branch_idx}"
                message = Message(payload=payload, headers=headers, trace_id=trace)
                await flow.emit(message)
                arrivals.append(payload)
            aggregated = await asyncio.wait_for(flow.fetch(), timeout=1.0)
            assert isinstance(aggregated, Message)
            assert aggregated.trace_id == trace
            assert aggregated.payload == arrivals
            expected_batches.append(arrivals)
        assert collected == expected_batches
        assert len(collected) == len(orders)
    finally:
        await flow.stop()


@given(fanout_patterns())
def test_join_k_handles_randomized_fanout(
    pattern: tuple[int, list[list[int]]]
) -> None:
    branch_count, orders = pattern
    asyncio.run(_run_join_k_handles_randomized_fanout(branch_count, orders))


@st.composite
def stream_scenarios(draw: st.DrawFn) -> tuple[list[list[str]], list[tuple[int, int]]]:
    """Generate per-stream token lists and an interleaving schedule."""

    stream_count = draw(st.integers(min_value=1, max_value=3))
    streams: list[list[str]] = []
    for _ in range(stream_count):
        tokens = draw(
            st.lists(
                st.text(min_size=1, max_size=8),
                min_size=1,
                max_size=4,
            )
        )
        streams.append(tokens)

    schedule: list[tuple[int, int]] = []
    remaining = [len(tokens) for tokens in streams]
    total_events = sum(remaining)
    for _ in range(total_events):
        available = [idx for idx, count in enumerate(remaining) if count > 0]
        stream_choice = draw(st.sampled_from(available))
        jitter_steps = draw(st.integers(min_value=0, max_value=2))
        schedule.append((stream_choice, jitter_steps))
        remaining[stream_choice] -= 1
    return streams, schedule


async def _run_stream_sequences_are_monotonic(
    streams: list[list[str]], schedule: list[tuple[int, int]]
) -> None:
    """Emit chunks following the schedule and assert per-stream monotonicity."""

    sequences: dict[str, list[int]] = defaultdict(list)
    completions: dict[str, int] = {}

    async def sink(message: Message, _ctx) -> StreamChunk | None:
        chunk = message.payload
        assert isinstance(chunk, StreamChunk)
        sequences[chunk.stream_id].append(chunk.seq)
        if chunk.done:
            completions[chunk.stream_id] = len(sequences[chunk.stream_id])
        return chunk

    sink_node = Node(sink, name="sink", policy=NodePolicy(validate="none"))
    flow = create(sink_node.to())
    flow.run()

    parents = {
        f"stream-{idx}": Message(
            payload=tokens,
            headers=Headers(tenant="prop"),
            trace_id=f"stream-{idx}",
        )
        for idx, tokens in enumerate(streams)
    }

    offsets = [0] * len(streams)

    try:
        for stream_idx, jitter in schedule:
            parent = parents[f"stream-{stream_idx}"]
            token_idx = offsets[stream_idx]
            token = streams[stream_idx][token_idx]
            done = token_idx == len(streams[stream_idx]) - 1
            offsets[stream_idx] += 1
            if jitter:
                await asyncio.sleep(jitter * 0.0005)
            await flow.emit_chunk(
                parent=parent,
                text=token,
                stream_id=parent.trace_id,
                done=done,
            )

        total_events = sum(len(tokens) for tokens in streams)
        for _ in range(total_events):
            await asyncio.wait_for(flow.fetch(), timeout=1.0)

        for stream_idx, tokens in enumerate(streams):
            stream_id = f"stream-{stream_idx}"
            expected = list(range(len(tokens)))
            assert sequences.get(stream_id, []) == expected
            assert completions.get(stream_id) == len(tokens)
    finally:
        await flow.stop()


@given(stream_scenarios())
def test_stream_sequences_are_monotonic(
    scenario: tuple[list[list[str]], list[tuple[int, int]]]
) -> None:
    streams, schedule = scenario
    asyncio.run(_run_stream_sequences_are_monotonic(streams, schedule))
